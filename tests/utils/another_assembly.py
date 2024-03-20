from pipilgrim import PilgrimAssembly
from tests.utils.quest import DamselInDistressQuest


class AnotherAssembly(PilgrimAssembly):
    def another_quest(self):
        return self.shared(lambda: DamselInDistressQuest())


provider = AnotherAssembly()
