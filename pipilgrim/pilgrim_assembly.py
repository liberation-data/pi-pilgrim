import inspect
import sys
import threading
import types
import typing
import weakref
from enum import Enum
from typing import Any, Callable, Dict, Hashable, List, Literal, Optional, Self, Set, Tuple, TypeVar, Union

from pipilgrim.errors import PilgrimError

T = TypeVar("T")


class _Infer(Enum):
    INFER = "infer"


# The default destroy hook: the instance's own aclose(), close() or shutdown(), if it has one.
_INFER = _Infer.INFER
_Destroy = Union[Callable[[T], object], None, Literal[_Infer.INFER]]

# What a component's teardown is, when it has no destroy hook, in the order they're looked for. Spring infers close
# and shutdown too. __exit__ isn't inferred, since calling it without __enter__ releases a lock that isn't held, or
# runs a @contextmanager's generator from the start.
_ASYNC_TEARDOWNS = ("aclose", "close", "shutdown")
_SYNC_TEARDOWNS = ("close", "shutdown")
_PRUNE_FLOOR = 64


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

    Closing the assembly, with `close()`, `await aclose()`, `with` or `async with`, tears down its `shared` and live
    `weak_shared` components, newest first.
    """

    class Lifecycle(Enum):
        SHARED = "shared"
        WEAK_SHARED = "weak_shared"
        OBJECT_GRAPH = "object_graph"
        UNSHARED = "unshared"

    # Set in __new__, before any __init__ runs, since a subclass's __init__ may ask AssemblyHolder for assemblies,
    # which this one then owns, before it calls super().__init__().
    _closed: bool
    _owned: List["PilgrimAssembly"]
    _on_close: List[Callable[[], None]]

    def __new__(cls, *args: Any, **kwargs: Any) -> Self:
        self = super().__new__(cls)
        self._closed = False
        self._owned = []
        self._on_close = []
        return self

    def __init__(self) -> None:
        self._bindings: Dict[Any, Callable[[], Any]] = {}
        self._shared: Dict[Hashable, Any] = {}
        self._weak_shared: weakref.WeakValueDictionary[Hashable, Any] = weakref.WeakValueDictionary()
        # Shared, weak_shared and object_graph instances made by the request in progress. Other threads see the
        # shared and weak_shared ones once the request is done, so none of them gets an instance whose configure
        # hasn't run yet.
        self._made: Dict[_Key, Any] = {}
        self._instance_stack: List[_Key] = []
        self._pending_configures: List[Callable[[], None]] = []
        self._request_depth = 0
        # Each request's explicit destroy hooks, until it publishes its instances.
        self._destroys: Dict[_Key, Any] = {}
        # The shared and weak_shared instances that have a teardown, in the order they were published.
        self._closeables: List[_Closeable] = []
        self._prune_at = _PRUNE_FLOOR
        self._lock = threading.RLock()
        self._bind_self()
        self.make_bindings()

    # ----- Lifecycles -----

    # init and destroy aren't keyword-only, though they're passed by keyword, because filling in keyword-only defaults
    # would slow every call, including a shared hit.

    def shared(
        self,
        factory: Callable[[], T],
        configure: Optional[Callable[[T], None]] = None,
        init: Optional[Callable[[T], None]] = None,
        destroy: "_Destroy[T]" = _INFER,
    ) -> T:
        """
        One instance for the life of the assembly.

        `init` runs as soon as the factory returns, before the instance is given to anything that depends on it, and
        `configure` once the whole graph is built. `destroy` tears the instance down when the assembly closes: by
        default, its `aclose()`, `close()` or `shutdown()`, whichever it has first. Pass a function of the instance
        to tear it down another way, or None to leave it open.
        """
        name = _caller_name(sys._getframe(1))
        instance = self._shared.get(name, _MISSING)
        if instance is not _MISSING:
            return instance  # type: ignore[no-any-return]
        return self._resolve(_SHARED, name, factory, configure, init, destroy)

    def weak_shared(
        self,
        factory: Callable[[], T],
        configure: Optional[Callable[[T], None]] = None,
        init: Optional[Callable[[T], None]] = None,
        destroy: "_Destroy[T]" = _INFER,
    ) -> T:
        """
        Shared while something else holds it. Once nothing does, the next request makes a new one. The instance
        must support weak references: most classes do, but `int`, `str`, `list` and `dict` don't.

        `init`, `configure` and `destroy` are as for `shared`. Closing the assembly tears down the instance if it is
        still alive. One already collected isn't torn down.
        """
        name = _caller_name(sys._getframe(1))
        instance = self._weak_shared.get(name)
        if instance is not None:
            return instance  # type: ignore[no-any-return]
        return self._resolve(_WEAK_SHARED, name, factory, configure, init, destroy)

    def object_graph(
        self,
        factory: Callable[[], T],
        configure: Optional[Callable[[T], None]] = None,
        init: Optional[Callable[[T], None]] = None,
    ) -> T:
        """
        One instance for each request: everything built while resolving one component shares it, and the next
        request makes a new one. `init` and `configure` are as for `shared`. The assembly doesn't keep the instance,
        so closing it doesn't tear the instance down.
        """
        return self._resolve(_OBJECT_GRAPH, _caller_name(sys._getframe(1)), factory, configure, init, _INFER)

    def unshared(
        self,
        factory: Callable[[], T],
        configure: Optional[Callable[[T], None]] = None,
        init: Optional[Callable[[T], None]] = None,
    ) -> T:
        """
        A new instance every time it is asked for. `init` and `configure` are as for `shared`. The assembly doesn't
        keep the instance, so closing it doesn't tear the instance down.
        """
        # Nothing is kept under this name, so the arguments needn't be part of it.
        name = sys._getframe(1).f_code.co_qualname
        with self._lock:
            return self._inject(_UNSHARED, name, factory, configure, init, _INFER)

    # ----- Closing -----

    def close(self) -> None:
        """
        Tears down the assembly's `shared` components, and its `weak_shared` ones that are still alive, newest first,
        then closes the assemblies it owns. A component's teardown is its `destroy=` hook, or else its `close()` or
        `shutdown()`, whichever it has first.

        If any teardown is async, `close()` refuses, before closing anything, and says to use `await aclose()`.

        Closing twice does nothing. A failed teardown doesn't stop the others: their errors are raised together, as an
        ExceptionGroup, once every component has been closed. A closed assembly makes no more components.
        """
        closing = self._begin_close(sync=True)
        if closing is None:
            return
        components, owned = closing
        errors: List[Exception] = []
        for key, instance, destroy in components:
            try:
                result = _teardown(instance, destroy, _SYNC_TEARDOWNS)
                if inspect.isawaitable(result):
                    if inspect.iscoroutine(result):
                        result.close()
                    raise PilgrimError(
                        f"The teardown of {_describe(key)} returned an awaitable, which close() can't wait for. "
                        f"Close {type(self).__name__} with await aclose()."
                    )
            except Exception as e:
                errors.append(_closing(e, key))
        for it in owned:
            try:
                it.close()
            except Exception as e:
                errors.append(e)
        self._raise_errors(errors)

    async def aclose(self) -> None:
        """
        Tears down the assembly's `shared` components, and its `weak_shared` ones that are still alive, newest first,
        then closes the assemblies it owns. A component's teardown is its `destroy=` hook, or else its `aclose()`,
        `close()` or `shutdown()`, whichever it has first. A teardown that returns an awaitable is awaited, so it may
        be sync or async.

        Closing twice does nothing: a second call returns at once, even while the first is still closing. A failed
        teardown doesn't stop the others: their errors are raised together, as an ExceptionGroup, once every component
        has been closed. A closed assembly makes no more components.
        """
        closing = self._begin_close(sync=False)
        if closing is None:
            return
        components, owned = closing
        errors: List[Exception] = []
        for key, instance, destroy in components:
            try:
                result = _teardown(instance, destroy, _ASYNC_TEARDOWNS)
                if inspect.isawaitable(result):
                    await result
            except Exception as e:
                errors.append(_closing(e, key))
        for it in owned:
            try:
                await it.aclose()
            except Exception as e:
                errors.append(e)
        self._raise_errors(errors)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    def _begin_close(self, sync: bool) -> "Optional[_Closing]":
        """
        Marks the assembly closed and lets go of its instances, under the lock, so a request in progress on another
        thread finishes first and one that comes later is refused. Returns what to tear down, newest first, or None
        if the assembly is already closed.
        """
        with self._lock:
            if self._closed:
                return None
            if self._request_depth:
                raise PilgrimError(f"{type(self).__name__} can't close while it is making a component.")
            if sync:
                _refuse_async(type(self).__name__, self._async_teardowns())
            self._closed = True
            components: List[Tuple[_Key, Any, Any]] = []
            seen = set()
            # One instance may be several components, under different names: it is torn down once.
            for key, held, weak, destroy in reversed(self._closeables):
                instance = held() if weak else held
                if instance is not None and id(instance) not in seen:
                    seen.add(id(instance))
                    components.append((key, instance, destroy))
            owned = self._owned[::-1]
            self._closeables.clear()
            self._shared.clear()
            self._weak_shared.clear()
            on_close = list(self._on_close)
        # Outside the lock, since AssemblyHolder takes its own.
        for it in on_close:
            it()
        return components, owned

    def _async_teardowns(self, seen: Optional[Set[int]] = None) -> List[str]:
        """
        The components, here or in the assemblies this one owns or holds, whose teardown close() can't run because
        it is async. `seen` holds the assemblies already looked at, so two that hold each other are looked at once.
        """
        found: List[str] = []
        seen = set() if seen is None else seen
        with self._lock:
            if self._closed or id(self) in seen:
                return found
            seen.add(id(self))
            for key, held, weak, destroy in self._closeables:
                instance = held() if weak else held
                if instance is None:
                    continue
                if destroy is not _INFER:
                    if inspect.iscoroutinefunction(destroy):
                        found.append(_describe(key))
                    continue
                hook = _first_teardown(instance, _SYNC_TEARDOWNS)
                if hook is None or inspect.iscoroutinefunction(hook):
                    found.append(_describe(key))
                elif isinstance(instance, PilgrimAssembly):
                    found += instance._async_teardowns(seen)
            for it in self._owned:
                found += it._async_teardowns(seen)
        return found

    def _raise_errors(self, errors: List[Exception]) -> None:
        if errors:
            raise ExceptionGroup(
                f"{type(self).__name__} closed, but {len(errors)} teardown{'s' if len(errors) > 1 else ''} failed",
                errors,
            )

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
        init: Optional[Callable[[T], None]],
        destroy: "_Destroy[T]",
    ) -> T:
        """
        Looks again under the lock, since another thread may have published the instance while this one waited.
        """
        with self._lock:
            instance = self._made.get((lifecycle, name), _MISSING)
            if instance is _MISSING:
                instance = self._published(lifecycle, name)
            if instance is _MISSING:
                instance = self._inject(lifecycle, name, factory, configure, init, destroy)
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
        init: Optional[Callable[[T], None]],
        destroy: "_Destroy[T]",
    ) -> T:
        """
        Makes one instance. A failure undoes what this call made, so nothing half-built is kept or configured, and
        a request that catches the failure carries on. The outermost call publishes the request's instances.
        """
        if self._closed:
            raise PilgrimError(
                f"{type(self).__name__} is closed, so it makes no more components. Make a new one: "
                f"AssemblyHolder.shared does, once the one it held is closed."
            )
        key = (lifecycle, name)
        # An unshared method may call itself for a new instance each time, so it isn't checked for a cycle.
        if lifecycle is not _UNSHARED and key in self._instance_stack:
            path = " -> ".join(_describe(it) for it in self._instance_stack + [key])
            raise PilgrimError(f"Circular dependency in initializers: {path}. Break it with configure=.")

        made_mark = len(self._made)
        # The queue this call adds to, and undoes on failure, though _configure_graph swaps in a new one below.
        configures = self._pending_configures
        configure_mark = len(configures)
        self._request_depth += 1
        try:
            instance = self._make(key, factory)
            if init is not None:
                init(instance)
            if lifecycle is not _UNSHARED:
                self._made[key] = instance
                if destroy is not _INFER:
                    self._destroys[key] = destroy
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
                self._destroys.pop(it, None)
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
        for key, instance in self._made.items():
            lifecycle, name = key
            if lifecycle is _SHARED:
                self._shared[name] = instance
                self._track(key, instance, instance, False)
            elif lifecycle is _WEAK_SHARED:
                self._weak_shared[name] = instance
                self._track(key, instance, weakref.ref(instance), True)
        self._made.clear()
        self._destroys.clear()

    def _track(self, key: "_Key", instance: Any, held: Any, weak: bool) -> None:
        """
        Remembers an instance to tear down when the assembly closes, if it has a teardown. Done as the instance is
        published, so resolving one that exists costs nothing more.
        """
        destroy = self._destroys.get(key, _INFER)
        if destroy is _INFER:
            try:
                hook = _first_teardown(instance, _ASYNC_TEARDOWNS)
            except Exception:
                # A proxy whose __getattr__ raises, outside the context it needs, is treated as having no teardown,
                # rather than failing a request whose instances are all made.
                hook = None
            if hook is None:
                return
        elif destroy is None:
            return
        self._closeables.append((key, held, weak, destroy))
        if len(self._closeables) >= self._prune_at:
            # weak_shared instances that have been collected, so a component remade often doesn't grow the list.
            self._closeables = [it for it in self._closeables if not it[2] or it[1]() is not None]
            self._prune_at = max(_PRUNE_FLOOR, 2 * len(self._closeables))


_SHARED = PilgrimAssembly.Lifecycle.SHARED
_WEAK_SHARED = PilgrimAssembly.Lifecycle.WEAK_SHARED
_OBJECT_GRAPH = PilgrimAssembly.Lifecycle.OBJECT_GRAPH
_UNSHARED = PilgrimAssembly.Lifecycle.UNSHARED
_Key = Tuple[PilgrimAssembly.Lifecycle, Hashable]
# The component's key, the instance or a weak reference to it, whether it is weak, and its destroy hook.
_Closeable = Tuple[_Key, Any, bool, Any]
# What closing tears down, newest first: each component's key, instance and destroy hook, then the owned assemblies.
_Closing = Tuple[List[Tuple[_Key, Any, Any]], List[PilgrimAssembly]]

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
    except KeyError:
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


def _first_teardown(instance: Any, names: Tuple[str, ...]) -> Optional[Callable[[], object]]:
    # A component that's a class isn't torn down: its close() is its instances', and the class outlives the assembly.
    if isinstance(instance, type):
        return None
    for it in names:
        method = getattr(instance, it, None)
        if callable(method):
            return typing.cast(Callable[[], object], method)
    return None


def _teardown(instance: Any, destroy: Any, names: Tuple[str, ...]) -> object:
    """
    Runs a component's teardown, returning what it returns, which may be an awaitable.
    """
    if destroy is not _INFER:
        return destroy(instance)
    hook = _first_teardown(instance, names)
    return None if hook is None else hook()


def _closing(error: Exception, key: _Key) -> Exception:
    error.add_note(f"While closing {_describe(key)}")
    return error


def _refuse_async(name: str, components: List[str]) -> None:
    if components:
        raise PilgrimError(
            f"{name} can't be closed with close(), since the teardown of {', '.join(components)} is async. Close it "
            f"with await aclose()."
        )


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
