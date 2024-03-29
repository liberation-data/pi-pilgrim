import os
from pipilgrim import PilgrimAssembly
from pipilgrim.file_resource import FileResource
from tests.utils.another_assembly import provider as another_provider
from tests.utils.castle import Castle
from tests.utils.knight import Knight
from tests.utils.quest import HolyGrailQuest, DamselInDistressQuest


class QuestAssembly(PilgrimAssembly):
    def __init__(self):
        super().__init__()
        self.other = another_provider

    def knight(self):
        return self.object_graph(lambda: Knight(quest=self.other.another_quest()))

    def holy_grail_quest(self):
        return self.shared(lambda: HolyGrailQuest(
            description=FileResource(
                root_dir=os.path.dirname(__file__),
                path="text_file.txt"
            ).provide()
        ))

    def damsel_in_distress_quest(self):
        return self.shared(lambda: DamselInDistressQuest())

    def castle(self):
        return self.shared(lambda: Castle(name="Tintagel", foo="hello"))


provider = QuestAssembly()
