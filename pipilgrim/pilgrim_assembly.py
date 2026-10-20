import sys
import threading
import types
import typing
import weakref
from enum import Enum
from typing import Any, Callable, Dict, Hashable, List, Optional, Tuple, TypeVar

from pipilgrim.errors import PilgrimError

T = TypeVar("T")


class PilgrimConfigurable:
    """
    An instance that finishes its own set-up once the object graph it belongs to is built. Pilgrim calls
    `configure(assembly)` after every instance in the request has been made, so it may look up others, including
    ones that depend on it.
    """

    def configure(self, assembly: "PilgrimAssembly") -> None:
        pass


class PilgrimAssembly:
    """
    A composition root: each method returns one component, made with a lifecycle (`shared`, `weak_shared`,
    `object_graph` or `unshared`), and asks the assembly's other methods for what it depends on.

    A component is named by the method that makes it. A method's arguments are part of the name, so a `shared`
    component made with different arguments is a different instance.

    Resolving is thread-safe: one thread builds an object graph at a time, and a shared component is made once.
    """

    class Lifecycle(Enum):
        SHARED = "shared"
        WEAK_SHARED = "weakShared"
        OBJECT_GRAPH = "objectGraph"
        UNSHARED = "unshared"

    def __init__(self):
        self.bindings: Dict[Hashable, Callable[[], Any]] = {}
        self.shared_instances: Dict[Hashable, Any] = {}
        self.weak_shared_instances: Dict[Hashable, weakref.ref] = {}
        self.scoped_instances: Dict[Hashable, Any] = {}
        # Shared and weak_shared instances made by the request in progress. Other threads see them once the request
        # is done, so none of them gets an instance whose configure hasn't run yet.
        self._made: Dict[Tuple["PilgrimAssembly.Lifecycle", Hashable], Any] = {}
        self._instance_stack: List[Tuple["PilgrimAssembly.Lifecycle", Hashable]] = []
        self._configure_stack: List[Callable[[], None]] = []
        self._request_depth = 0
        self._lock = threading.RLock()
        self.make_injectable(lambda: self, by_type=type(self))
        self.make_bindings()

    # ----- Lifecycles -----

    def shared(self, factory: Callable[[], T], configure: Optional[Callable[[T], None]] = None) -> T:
        """
        One instance for the life of the assembly.
        """
        name = _caller_name(sys._getframe(1))
        instance = self.shared_instances.get(name, _MISSING)
        if instance is not _MISSING:
            return instance
        with self._lock:
            instance = self.shared_instances.get(name, self._made.get((self.Lifecycle.SHARED, name), _MISSING))
            if instance is not _MISSING:
                return instance
            return self._inject(self.Lifecycle.SHARED, name, factory, configure)

    def weak_shared(self, factory: Callable[[], T], configure: Optional[Callable[[T], None]] = None) -> T:
        """
        Shared while something else holds it. Once nothing does, the next request makes a new one. The instance
        must support weak references: most classes do, but `int`, `str`, `list` and `dict` don't.
        """
        name = _caller_name(sys._getframe(1))
        instance = _alive(self.weak_shared_instances.get(name))
        if instance is not None:
            return instance
        with self._lock:
            instance = self._made.get((self.Lifecycle.WEAK_SHARED, name)) or _alive(self.weak_shared_instances.get(name))
            if instance is not None:
                return instance
            return self._inject(self.Lifecycle.WEAK_SHARED, name, factory, configure)

    def object_graph(self, factory: Callable[[], T], configure: Optional[Callable[[T], None]] = None) -> T:
        """
        One instance for each request: everything built while resolving one component shares it, and the next
        request makes a new one.
        """
        name = _caller_name(sys._getframe(1))
        with self._lock:
            instance = self.scoped_instances.get(name, _MISSING)
            if instance is not _MISSING:
                return instance
            return self._inject(self.Lifecycle.OBJECT_GRAPH, name, factory, configure)

    def unshared(self, factory: Callable[[], T], configure: Optional[Callable[[T], None]] = None) -> T:
        """
        A new instance every time it is asked for.
        """
        # Nothing is kept under this name, so the arguments needn't be part of it.
        name = sys._getframe(1).f_code.co_qualname
        with self._lock:
            return self._inject(self.Lifecycle.UNSHARED, name, factory, configure)

    # ----- Bindings -----

    def make_bindings(self) -> None:
        """
        Override to declare which components `Assembled` can inject, with `make_injectable` and `import_bindings`.
        """

    def make_injectable(self, factory: Callable[[], Any], by_type: Any = None, by_key: Optional[str] = None) -> None:
        """
        Lets `Assembled` inject what `factory` returns: by type, when the attribute is annotated with `by_type`, or
        by key, when it names `by_key`. A factory is usually one of the assembly's methods.
        """
        if (by_type is None) == (by_key is None):
            raise PilgrimError("make_injectable takes one of by_type or by_key.")
        self.bindings[_binding_key(by_type) if by_type is not None else by_key] = factory

    def import_bindings(self, *assemblies: "PilgrimAssembly") -> None:
        """
        Makes the bindings of other assemblies injectable through this one.
        """
        for assembly in assemblies:
            self.bindings.update(assembly.bindings)
        self.make_injectable(lambda: self, by_type=type(self))

    def factory_for(self, key: Any) -> Optional[Callable[[], Any]]:
        return self.bindings.get(_binding_key(key))

    # ----- Resolving -----

    def _inject(self, lifecycle: "PilgrimAssembly.Lifecycle", name: Hashable, factory, configure):
        key = (lifecycle, name)
        if lifecycle is not self.Lifecycle.UNSHARED and key in self._instance_stack:
            path = " -> ".join(_describe(it) for it in self._instance_stack + [key])
            raise PilgrimError(f"Circular dependency in initializers: {path}. Break it with configure=.")

        self._instance_stack.append(key)
        try:
            instance = factory()
            if lifecycle is self.Lifecycle.WEAK_SHARED:
                try:
                    weakref.ref(instance)
                except TypeError:
                    raise PilgrimError(
                        f"{_describe(key)} made a {type(instance).__name__}, which can't be weakly referenced. "
                        f"Use shared, or wrap it in a class."
                    ) from None
        except BaseException:
            self._instance_stack.pop()
            if self._request_depth == 0 and not self._instance_stack:
                self._abandon()
            raise
        self._instance_stack.pop()

        if lifecycle is self.Lifecycle.OBJECT_GRAPH:
            self.scoped_instances[name] = instance
        elif lifecycle is not self.Lifecycle.UNSHARED:
            self._made[key] = instance

        if configure is not None:
            self._configure_stack.append(lambda: configure(instance))
        if isinstance(instance, PilgrimConfigurable):
            self._configure_stack.append(lambda: instance.configure(self))

        if not self._instance_stack:
            # A configure may resolve more components, which start a configure stack of their own.
            delayed = self._configure_stack
            self._configure_stack = []
            self._request_depth += 1
            try:
                for it in delayed:
                    it()
            except BaseException:
                self._request_depth -= 1
                if self._request_depth == 0:
                    self._abandon()
                raise
            self._request_depth -= 1
            if self._request_depth == 0:
                self._publish()
        return instance

    def _publish(self) -> None:
        """
        The whole request is done: its shared instances are ready for everyone, and its object_graph ones are let go.
        """
        for (lifecycle, name), instance in self._made.items():
            if lifecycle is self.Lifecycle.SHARED:
                self.shared_instances[name] = instance
            else:
                self.weak_shared_instances[name] = weakref.ref(instance)
        self._made.clear()
        self.scoped_instances.clear()

    def _abandon(self) -> None:
        """
        The request failed: nothing it made is kept, so the next request starts clean.
        """
        self._instance_stack.clear()
        self._configure_stack.clear()
        self._made.clear()
        self.scoped_instances.clear()


