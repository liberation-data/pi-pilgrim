import unittest

from tests.utils.example_assembly import ExampleAssembly
from tests.utils.test_instance import TestInstance


class TestExampleAssembly(unittest.TestCase):
    def setUp(self):
        self.test_factory = ExampleAssembly()
        TestInstance.clear_init_counts()

    def tearDown(self):
        pass

    def test_shared(self):
        instance_a1 = self.test_factory.shared_a1()
        instance_a2 = self.test_factory.shared_a2()
        instance_b1 = self.test_factory.shared_b1()
        instance_b2 = self.test_factory.shared_b2()

        self.assertEqual(instance_a1.name, "A1")
        self.assertEqual(instance_a2.name, "A2")
        self.assertEqual(instance_b1.name, "B1")
        self.assertEqual(instance_b2.name, "B2")

        self.assertEqual(instance_a1.init_count, 1)
        self.assertEqual(instance_a2.init_count, 1)
        self.assertEqual(instance_b1.init_count, 1)
        self.assertEqual(instance_b2.init_count, 1)

        self.assertIs(instance_a1.dependency, instance_a2)
        self.assertIsNone(instance_a1.delegate)
        self.assertIsNone(instance_a2.dependency)
        self.assertIs(instance_a2.delegate, instance_a1)
        self.assertIsNone(instance_b1.dependency)
        self.assertIsNone(instance_b1.delegate)
        self.assertIs(instance_b2.dependency, instance_b1)
        self.assertIs(instance_b2.delegate, instance_a1)

    def test_unshared(self):
        instance_a1 = self.test_factory.unshared_a1()
        instance_a2 = self.test_factory.unshared_a2()

        self.assertEqual(instance_a1.name, "A1")
        self.assertEqual(instance_a2.name, "A2")

        self.assertIsNot(instance_a1.dependency, instance_a2)
        self.assertIsNone(instance_a1.delegate)
        self.assertIsNone(instance_a2.dependency)
        self.assertIsNone(instance_a2.delegate)

    def test_scoped(self):
        instance_a1 = self.test_factory.scoped_a1()
        instance_a2 = self.test_factory.scoped_a2()
        instance_a3 = self.test_factory.scoped_a3()

        self.assertEqual(instance_a1.name, "A1")
        self.assertEqual(instance_a2.name, "A2")
        self.assertEqual(instance_a3.name, "A3")

        self.assertEqual(instance_a1.init_count, 1)
        self.assertEqual(instance_a2.init_count, 2)
        self.assertEqual(instance_a3.init_count, 3)

        self.assertIs(instance_a1.dependency.delegate, instance_a1.delegate)
        self.assertIsNot(instance_a1.delegate, instance_a2.delegate)


if __name__ == '__main__':
    unittest.main()
