from typing import TypeVar

T = TypeVar("T")


class InstanceProviderMeta(type):
    """
    Any object with a callable `provide` counts as an InstanceProvider, whether or not it subclasses one.
    """

    def __instancecheck__(cls, instance):
        return cls.__subclasscheck__(type(instance))

    def __subclasscheck__(cls, subclass):
        return hasattr(subclass, "provide") and callable(subclass.provide)


class InstanceProvider(metaclass=InstanceProviderMeta):
    """
    Provides a value for an assembly to inject: `FileResource` reads a file, for example.
    """
