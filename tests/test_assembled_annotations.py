import unittest
from typing import Annotated, Optional

from pipilgrim import Assembled, PilgrimAssembly, PilgrimError

# Without `from __future__ import annotations`, so annotations can name classes defined in a function.


class EmptyAssembly(PilgrimAssembly):
    pass


class Knight:
    pass


class Retinue:
    pass


class ArmyAssembly(PilgrimAssembly):
    def make_bindings(self):
        self.make_injectable(self.knight, by_type=Knight)
        self.make_injectable(self.retinue, by_type=list[Knight])

    def knight(self):
        return self.shared(lambda: Knight())

    def retinue(self):
        return self.shared(lambda: Retinue())


class Camp:
    marked: Annotated[Knight, "marked"] = Assembled(assembly=ArmyAssembly)
    quoted: "Knight" = Assembled(assembly=ArmyAssembly)
    maybe: Optional["Knight"] = Assembled(assembly=ArmyAssembly)
    retinue: list["Knight"] = Assembled(assembly=ArmyAssembly)


class TestAssembledAnnotations(unittest.TestCase):
    def test_resolves_annotations_as_get_type_hints_does(self):
        camp = Camp()
        self.assertIsInstance(camp.marked, Knight)
        self.assertIsInstance(camp.quoted, Knight)
        self.assertIsInstance(camp.maybe, Knight)
        self.assertIsInstance(camp.retinue, Retinue)

    def test_resolves_a_class_defined_in_a_function(self):
        class Herald:
            pass

        class Gate:
            herald: Herald = Assembled(assembly=EmptyAssembly)

        with self.assertRaisesRegex(PilgrimError, "no binding for Herald"):
            Gate().herald


if __name__ == "__main__":
    unittest.main()
