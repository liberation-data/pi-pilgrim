from __future__ import annotations

import threading
import time
import unittest
from typing import TYPE_CHECKING, Optional, Union

from pipilgrim import Assembled, AssemblyHolder, PilgrimAssembly, PilgrimError
from tests.utils.castle import Castle
from tests.utils.knight import Knight
from tests.utils.quest import DamselInDistressQuest, HolyGrailQuest, Quest
from tests.utils.threads import run_in_threads

if TYPE_CHECKING:
    from decimal import Decimal


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


class TestKingdomAssembly(KingdomAssembly):
    pass


class SlowAssembly(PilgrimAssembly):
    made = 0

    def __init__(self):
        time.sleep(0.05)
        SlowAssembly.made += 1
        super().__init__()


class SlowlyBoundAssembly(PilgrimAssembly):
    def make_bindings(self):
        self.make_injectable(self.token, by_key="token")

    def token(self):
        def make():
            time.sleep(0.05)
            return object()

        return self.unshared(make)


class ArmoryBase(PilgrimAssembly):
    pass


class Armory(ArmoryBase):
    def make_bindings(self):
        self.import_bindings(ArmoryBase())


class Inherited:
    knight: Knight


class Heir(Inherited):
    knight = Assembled(assembly=KingdomAssembly)


class Hall:
    class Banner:
        pass

    banner: "Banner" = Assembled(assembly=KingdomAssembly)
    ledger: Decimal = Assembled(assembly=KingdomAssembly)


class RoundTable:
    knight: Knight = Assembled(assembly=KingdomAssembly)
    quest: Quest = Assembled(assembly=KingdomAssembly)
    grail = Assembled(key="grail", assembly=KingdomAssembly)
    castle: Optional[Castle] = Assembled(assembly=KingdomAssembly)
    damsel: DamselInDistressQuest = Assembled(assembly=KingdomAssembly)
    kingdom: KingdomAssembly = Assembled(assembly=KingdomAssembly)
    later: Squire = Assembled(assembly=KingdomAssembly)
    keep: Castle | None = Assembled(assembly=KingdomAssembly)
    nowhere = Assembled(key="nowhere", assembly=KingdomAssembly)
    price: Decimal


class Tavern:
    knight: Knight = Assembled()
    nameless = Assembled()


class Squire:
    pass


class Steward:
    kingdom: KingdomAssembly = Assembled()
    token = Assembled(key="token", assembly=SlowlyBoundAssembly)


class Page:
    __slots__ = ()
    knight: Knight = Assembled(assembly=KingdomAssembly)


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
        with self.assertRaisesRegex(PilgrimError, "one of by_type or by_key"):
            assembly.make_injectable(assembly.knight)
        with self.assertRaisesRegex(PilgrimError, "one of by_type or by_key"):
            assembly.make_injectable(assembly.knight, by_type=Knight, by_key="knight")

    def test_union_with_none_injects_as_the_type(self):
        self.assertEqual("Camelot", RoundTable().keep.name)
        self.assertIsNone(KingdomAssembly().factory_for(Union[Castle, Knight]))

    def test_an_unresolvable_annotation_elsewhere_doesnt_matter(self):
        self.assertIsInstance(RoundTable().knight, Knight)

    def test_a_missing_key_names_the_key(self):
        with self.assertRaisesRegex(PilgrimError, "no binding for 'nowhere', needed by RoundTable.nowhere"):
            RoundTable().nowhere

    def test_a_subclass_injects_as_the_assembly_it_overrides(self):
        AssemblyHolder.default_assembly_type = TestKingdomAssembly
        self.assertIs(AssemblyHolder.shared(TestKingdomAssembly), Steward().kingdom)

    def test_imports_inject_the_imported_assembly_and_keep_the_importer(self):
        kingdom = KingdomAssembly()
        self.assertIs(AssemblyHolder.shared(StableAssembly), kingdom.factory_for(StableAssembly)())
        self.assertIs(kingdom, kingdom.factory_for(KingdomAssembly)())

    def test_an_import_doesnt_replace_the_importer(self):
        armory = Armory()
        self.assertIs(armory, armory.factory_for(ArmoryBase)())
        self.assertIsNone(armory.factory_for(PilgrimAssembly))

    def test_reads_an_annotation_from_a_base_class(self):
        self.assertIsInstance(Heir().knight, Knight)

    def test_resolves_a_nested_class_annotation(self):
        # Nothing binds Banner, so the error shows the annotation was resolved to the class.
        with self.assertRaisesRegex(PilgrimError, "no binding for Banner"):
            Hall().banner

    def test_an_annotation_that_cant_be_found_says_so(self):
        with self.assertRaisesRegex(PilgrimError, "Hall.ledger is annotated with Decimal, which can't be found"):
            Hall().ledger

    def test_threads_see_one_value_for_an_object(self):
        steward, seen = Steward(), []
        run_in_threads(lambda: seen.append(steward.token), 2)
        self.assertIs(seen[0], seen[1])
        self.assertIs(seen[0], steward.token)

    def test_slots_without_dict_say_so(self):
        with self.assertRaisesRegex(PilgrimError, "Page has __slots__"):
            Page().knight

    def test_the_class_attribute_is_the_descriptor(self):
        self.assertIsInstance(RoundTable.knight, Assembled)


class TestAssemblyHolder(unittest.TestCase):
    def setUp(self):
        SlowAssembly.made = 0
        AssemblyHolder.reset()

    def tearDown(self):
        AssemblyHolder.reset()

    def test_holds_one_of_each_until_reset(self):
        first = AssemblyHolder.shared(KingdomAssembly)
        self.assertIs(first, AssemblyHolder.shared(KingdomAssembly))
        AssemblyHolder.default_assembly_type = KingdomAssembly
        self.assertIs(first, AssemblyHolder.shared())
        AssemblyHolder.reset()
        self.assertIsNone(AssemblyHolder.default_assembly_type)
        self.assertIsNot(first, AssemblyHolder.shared(KingdomAssembly))

    def test_makes_one_across_threads(self):
        ready = threading.Barrier(8)

        def resolve():
            ready.wait()
            AssemblyHolder.shared(SlowAssembly)

        run_in_threads(resolve, 8)
        self.assertEqual(1, SlowAssembly.made)


if __name__ == "__main__":
    unittest.main()
