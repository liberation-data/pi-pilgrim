# pi-pilgrim

**Dependency injection for Python.** A port of [Pilgrim](https://github.com/appsquickly/pilgrim), the successor to
[Typhoon](https://github.com/appsquickly/typhoon).

- **Plain Python.** An assembly is a class, and each component is a method that says how to make it. No decorators
  on your classes, no container configuration, no string lookups, nothing generated.
- **A composition root.** The key actors in your application, and how they're wired together, are written down in
  one place, as code your IDE can follow and refactor.
- **Lifecycles:** `shared`, `weak_shared`, `object_graph` (Typhoon's idea: one instance for each graph that's
  built) and `unshared`.
- **Circular dependencies** that are resolved after the graph is built, with `configure=` or `PilgrimConfigurable`.
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

Pilgrim runs every `configure` once each instance in the request has been made, so `self.knight()` returns the
knight that holds this squire. A cycle through initializers alone, whatever the lifecycles, is an error that names the cycle:

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
- `AssemblyHolder.reset()` forgets every assembly and the default, so each test can start clean.

## Threads and asyncio

Resolving is thread-safe. One thread builds a graph at a time, and a `shared` component is made once however many
threads ask for it at once. A `shared` or `weak_shared` component that already exists is returned without taking
the lock.

Factories are plain functions, so a resolve never awaits and asyncio tasks can't interleave inside one. Make
components that need async set-up, such as a connection pool, in the assembly, and open them at start-up:

```python
assembly = AppAssembly()
await assembly.database().connect()
```

## How it works

A lifecycle names the component by the method that called it, read from the caller's frame, along with that
method's arguments. So call the lifecycle directly from the assembly method, as every example here does, and not
from a helper that the method calls: the helper's name would be used instead.

Reading the frame costs well under a microsecond, where `inspect.stack()` reads every frame and its source file.
Resolving a `shared` component that already exists takes about 0.3 µs, and making an `unshared` one about 1 µs, on
an Apple M2 Max.

## Upgrading from 0.1

Assemblies written for 0.1 work unchanged. In 0.2:

- Resolving is about 600 times faster, and thread-safe.
- `weak_shared`, `PilgrimConfigurable`, `Assembled`, `AssemblyHolder` and bindings are new, and a method's
  arguments, `*args` and `**kwargs` included, now name its instance.
- Errors are `PilgrimError`, and a circular dependency names the cycle.
- A component is named by its method's qualified name, so an override no longer clashes with the method it
  overrides.
- `loguru` and `python-dotenv` are no longer dependencies.

## License

MIT. Copyright (c) 2024 Liberation Data.

pi-pilgrim follows [Pilgrim](https://github.com/appsquickly/pilgrim), which is based on
[FieryCrucible](https://github.com/jkolb/FieryCrucible) by [jkolb](https://github.com/jkolb).
