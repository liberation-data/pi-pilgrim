import inspect
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
        WEAK_SHARED = "weak_shared"
        OBJECT_GRAPH = "object_graph"
        UNSHARED = "unshared"

    def __init__(self) -> None:
        self._bindings: Dict[Any, Callable[[], Any]] = {}
        self._shared: Dict[Hashable, Any] = {}
        self._weak_shared: weakref.WeakValueDictionary[Hashable, Any] = weakref.WeakValueDictionary()
        # Shared, weak_shared and object_graph instances made by the request in progress. Other threads see the
        # shared ones once the request is done, so none of them gets an instance whose configure hasn't run yet.
        self._made: Dict[_Key, Any] = {}
        self._instance_stack: List[_Key] = []
        self._pending_configures: List[Callable[[], None]] = []
        self._request_depth = 0
        self._lock = threading.RLock()
        self._bind_self()
        self.make_bindings()

    # ----- Lifecycles -----

    def shared(self, factory: Callable[[], T], configure: Optional[Callable[[T], None]] = None) -> T:
        """
        One instance for the life of the assembly.
        """
        name = _caller_name(sys._getframe(1))
        instance = self._shared.get(name, _MISSING)
        if instance is not _MISSING:
            return instance  # type: ignore[no-any-return]
        return self._resolve(_SHARED, name, factory, configure)

    def weak_shared(self, factory: Callable[[], T], configure: Optional[Callable[[T], None]] = None) -> T:
        """
        Shared while something else holds it. Once nothing does, the next request makes a new one. The instance
        must support weak references: most classes do, but `int`, `str`, `list` and `dict` don't.
        """
        name = _caller_name(sys._getframe(1))
        instance = self._weak_shared.get(name)
        if instance is not None:
            return instance  # type: ignore[no-any-return]
        return self._resolve(_WEAK_SHARED, name, factory, configure)

    def object_graph(self, factory: Callable[[], T], configure: Optional[Callable[[T], None]] = None) -> T:
        """
        One instance for each request: everything built while resolving one component shares it, and the next
        request makes a new one.
        """
        return self._resolve(_OBJECT_GRAPH, _caller_name(sys._getframe(1)), factory, configure)

    def unshared(self, factory: Callable[[], T], configure: Optional[Callable[[T], None]] = None) -> T:
        """
        A new instance every time it is asked for.
        """
        # Nothing is kept under this name, so the arguments needn't be part of it.
        name = sys._getframe(1).f_code.co_qualname
        with self._lock:
            return self._inject(_UNSHARED, name, factory, configure)

    # ----- Bindings -----

    def make_bindings(self) -> None:
        """
        Override to declare which components `Assembled` can inject, with `make_injectable` and `import_bindings`.
        """

    def make_injectable(
        self, factory: Callable[[], Any], by_type: Any = None, by_key: Optional[Hashable] = None
    ) -> None:
        """
        Lets `Assembled` inject what `factory` returns: by type, when the attribute is annotated with `by_type`, or
        by key, when it names `by_key`. A factory is usually one of the assembly's methods.
        """
        if (by_type is None) == (by_key is None):
            raise PilgrimError("make_injectable takes one of by_type or by_key.")
        self._bindings[_binding_key(by_type) if by_type is not None else by_key] = factory

    def import_bindings(self, *assemblies: "PilgrimAssembly") -> None:
        """
        Makes the bindings of other assemblies injectable through this one. An imported binding replaces one
        declared before it under the same type or key, except for this assembly's own types.
        """
        for assembly in assemblies:
            self._bindings.update(assembly._bindings)
        self._bind_self()

    def factory_for(self, key: Any) -> Optional[Callable[[], Any]]:
        """
        The factory bound to a type or key, or None.
        """
        return self._bindings.get(_binding_key(key))

    def _bind_self(self) -> None:
        # Injectable as each assembly class it is, so a test subclass stands in for the assembly it overrides.
        for it in type(self).__mro__:
            if issubclass(it, PilgrimAssembly) and it is not PilgrimAssembly:
                self._bindings[it] = lambda: self

    # ----- Resolving -----

    def _resolve(
        self,
        lifecycle: "PilgrimAssembly.Lifecycle",
        name: Hashable,
        factory: Callable[[], T],
        configure: Optional[Callable[[T], None]],
    ) -> T:
        """
        Looks again under the lock, since another thread may have published the instance while this one waited.
        """
        with self._lock:
            instance = self._made.get((lifecycle, name), _MISSING)
            if instance is _MISSING:
                instance = self._published(lifecycle, name)
            if instance is _MISSING:
                instance = self._inject(lifecycle, name, factory, configure)
            return typing.cast(T, instance)

    def _published(self, lifecycle: "PilgrimAssembly.Lifecycle", name: Hashable) -> Any:
        if lifecycle is _SHARED:
            return self._shared.get(name, _MISSING)
        if lifecycle is _WEAK_SHARED:
            instance = self._weak_shared.get(name)
            return _MISSING if instance is None else instance
        return _MISSING

    def _inject(
        self,
        lifecycle: "PilgrimAssembly.Lifecycle",
        name: Hashable,
        factory: Callable[[], T],
        configure: Optional[Callable[[T], None]],
    ) -> T:
        """
        Makes one instance. A failure undoes what this call made, so nothing half-built is kept or configured, and
        a request that catches the failure carries on. The outermost call publishes the request's instances.
        """
        key = (lifecycle, name)
        # An unshared method may call itself for a new instance each time, so it isn't checked for a cycle.
        if lifecycle is not _UNSHARED and key in self._instance_stack:
            path = " -> ".join(_describe(it) for it in self._instance_stack + [key])
            raise PilgrimError(f"Circular dependency in initializers: {path}. Break it with configure=.")

        made_mark = len(self._made)
        # Held here, since a configure that runs below swaps the queue for a new one.
        configures = self._pending_configures
        configure_mark = len(configures)
        self._request_depth += 1
        try:
            instance = self._make(key, factory)
            if lifecycle is not _UNSHARED:
                self._made[key] = instance
            if configure is not None:
                configures.append(lambda: configure(instance))
            if isinstance(instance, PilgrimConfigurable):
                configures.append(lambda: instance.configure(self))
            # The graph is built once nothing is being made, though a configure may still be resolving more.
            if not self._instance_stack:
                self._configure_graph()
        except BaseException:
            for it in list(self._made)[made_mark:]:
                del self._made[it]
            del configures[configure_mark:]
            raise
        finally:
            self._request_depth -= 1

        # The request is done once the call that began it returns, configures included.
        if self._request_depth == 0:
            self._publish()
        return instance

    def _make(self, key: "_Key", factory: Callable[[], T]) -> T:
        self._instance_stack.append(key)
        try:
            instance = factory()
        finally:
            self._instance_stack.pop()
        if key[0] is _WEAK_SHARED:
            try:
                weakref.ref(instance)
            except TypeError:
                raise PilgrimError(
                    f"{_describe(key)} made a {type(instance).__name__}, which can't be weakly referenced. "
                    f"Use shared, or wrap it in a class."
                ) from None
        return instance

    def _configure_graph(self) -> None:
        """
        Every instance in the graph has been made, so each may finish its set-up, in the order they were made:
        `configure=` first, then `PilgrimConfigurable.configure`. A configure may resolve more components, which
        queue configures of their own.
        """
        delayed = self._pending_configures
        self._pending_configures = []
        for it in delayed:
            it()

    def _publish(self) -> None:
        """
        The whole request is done: its shared instances are ready for everyone, and its object_graph ones are let go.
        """
        for (lifecycle, name), instance in self._made.items():
            if lifecycle is _SHARED:
                self._shared[name] = instance
            elif lifecycle is _WEAK_SHARED:
                self._weak_shared[name] = instance
        self._made.clear()


