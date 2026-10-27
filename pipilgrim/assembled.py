import sys
import threading
import typing
import types
from typing import TYPE_CHECKING, Any, ClassVar, Dict, Hashable, List, Optional, Set, Type, TypeVar, overload

from pipilgrim.errors import PilgrimError
from pipilgrim.pilgrim_assembly import PilgrimAssembly, _refuse_async

A = TypeVar("A", bound=PilgrimAssembly)


class AssemblyHolder:
    """
    Holds one instance of each assembly class, so components are shared across the application. Set
    `default_assembly_type` once, at start-up, for `Assembled` attributes that don't name an assembly.

    An assembly that asks for one the holder hasn't made yet owns it, and closes it when it closes. `close()` or
    `await aclose()` closes every held assembly. The holder forgets an assembly once it is closed, so the next
    `shared` makes a new one.
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

        The assembly that asks for it first, from its `__init__` or a method, or a function those call, owns it: closing
        that assembly closes this one. One that something else asks for first, even a component's own `__init__` while
        the assembly makes it, is closed by `AssemblyHolder.close()`.
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
                    instance._on_close.append(lambda: cls._forget(wanted, instance))
                    owner = _nearest_self(sys._getframe(1))
                    if isinstance(owner, PilgrimAssembly) and owner is not instance:
                        owner._owned.append(instance)
        return instance

    @classmethod
    def close(cls) -> None:
        """
        Closes every held assembly, newest first, with its `close()`, and forgets it. One that another held assembly
        owns is closed by its owner, after the owner's components. If any of them has a teardown that is async,
        refuses before closing anything, and says to use `await aclose()`. Errors are raised together, as an
        ExceptionGroup, once every assembly has been closed.
        """
        held = cls._held()
        seen: Set[int] = set()
        _refuse_async("AssemblyHolder", [it for assembly in held for it in assembly._async_teardowns(seen)])
        errors: List[Exception] = []
        for assembly in held:
            try:
                assembly.close()
            except Exception as e:
                errors.append(e)
        cls._raise_errors(errors)

    @classmethod
    async def aclose(cls) -> None:
        """
        Closes every held assembly, newest first, with its `aclose()`, and forgets it. One that another held assembly
        owns is closed by its owner, after the owner's components. Errors are raised together, as an ExceptionGroup,
        once every assembly has been closed.
        """
        errors: List[Exception] = []
        for assembly in cls._held():
            try:
                await assembly.aclose()
            except Exception as e:
                errors.append(e)
        cls._raise_errors(errors)

    @classmethod
    def reset(cls) -> None:
        """
        Forgets every assembly and the default, without closing them: for tests.
        """
        with cls._lock:
            cls._instances.clear()
            cls.default_assembly_type = None

    @classmethod
    def _held(cls) -> List[PilgrimAssembly]:
        # Newest first, leaving out those a held assembly owns, which it closes after its own components, so each
        # is closed before the ones it asked for, even after it was made.
        with cls._lock:
            held = list(cls._instances.values())
        owned = {id(it) for assembly in held for it in assembly._owned}
        return [it for it in reversed(held) if id(it) not in owned]

    @classmethod
    def _forget(cls, assembly_type: Type[PilgrimAssembly], instance: PilgrimAssembly) -> None:
        with cls._lock:
            if cls._instances.get(assembly_type) is instance:
                del cls._instances[assembly_type]

    @staticmethod
    def _raise_errors(errors: List[Exception]) -> None:
        if errors:
            raise ExceptionGroup(
                f"Closing the held assemblies failed: {len(errors)} of them raised", errors
            )


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
        # Set when the class body that assigns it runs.
        self.name = ""
        self.owner: type = object
        self._resolved_key: Any = None

    def __set_name__(self, owner: type, name: str) -> None:
        self.owner = owner
        self.name = name

    def __get__(self, instance: Any, owner: Optional[type] = None) -> Any:
        if instance is None:
            return self
        if not self.name:
            raise PilgrimError(
                f"An Assembled attribute of {type(instance).__name__} was added after the class was made, so it has "
                f"no name. Assign it in the class body."
            )
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
                except (AttributeError, SyntaxError) as e:
                    # Raised as an AttributeError, it would reach the owner's __getattr__, if it has one.
                    written = getattr(annotation, "__forward_arg__", annotation)
                    raise PilgrimError(
                        f"{self.owner.__name__}.{self.name}'s annotation {written!r} can't be resolved: {e}."
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
    alone = type(owner.__name__, (), {"__annotations__": {name: annotation}})
    module = getattr(sys.modules.get(owner.__module__), "__dict__", {})
    # Swapped, as get_type_hints does for a class, so module names win over the class's own, which may be the very
    # attribute being resolved, and nested classes are still found.
    hints = typing.get_type_hints(alone, dict(vars(owner)), module)
    return hints[name]


def _nearest_self(frame: Optional[types.FrameType]) -> Any:
    """
    The `self` of the nearest method on the stack, so an assembly that asks for another, even from a lambda or a
    helper function, is found. Read only when the holder makes an assembly.
    """
    while frame is not None:
        if "self" in frame.f_code.co_varnames or "self" in frame.f_code.co_freevars:
            found = frame.f_locals.get("self", _NOT_FOUND)
            if found is not _NOT_FOUND:
                return found
        frame = frame.f_back
    return None


_NOT_FOUND: Any = object()


def _type_name(key: Any) -> str:
    return key.__name__ if isinstance(key, type) else repr(key)