_MISSING = object()


def _caller_name(frame) -> Hashable:
    """
    Names a component by the assembly method that makes it, and that method's arguments, if it has any. Reading the
    caller's frame costs well under a microsecond, where inspect.stack() reads every frame and its source.
    """
    code = frame.f_code
    count = code.co_argcount + code.co_kwonlyargcount
    if count <= 1:
        return code.co_qualname
    values = frame.f_locals
    arguments = tuple(values[it] for it in code.co_varnames[1:count])
    try:
        hash(arguments)
    except TypeError:
        raise PilgrimError(
            f"{code.co_qualname} was called with an argument that can't be hashed, so it can't name an instance. "
            f"Use unshared, or pass hashable arguments."
        ) from None
    return code.co_qualname, arguments


def _alive(reference: Optional[weakref.ref]):
    return reference() if reference is not None else None


def _describe(key) -> str:
    lifecycle, name = key
    if isinstance(name, tuple):
        name = f"{name[0]}{name[1]!r}"
    return f"{lifecycle.value}({name})"


def _binding_key(key: Any) -> Any:
    """
    `Optional[X]` and `X | None` inject as `X`.
    """
    if typing.get_origin(key) in (typing.Union, types.UnionType):
        rest = tuple(it for it in typing.get_args(key) if it is not type(None))
        if len(rest) == 1:
            return rest[0]
    return key
