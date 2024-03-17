import unittest

from tests.utils.quest_assembly import QuestAssembly


class TestQuestAssembly(unittest.TestCase):

    def test_knight_should_be_assembled(self):
        quest_assembly = QuestAssembly()
        knight = quest_assembly.knight()
        self.assertIsNotNone(knight)
        self.assertEqual("The damsel is a comely fellow named Bruce, the knight is none other than the fearsome Fiona",
                         knight.quest.describe())

    def test_quest_injection_by_type(self):
        quest_assembly = QuestAssembly()
        quest = quest_assembly.holy_grail_quest()
        self.assertIsNotNone(quest)

    def test_quest_injection_by_key(self):
        quest_assembly = QuestAssembly()
        another_quest = quest_assembly.damsel_in_distress_quest()
        self.assertIsNotNone(another_quest)

    def test_injection_with_generic_type(self):
        quest_assembly = QuestAssembly()
        castle = quest_assembly.castle()
        self.assertIsNotNone(castle)


if __name__ == '__main__':
    unittest.main()
