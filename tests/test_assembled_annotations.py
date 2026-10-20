import unittest

from pipilgrim import Assembled, PilgrimAssembly, PilgrimError

# Without `from __future__ import annotations`, so annotations can name classes defined in a function.


class EmptyAssembly(PilgrimAssembly):
    pass


class TestAssembledAnnotations(unittest.TestCase):
    def test_resolves_a_class_defined_in_a_function(self):
        class Herald:
            pass

        class Gate:
            herald: Herald = Assembled(assembly=EmptyAssembly)

        with self.assertRaisesRegex(PilgrimError, "no binding for Herald"):
            Gate().herald


if __name__ == "__main__":
    unittest.main()
