import sys
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

    @unittest.skipIf(sys.version_info < (3, 14), "annotations are evaluated lazily from 3.14")
    def test_a_lazy_annotation_that_cant_be_found_doesnt_stop_the_others(self):
        class Garrison:
            knight: Knight = Assembled(assembly=ArmyAssembly)
            ledger: Undefined = Assembled(assembly=ArmyAssembly)  # noqa: F821

        self.assertIsInstance(Garrison().knight, Knight)
        with self.assertRaisesRegex(PilgrimError, "Garrison.ledger is annotated with Undefined"):
            Garrison().ledger

    def test_an_attribute_named_as_its_type_resolves_the_type(self):
        class Sentry:
            Knight: "Knight" = Assembled(assembly=ArmyAssembly)

        self.assertIsInstance(Sentry().Knight, Knight)

    def test_resolves_a_class_from_a_module_that_isnt_imported(self):
        names = {"__name__": "not_a_module", "Assembled": Assembled, "ArmyAssembly": ArmyAssembly, "Knight": Knight}
        exec("class Tent:\n    knight: Knight = Assembled(assembly=ArmyAssembly)\n", names)
        self.assertIsInstance(names["Tent"]().knight, Knight)

    def test_resolves_a_class_defined_in_a_function(self):
        class Herald:
            pass

        class Gate:
            herald: Herald = Assembled(assembly=EmptyAssembly)

        with self.assertRaisesRegex(PilgrimError, "no binding for Herald"):
            Gate().herald


if __name__ == "__main__":
    unittest.main()
