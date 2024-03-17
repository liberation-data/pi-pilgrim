import inspect


class PilgrimAssembly:
    class Lifecycle:
        SHARED = "shared"
        OBJECT_GRAPH = "objectGraph"
        UNSHARED = "unshared"

    class InstanceKey:
        def __init__(self, lifecycle, name):
            self.lifecycle = lifecycle
            self.name = name

        def __str__(self):
            return f"{self.lifecycle}({self.name})"

        def __eq__(self, other):
            return self.lifecycle == other.lifecycle and self.name == other.name

        def __hash__(self):
            return hash((self.lifecycle, self.name))

    def __init__(self):
        self.bindings = {}
        self.shared_instances = {}
        self.scoped_instances = {}
        self.instance_stack = []
        self.configure_stack = []
        self.request_depth = 0

    def shared(self, factory, configure=None):
        stack = inspect.stack()
        the_class = stack[1][0].f_locals["self"].__class__.__name__
        name = stack[1].function
        instance = self.shared_instances.get(name)
        if instance is not None:
            return instance
        return self.inject(self.Lifecycle.SHARED, name, factory, configure)

    def unshared(self, factory, configure=None):
        name = inspect.stack()[1].function
        return self.inject(self.Lifecycle.UNSHARED, name, factory, configure)

    def object_graph(self, factory, configure=None):
        name = inspect.stack()[1].function
        instance = self.scoped_instances.get(name)
        if instance is not None:
            return instance
        return self.inject(self.Lifecycle.OBJECT_GRAPH, name, factory, configure)

    def inject(self, lifecycle, name, factory, configure):
        key = self.InstanceKey(lifecycle, name)

        if lifecycle != self.Lifecycle.UNSHARED and key in self.instance_stack:
            raise Exception(f"Circular dependency from one of {self.instance_stack} to {key} in initializer")

        self.instance_stack.append(key)
        instance = factory()
        self.instance_stack.pop()
        self.register_instance(lifecycle, name, instance)

        self.perform_lifecycle_events(instance, configure)

        if not self.instance_stack:
            delayed_configures = list(self.configure_stack)
            self.configure_stack.clear()

            self.request_depth += 1

            for delayed_configure in delayed_configures:
                delayed_configure()

            self.request_depth -= 1

            if self.request_depth == 0:
                self.scoped_instances.clear()

        return instance

    def perform_lifecycle_events(self, instance, configure):
        if configure:
            self.configure_stack.append(lambda: configure(instance))

        # if isinstance(instance, PilgrimConfigurable):
        #     self.configure_stack.append(lambda: instance.configure(self))

    def register_instance(self, lifecycle, name, instance):
        if lifecycle == self.Lifecycle.SHARED:
            self.shared_instances[name] = instance
        elif lifecycle == self.Lifecycle.OBJECT_GRAPH:
            self.scoped_instances[name] = instance

