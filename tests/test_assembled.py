from __future__ import annotations

import unittest
from typing import Optional

from pipilgrim import Assembled, AssemblyHolder, PilgrimAssembly, PilgrimError
from tests.utils.castle import Castle
from tests.utils.knight import Knight
from tests.utils.quest import DamselInDistressQuest, HolyGrailQuest, Quest


class StableAssembly(PilgrimAssembly):
    def make_bindings(self):
        self.make_injectable(self.damsel_quest, by_type=DamselInDistressQuest)

    def damsel_quest(self):
        return self.shared(lambda: DamselInDistressQuest())


class KingdomAssembly(PilgrimAssembly):
    def make_bindings(self):
        self.make_injectable(self.knight, by_type=Knight)
        self.make_injectable(self.grail_quest, by_type=Quest)
        self.make_injectable(self.grail_quest, by_key="grail")
        self.make_injectable(self.castle, by_type=Castle)
        self.import_bindings(AssemblyHolder.shared(StableAssembly))

    def knight(self):
        return self.unshared(lambda: Knight(quest=self.grail_quest()))

    def grail_quest(self):
        return self.shared(lambda: HolyGrailQuest("Seek the grail"))

    def castle(self):
        return self.shared(lambda: Castle(name="Camelot", foo="bar"))


class RoundTable:
    knight: Knight = Assembled(assembly=KingdomAssembly)
    quest: Quest = Assembled(assembly=KingdomAssembly)
    grail = Assembled(key="grail", assembly=KingdomAssembly)
    castle: Optional[Castle] = Assembled(assembly=KingdomAssembly)
    damsel: DamselInDistressQuest = Assembled(assembly=KingdomAssembly)
    kingdom: KingdomAssembly = Assembled(assembly=KingdomAssembly)
    later: Squire = Assembled(assembly=KingdomAssembly)


class Tavern:
    knight: Knight = Assembled()
    nameless = Assembled()


class Squire:
    pass


class TestAssembled(unittest.TestCase):
    def setUp(self):
        AssemblyHolder.reset()

    def tearDown(self):
        AssemblyHolder.reset()

    def test_injects_by_annotated_type(self):
        table = RoundTable()
        self.assertEqual("Seek the grail", table.knight.quest.describe())
        self.assertIs(table.quest, table.knight.quest)

    def test_injects_by_key(self):
        self.assertIs(RoundTable().quest, RoundTable().grail)

    def test_optional_injects_as_the_type(self):
        self.assertEqual("Camelot", RoundTable().castle.name)

    def test_injects_an_imported_binding(self):
        self.assertIsInstance(RoundTable().damsel, DamselInDistressQuest)

    def test_injects_the_assembly(self):
        self.assertIs(AssemblyHolder.shared(KingdomAssembly), RoundTable().kingdom)

    def test_resolves_once_for_each_object(self):
        table = RoundTable()
        self.assertIs(table.knight, table.knight)
        self.assertIsNot(table.knight, RoundTable().knight)

    def test_uses_the_default_assembly(self):
        AssemblyHolder.default_assembly_type = KingdomAssembly
        self.assertIsInstance(Tavern().knight, Knight)

    def test_without_an_assembly_says_how_to_name_one(self):
        with self.assertRaisesRegex(PilgrimError, "default_assembly_type"):
            Tavern().knight

    def test_a_missing_binding_names_the_attribute(self):
        with self.assertRaisesRegex(PilgrimError, "no binding for Squire, needed by RoundTable.later"):
            RoundTable().later

    def test_needs_a_type_or_a_key(self):
        AssemblyHolder.default_assembly_type = KingdomAssembly
        with self.assertRaisesRegex(PilgrimError, "needs a type annotation or a key"):
            Tavern().nameless

    def test_make_injectable_takes_one_way_in(self):
        assembly = KingdomAssembly()
        with self.assertRaises(PilgrimError):
            assembly.make_injectable(assembly.knight)
        with self.assertRaises(PilgrimError):
            assembly.make_injectable(assembly.knight, by_type=Knight, by_key="knight")


if __name__ == "__main__":
    unittest.main()
