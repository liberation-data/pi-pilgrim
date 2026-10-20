import threading
import typing
from typing import Any, Dict, Optional, Type, TypeVar

from pipilgrim.errors import PilgrimError
from pipilgrim.pilgrim_assembly import PilgrimAssembly

A = TypeVar("A", bound=PilgrimAssembly)


class AssemblyHolder:
    """
    Holds one instance of each assembly class, so components are shared across the application. Set
    `default_assembly_type` once, at start-up, for `Assembled` attributes that don't name an assembly.
    """

    default_assembly_type: Optional[Type[PilgrimAssembly]] = None
    instances: Dict[Type[PilgrimAssembly], PilgrimAssembly] = {}
    _lock = threading.RLock()

    @classmethod
    def shared(cls, assembly_type: Optional[Type[A]] = None) -> A:
        assembly_type = assembly_type or cls.default_assembly_type
        if assembly_type is None:
            raise PilgrimError(
                "No assembly to inject from. Name one with Assembled(assembly=...), or set "
                "AssemblyHolder.default_assembly_type at start-up."
            )
        instance = cls.instances.get(assembly_type)
        if instance is None:
            with cls._lock:
                instance = cls.instances.get(assembly_type)
                if instance is None:
                    instance = cls.instances[assembly_type] = assembly_type()
        return instance

    @classmethod
    def reset(cls) -> None:
        """
        Forgets every assembly and the default: for tests.
        """
        with cls._lock:
            cls.instances.clear()
            cls.default_assembly_type = None


class Assembled:
    """
    An attribute injected from an assembly, on first access:

        class Tavern:
            knight: Knight = Assembled()                        # by the annotated type
            quest: Quest = Assembled(key="damsel_quest")        # by key
            castle: Castle = Assembled(assembly=QuestAssembly)  # from a named assembly

    The assembly declares what it can inject in `make_bindings`. Each object resolves its attribute once and keeps
    it, so the component's lifecycle decides whether objects share it.
    """

    def __init__(self, key: Any = None, assembly: Optional[Type[PilgrimAssembly]] = None):
        self.key = key
        self.assembly = assembly
        self.name: Optional[str] = None
        self.owner: Optional[type] = None

    def __set_name__(self, owner: type, name: str) -> None:
        self.owner = owner
        self.name = name

    def __get__(self, instance: Any, owner: type = None) -> Any:
        if instance is None:
            return self
        value = self.resolve()
        instance.__dict__[self.name] = value
        return value

    def resolve(self) -> Any:
        key = self.key if self.key is not None else self._annotated_type()
        assembly = AssemblyHolder.shared(self.assembly)
        factory = assembly.factory_for(key)
        if factory is None:
            raise PilgrimError(
                f"{type(assembly).__name__} has no binding for {_describe(key)}, needed by "
                f"{self.owner.__name__}.{self.name}. Declare it in make_bindings with make_injectable."
            )
        return factory()

    def _annotated_type(self) -> Any:
        # Read when first needed, so annotations may name classes defined after this one.
        hints = typing.get_type_hints(self.owner)
        if self.name not in hints:
            raise PilgrimError(
                f"{self.owner.__name__}.{self.name} needs a type annotation or a key to be Assembled."
            )
        return hints[self.name]


def _describe(key: Any) -> str:
    return key.__name__ if isinstance(key, type) else repr(key)
