import tempfile
import unittest
from pathlib import Path

from pipilgrim import FileResource, InstanceProvider


class TestResources(unittest.TestCase):
    def test_file_resource_reads_relative_to_its_root(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / "scrolls").mkdir()
            (Path(root) / "scrolls" / "quest.txt").write_text("Café", encoding="latin-1")
            self.assertEqual("Café", FileResource(root, "scrolls/quest.txt", encoding="latin-1").provide())

    def test_file_resource_reads_utf_8_by_default(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / "quest.txt").write_text("Café", encoding="utf-8")
            self.assertEqual("Café", FileResource(root, "quest.txt").provide())

    def test_a_missing_file_raises(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(FileNotFoundError):
                FileResource(root, "missing.txt").provide()

    def test_anything_with_provide_is_an_instance_provider(self):
        class Herald:
            def provide(self):
                return "Hear ye"

        self.assertIsInstance(FileResource(".", "x"), InstanceProvider)
        self.assertIsInstance(Herald(), InstanceProvider)
        self.assertNotIsInstance(object(), InstanceProvider)


if __name__ == "__main__":
    unittest.main()
