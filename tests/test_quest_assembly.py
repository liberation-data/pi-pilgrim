import unittest

from tests.utils.quest_assembly import QuestAssembly


class TestQuestAssembly(unittest.TestCase):

    def test_knight_should_be_assembled(self):
        quest_assembly = QuestAssembly()
        knight = quest_assembly.knight()
        self.assertIsNotNone(knight)
        self.assertEqual("The damsel is a comely fellow named Bruce, the knight is none other than the fearsome Fiona",
                         knight.quest.describe())

    def test_file_resource_injected(self):
        quest_assembly = QuestAssembly()
        quest = quest_assembly.holy_grail_quest()
        self.assertIsNotNone(quest)
        self.assertEqual("The quick brown fux jumped over the lazy dogs", quest.describe())

    def test_multiple_instances_of_type_supported(self):
        quest_assembly = QuestAssembly()
        another_quest = quest_assembly.damsel_in_distress_quest()
        self.assertIsNotNone(another_quest)

if __name__ == '__main__':
    unittest.main()
