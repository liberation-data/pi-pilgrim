# pi-pilgrim

**Dependency injection for Python.** A port of [Pilgrim](https://github.com/appsquickly/pilgrim), the successor to
[Typhoon](https://github.com/appsquickly/typhoon).

- **Plain Python.** An assembly is a class, and each component is a method that says how to make it. No decorators
  on your classes, no container configuration, no string lookups unless you choose a key, nothing generated.
- **A composition root.** The key actors in your application, and how they're wired together, are written down in
  one place, as code your IDE can follow and refactor.
- **Lifecycles:** `shared`, `weak_shared`, `object_graph` (Typhoon's idea: one instance for each graph that's
  built) and `unshared`.
- **Circular dependencies** that are resolved after the graph is built, with `configure=` or `PilgrimConfigurable`.
- **Closing.** An assembly closes the components it holds when it stops, newest first, as Spring closes its
  singletons: `with`, `async with`, `close()` or `await aclose()`.
- **Runtime arguments.** An assembly method can take arguments, so it doubles as a factory that mixes the caller's
  values with the components it wires in.
- **Injection into top-level classes** with `Assembled`, by type or by key.
- **Fast and thread-safe.** A shared component resolves in about a third of a microsecond, and is made once however
  many threads ask for it.
- **No dependencies.** Python 3.11 or newer.

```
pip install pi-pilgrim
```

## Quick start

An assembly says what your components are and how they depend on each other. Each method returns one component,
made with a lifecycle, and calls the assembly's other methods for what it needs:

```python
from pipilgrim import PilgrimAssembly


class QuestAssembly(PilgrimAssembly):
    def knight(self) -> Knight:
        return self.unshared(lambda: Knight(quest=self.quest(), horse=self.horse()))

    def quest(self) -> Quest:
        return self.shared(lambda: HolyGrailQuest(map=self.map()))

    def horse(self) -> Horse:
        return self.object_graph(lambda: Horse(name="Bucephalus"))

    def map(self) -> Map:
        return self.shared(lambda: Map(region="Britain"))
```

Ask it for a component and you get the whole graph:

```python
assembly = QuestAssembly()
knight = assembly.knight()  # a Knight, its shared Quest and Map, and a Horse
```

Your classes stay plain: `Knight` takes its quest and horse in its initializer and knows nothing about Pilgrim. To
test it, pass it a fake.

## Lifecycles

| lifecycle | each request for the component gets |
|---|---|
| `shared` | the same instance, for the life of the assembly |
| `weak_shared` | the same instance while something else holds it. Once nothing does, a new one |
| `object_graph` | the same instance throughout one request, and a new one for the next |
| `unshared` | a new instance |

`object_graph` is for components that should be shared inside one graph but not between graphs. If a `Knight` and
its `Squire` both ask for the horse while one knight is being built, they get the same horse. The next knight gets
another.

`weak_shared` keeps a weak reference, so the instance must support one. Most classes do. `int`, `str`, `list` and
`dict` don't, and Pilgrim says so when you try.

## Runtime arguments

An assembly method can take arguments. They're part of the component's name, so a `shared` component made with
different arguments is a different instance:

```python
class QuestAssembly(PilgrimAssembly):
    def knight_named(self, name: str) -> Knight:
        return self.unshared(lambda: Knight(name=name, quest=self.quest()))

    def kingdom(self, region: str) -> Kingdom:
        return self.shared(lambda: Kingdom(region=region))


assembly.knight_named("Lancelot")     # a new Knight, with the shared quest
assembly.kingdom("Wessex")            # the same Kingdom on every call with "Wessex"
```

A `shared`, `weak_shared` or `object_graph` component's arguments must be hashable. An `unshared` one's needn't be.
Arguments that are equal name the same instance, so `kingdom(1)` and `kingdom(1.0)` are one kingdom.

## Circular dependencies

Two components that need each other can't both take the other in their initializer. Give one of them its
dependency after the graph is built, with `configure=`:

```python
class QuestAssembly(PilgrimAssembly):
    def knight(self) -> Knight:
        return self.shared(lambda: Knight(squire=self.squire()))

    def squire(self) -> Squire:
        return self.shared(
            lambda: Squire(),
            configure=lambda squire: setattr(squire, "master", self.knight()),
        )
```

Pilgrim runs every `configure` once each instance in the request has been made, in the order they were made, so
`self.knight()` returns the knight that holds this squire. A cycle through initializers alone is an error that names
the cycle, if any component in it is `shared`, `weak_shared` or `object_graph`:

```
PilgrimError: Circular dependency in initializers: shared(QuestAssembly.knight) -> shared(QuestAssembly.squire)
-> shared(QuestAssembly.knight). Break it with configure=.
```

A class can instead configure itself, by subclassing `PilgrimConfigurable`. Its `configure(assembly)` runs at the
same point:

```python
class Squire(PilgrimConfigurable):
    def configure(self, assembly):
        self.master = assembly.knight()
```

## Closing

An assembly holds its `shared` components, and its `weak_shared` ones while they're alive. Closing it tears them
down, newest first, so a component is closed before the ones it was made from, as Spring closes its singletons:

```python
class DatabaseAssembly(PilgrimAssembly):
    def pool(self) -> Pool:
        return self.shared(lambda: Pool(url="postgresql://localhost/quests"))

    def repository(self) -> QuestRepository:
        return self.shared(lambda: QuestRepository(self.pool()))


async def main():
    async with DatabaseAssembly() as assembly:
        await assembly.repository().save(quest)
    # The repository, then the pool, are closed here, before the event loop ends.
```

A component's teardown is the first of these it has, so its class needn't know about Pilgrim:

| closed with | calls |
|---|---|
| `await assembly.aclose()`, `async with` | `aclose()`, `close()` or `shutdown()`, and awaits what it returns |
| `assembly.close()`, `with` | `close()` or `shutdown()` |

`__exit__` isn't called: called without `__enter__`, it releases a lock that isn't held, or runs a `@contextmanager`
from the start. `object_graph` and `unshared` components aren't held, so closing doesn't close them, as Spring doesn't
close its prototypes. Nor is a component that's a class.

`destroy=` tears a component down another way, and `destroy=None` leaves it open. `init=` runs as soon as the factory
returns, before the instance is given to anything that depends on it, where `configure=` waits for the whole graph:

```python
class QuestAssembly(PilgrimAssembly):
    def ledger(self) -> Ledger:
        return self.shared(lambda: Ledger(), init=lambda it: it.open(), destroy=lambda it: it.flush_and_close())

    def scroll(self) -> Scroll:
        return self.shared(lambda: Scroll(), destroy=None)  # closed by someone else
```

- **Async teardown.** `close()` can't wait for one, so if any component's teardown is async (`aclose()`, an
  `async def close()`, or an `async def` destroy), it refuses before closing anything and says to use
  `await aclose()`. A sync destroy that returns a coroutine is found only when it is called: that component's
  teardown is reported as an error and the others are closed.
- **Errors.** A failed teardown doesn't stop the others. Once every component is closed, their errors are raised
  together as an `ExceptionGroup`, each with a note naming its component.
- **A failed request** keeps none of the components it made, so closing doesn't close them either. If a request can
  fail after it makes a pool, say, ask for the pool first, in a request of its own.
- **Closing twice** does nothing.
- **After closing,** the assembly makes no more components, and says so. Make a new assembly to start again.
- **Threads.** A request in progress on another thread finishes, and its components are closed with the rest. One
  that comes after is refused.

### Nested assemblies

An assembly that asks `AssemblyHolder` for another, which the holder hasn't made yet, owns it, and closes it after its
own components. That's the case for `QuestAssembly` under [More than one assembly](#more-than-one-assembly), which
asks for `MapAssembly` in its initializer: closing the quests closes the maps. The holder then forgets the closed
assembly, so the next `AssemblyHolder.shared(MapAssembly)` makes a new one.

An assembly that something else asked for first, such as an `Assembled` attribute or a component's own `__init__`, is
owned by the holder.
`AssemblyHolder.close()`, or `await AssemblyHolder.aclose()`, closes every assembly it holds, newest first, and leaves
one that another owns to its owner, so each is closed before the ones it asked for:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await AssemblyHolder.aclose()
```

An assembly that's a component of another, made with `self.shared(lambda: MapAssembly())`, is closed like any other
component.

## Injecting into top-level classes

Most of your classes are made by the assembly. A few, such as an application's entry point or a web framework's
handlers, are made by something else. They can declare what they need with `Assembled`.

The assembly says what it can inject in `make_bindings`:

```python
class QuestAssembly(PilgrimAssembly):
    def make_bindings(self):
        self.make_injectable(self.knight, by_type=Knight)
        self.make_injectable(self.quest, by_type=Quest)
        self.make_injectable(self.damsel_quest, by_key="damsel_quest")
    ...
```

A class asks for them by the annotated type, or by key:

```python
from pipilgrim import Assembled, AssemblyHolder


class Tavern:
    knight: Knight = Assembled()
    quest: Quest = Assembled(key="damsel_quest")
    map: Map = Assembled(assembly=MapAssembly)  # from an assembly other than the default


AssemblyHolder.default_assembly_type = QuestAssembly  # once, at start-up

Tavern().knight
```

- An attribute is resolved on first access, and each object keeps what it got. Whether two objects share it is up
  to the component's lifecycle.
- `Optional[Knight]` and `Knight | None` inject as `Knight`.
- An assembly is always injectable as its own type: `assembly: QuestAssembly = Assembled()`.
- A missing binding is an error that names the attribute and the assembly.

### More than one assembly

`AssemblyHolder.shared(SomeAssembly)` returns the application's one instance of an assembly, so components are
shared across the application. An assembly can use another's components directly, or make them injectable through
itself:

```python
class QuestAssembly(PilgrimAssembly):
    def __init__(self):
        self.maps = AssemblyHolder.shared(MapAssembly)
        super().__init__()

    def make_bindings(self):
        self.import_bindings(self.maps)  # Assembled can inject MapAssembly's components through this one

    def quest(self) -> Quest:
        return self.shared(lambda: HolyGrailQuest(map=self.maps.map()))
```

## Where assemblies live

Give an assembly a module of its own, named for its job: `quests/assembly.py`. A larger application can have one
for each package, `quests/assembly.py` and `maps/assembly.py`, joined with `AssemblyHolder.shared` as above.

A package's `__init__.py` may re-export its assembly, but shouldn't define it:

- **Python runs `__init__.py` whenever anything in the package is imported.** Importing `quests.knight` runs
  `quests/__init__.py` first, so an assembly defined there imports every class it wires, on every import from the
  package.
- **That makes circular imports.** If a module in the package imports from the package itself, `__init__.py` is
  part-way through importing it, and the name isn't there yet.
- **Readers expect `__init__.py` to say what a package exports**, not how it is wired.

## Resources

`FileResource` provides the text of a file, relative to a directory:

```python
import os

from pipilgrim import FileResource


class QuestAssembly(PilgrimAssembly):
    def quest(self) -> Quest:
        return self.shared(lambda: HolyGrailQuest(
            description=FileResource(os.path.dirname(__file__), "quest.txt").provide()
        ))
```

`InstanceProvider` is the protocol for things like this: any object with a `provide()` method counts as one, so
you can write your own and call them the same way.

## Testing

- Unit tests needn't involve Pilgrim: your classes take their collaborators in their initializers, so pass fakes.
- To swap one component in an integration test, subclass the assembly and override that method:

  ```python
  class TestQuestAssembly(QuestAssembly):
      def map(self) -> Map:
          return self.shared(lambda: FakeMap())
  ```

  An override can build on the method it replaces. Take `super().map` before the lambda, since `super()` with no
  arguments doesn't work inside one:

  ```python
  class TestQuestAssembly(QuestAssembly):
      def map(self) -> Map:
          real = super().map
          return self.shared(lambda: RecordingMap(real()))
  ```
- `AssemblyHolder.reset()` forgets every assembly and the default, so each test can start clean. It doesn't close
  them: call `AssemblyHolder.close()` first if they hold resources.

## Threads and asyncio

Resolving is thread-safe. One thread at a time builds a graph from an assembly, and a `shared` component is made
once however many threads ask for it at once. A `shared` or `weak_shared` component that already exists is returned
without taking the lock. Each assembly has its own lock, so one assembly may call into another, as `QuestAssembly`
calls `MapAssembly` above, but not back: two threads could each hold one lock and wait for the other.

Factories are plain functions, so a resolve never awaits and asyncio tasks can't interleave inside one. Make
components that need async set-up, such as a connection pool, in the assembly, and open them at start-up:

```python
assembly = AppAssembly()
await assembly.database().connect()
...
await assembly.aclose()  # closes the database, with its aclose() or close()
```

## How it works

A lifecycle names the component by the method that called it, read from the caller's frame, along with that
method's arguments. So call the lifecycle directly from the assembly method, as every example here does, and not
from a helper that the method calls: the helper's name would be used instead. And leave the arguments as they were
passed until then, since their values at that point are what name the instance.

Reading the frame costs well under a microsecond, where `inspect.stack()` reads every frame and its source file.
Resolving a `shared` component that already exists takes about 0.3 µs, and making an `unshared` one about 1 µs, on
an Apple M2 Max.

## License

MIT. Copyright (c) 2024 Liberation Data.

pi-pilgrim follows [Pilgrim](https://github.com/appsquickly/pilgrim), which is based on
[FieryCrucible](https://github.com/jkolb/FieryCrucible) by [jkolb](https://github.com/jkolb).
