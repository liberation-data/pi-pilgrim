from pilgrim.pilgrim_assembly import PilgrimAssembly
from tests.utils.test_instance import TestInstance


class ExampleAssembly(PilgrimAssembly):
    def shared_a1(self) -> TestInstance:
        return self.shared(lambda: TestInstance(name="A1", dependency=self.shared_a2()))

    def shared_a2(self) -> TestInstance:
        return self.shared(lambda: TestInstance(name="A2"),
                           configure=lambda instance: setattr(instance, "delegate", self.shared_a1()))

    def shared_b1(self) -> TestInstance:
        return self.shared(lambda: TestInstance(name="B1", required_during_init=True))

    def shared_b2(self) -> TestInstance:
        return self.shared(
            lambda: TestInstance(name="B2", dependency=self.shared_b1()),
            lambda instance: setattr(instance, "delegate", self.shared_a1())
        )

    def unshared_a1(self) -> TestInstance:
        return self.unshared(lambda: TestInstance(name="A1", dependency=self.unshared_a2()))

    def unshared_a2(self) -> TestInstance:
        return self.unshared(lambda: TestInstance(name="A2"))

    def scoped_a1(self) -> TestInstance:
        return self.object_graph(lambda: TestInstance(name="A1", dependency=self.scoped_a2()), lambda instance: setattr(instance, "delegate", self.scoped_a3()))

    def scoped_a2(self) -> TestInstance:
        return self.object_graph(lambda: TestInstance(name="A2"), lambda instance: setattr(instance, "dependency", self.scoped_a3()) or setattr(instance, "delegate", self.scoped_a3()))

    def scoped_a3(self) -> TestInstance:
        return self.object_graph(lambda: TestInstance(name="A3"))
