from typing import Dict, TypeVar

T = TypeVar("T")


class InstanceProviderMeta(type):
    """
    When an assembly method returns
    """
    def __instancecheck__(cls, instance):
        return cls.__subclasscheck__(type(instance))

    def __subclasscheck__(cls, subclass):
        return (hasattr(subclass, 'provide') and
                callable(subclass.provide))


class InstanceProvider(metaclass=InstanceProviderMeta):
    pass
