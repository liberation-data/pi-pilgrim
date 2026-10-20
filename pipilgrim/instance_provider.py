from typing import Protocol, TypeVar, runtime_checkable

T = TypeVar("T", covariant=True)


@runtime_checkable
class InstanceProvider(Protocol[T]):
    """
    Provides a value for an assembly to inject: `FileResource` reads a file, for example. Any object with a
    `provide()` method counts as one, whether or not it subclasses InstanceProvider.
    """

    def provide(self) -> T: ...