_SHARED = PilgrimAssembly.Lifecycle.SHARED
_WEAK_SHARED = PilgrimAssembly.Lifecycle.WEAK_SHARED
_OBJECT_GRAPH = PilgrimAssembly.Lifecycle.OBJECT_GRAPH
_UNSHARED = PilgrimAssembly.Lifecycle.UNSHARED
_Key = Tuple[PilgrimAssembly.Lifecycle, Hashable]

_MISSING: Any = object()
_STAR_ARGUMENTS = inspect.CO_VARARGS | inspect.CO_VARKEYWORDS


def _caller_name(frame: types.FrameType) -> Hashable:
    """
    Names a component by the assembly method that makes it, and that method's arguments, if it has any. Reading the
    caller's frame costs well under a microsecond, where inspect.stack() reads every frame and its source.

    The arguments are read as they are when the lifecycle is called, so the method mustn't rebind them before then.
    """
    code = frame.f_code
    count = code.co_argcount + code.co_kwonlyargcount
    flags = code.co_flags
    if count <= 1 and not flags & _STAR_ARGUMENTS:
        return code.co_qualname
    star_args = bool(flags & inspect.CO_VARARGS)
    values = frame.f_locals
    try:
        arguments = tuple(values[it] for it in code.co_varnames[1:count])
        if star_args:
            arguments += tuple(values[code.co_varnames[count]])
        if flags & inspect.CO_VARKEYWORDS:
            arguments += (tuple(sorted(values[code.co_varnames[count + star_args]].items())),)
    except (KeyError, NameError):
        raise PilgrimError(
            f"{code.co_qualname} deleted one of its arguments, which name its instance. Leave them as they were "
            f"passed until the lifecycle is called."
        ) from None
    try:
        hash(arguments)
    except TypeError:
        raise PilgrimError(
            f"{code.co_qualname} was called with an argument that can't be hashed, so it can't name an instance. "
            f"Use unshared, or pass hashable arguments."
        ) from None
    return code.co_qualname, arguments


def _describe(key: _Key) -> str:
    lifecycle, name = key
    if isinstance(name, tuple):
        name = f"{name[0]}({', '.join(map(repr, name[1]))})"
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
