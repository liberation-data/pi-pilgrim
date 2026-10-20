import sys
import threading
import typing
from typing import TYPE_CHECKING, Any, ClassVar, Dict, Hashable, Optional, Type, TypeVar, overload

from pipilgrim.errors import PilgrimError
from pipilgrim.pilgrim_assembly import PilgrimAssembly

A = TypeVar("A", bound=PilgrimAssembly)


class AssemblyHolder:
    """
    Holds one instance of each assembly class, so components are shared across the application. Set
    `default_assembly_type` once, at start-up, for `Assembled` attributes that don't name an assembly.
    """

    default_assembly_type: ClassVar[Optional[Type[PilgrimAssembly]]] = None
    _instances: ClassVar[Dict[Type[PilgrimAssembly], PilgrimAssembly]] = {}
    _lock: ClassVar[threading.RLock] = threading.RLock()

    @overload
    @classmethod
    def shared(cls, assembly_type: Type[A]) -> A: ...

    @overload
    @classmethod
    def shared(cls, assembly_type: None = None) -> PilgrimAssembly: ...

    @classmethod
    def shared(cls, assembly_type: Optional[Type[PilgrimAssembly]] = None) -> PilgrimAssembly:
        """
        The application's one instance of an assembly class, or of `default_assembly_type`, made on first use.
        """
        wanted = assembly_type or cls.default_assembly_type
        if wanted is None:
            raise PilgrimError(
                "No assembly to inject from. Name one with Assembled(assembly=...), or set "
                "AssemblyHolder.default_assembly_type at start-up."
            )
        instance = cls._instances.get(wanted)
        if instance is None:
            with cls._lock:
                instance = cls._instances.get(wanted)
                if instance is None:
                    instance = cls._instances[wanted] = wanted()
        return instance

    @classmethod
    def reset(cls) -> None:
        """
        Forgets every assembly and the default: for tests.
        """
        with cls._lock:
            cls._instances.clear()
            cls.default_assembly_type = None


class _Assembled:
    """
    An attribute injected from an assembly, on first access:

        class Tavern:
            knight: Knight = Assembled()                        # by the annotated type
            quest: Quest = Assembled(key="damsel_quest")        # by key
            castle: Castle = Assembled(assembly=QuestAssembly)  # from a named assembly

    The assembly declares what it can inject in `make_bindings`. Each object resolves its attribute once and keeps
    it, so the component's lifecycle decides whether objects share it.
    """

    def __init__(self, key: Optional[Hashable] = None, assembly: Optional[Type[PilgrimAssembly]] = None):
        self.key = key
        self.assembly = assembly
        self.name = "?"
        self.owner: type = object
        self._resolved_key: Any = None

    def __set_name__(self, owner: type, name: str) -> None:
        self.owner = owner
        self.name = name

    def __get__(self, instance: Any, owner: Optional[type] = None) -> Any:
        if instance is None:
            return self
        try:
            attributes = instance.__dict__
        except AttributeError:
            raise PilgrimError(
                f"{self.owner.__name__}.{self.name} can't be Assembled: {self.owner.__name__} has __slots__ and no "
                f"__dict__ to keep it in."
            ) from None
        # If two threads get here at once, both keep the first value stored.
        return attributes.setdefault(self.name, self.resolve())

    def resolve(self) -> Any:
        assembly = AssemblyHolder.shared(self.assembly)
        if self._resolved_key is None:
            self._resolved_key = self.key if self.key is not None else self._annotated_type()
        factory = assembly.factory_for(self._resolved_key)
        if factory is None:
            raise PilgrimError(
                f"{type(assembly).__name__} has no binding for {_type_name(self._resolved_key)}, needed by "
                f"{self.owner.__name__}.{self.name}. Declare it in make_bindings with make_injectable."
            )
        return factory()

    def _annotated_type(self) -> Any:
        # Read when first needed, so annotations may name classes defined after this one.
        for it in self.owner.__mro__:
            annotation = _annotations(it).get(self.name)
            if annotation is not None:
                try:
                    return _binding_type(annotation, it, self.name)
                except NameError as e:
                    raise PilgrimError(
                        f"{self.owner.__name__}.{self.name} is annotated with {e.name}, which can't be found when "
                        f"it is injected. Import it at run time, or give a key."
                    ) from None
        raise PilgrimError(f"{self.owner.__name__}.{self.name} needs a type annotation or a key to be Assembled.")


if TYPE_CHECKING:
    # So `knight: Knight = Assembled()` type-checks, as `dataclasses.field()` does. The class stays outside this
    # block, where type checkers still check it.
    def Assembled(key: Optional[Hashable] = None, assembly: Optional[Type[PilgrimAssembly]] = None) -> Any:
        """
        An attribute injected from an assembly, on first access, by its annotated type or by key.
        """

else:
    Assembled = _Assembled
    Assembled.__name__ = Assembled.__qualname__ = "Assembled"


def _annotations(owner: type) -> Dict[str, Any]:
    """
    The class's own annotations, left unresolved where they can't be resolved, so that one that can't, such as a
    type imported only under TYPE_CHECKING, doesn't stop the others.
    """
    if sys.version_info >= (3, 14):
        import annotationlib

        return annotationlib.get_annotations(owner, format=annotationlib.Format.FORWARDREF)
    return typing.cast(Dict[str, Any], owner.__dict__.get("__annotations__", {}))


def _binding_type(annotation: Any, owner: type, name: str) -> Any:
    """
    Resolves one annotation as `typing.get_type_hints` would, against the module and class it was written in:
    strings and forward references, nested ones included, and `Annotated` metadata dropped.
    """
    alone = type(owner.__name__, (), {"__annotations__": {name: annotation}, "__module__": owner.__module__})
    hints = typing.get_type_hints(alone, vars(sys.modules[owner.__module__]), dict(vars(owner)))
    return hints[name]


def _type_name(key: Any) -> str:
    return key.__name__ if isinstance(key, type) else repr(key)
