"""
scripts/release_version.py: the semver arithmetic the release workflows do. A prerelease comes before its release,
which `sort -V` gets the wrong way round.
"""

import importlib.util
import io
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

_root = Path(__file__).parents[1]
_spec = importlib.util.spec_from_file_location("release_version", _root / "scripts" / "release_version.py")
release_version = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release_version)


class TestReleaseVersion(unittest.TestCase):
    def test_bump(self):
        for version, kind, expected in [
            ("0.2.0", "patch", "0.2.1"),
            ("0.2.0", "minor", "0.3.0"),
            ("0.2.0", "major", "1.0.0"),
            ("0.2.0", "prerelease", "0.2.1-rc.0"),
            ("0.3.1-rc.0", "prerelease", "0.3.1-rc.1"),
            ("0.3.1-rc.0", "patch", "0.3.1"),
            ("0.4.0-rc.1", "minor", "0.4.0"),
            ("0.4.0-rc.1", "major", "1.0.0"),
            ("1.0.0-rc.1", "major", "1.0.0"),
            ("1.2.0-rc.1", "major", "2.0.0"),
            ("1.2.3-rc.0", "minor", "1.3.0"),
            ("0.4.0-beta.2", "prerelease", "0.4.0-rc.0"),
            ("0.4.0-rc", "prerelease", "0.4.0-rc.0"),
            ("0.4.0-rc.alpha", "prerelease", "0.4.0-rc.0"),
            ("0.4.0-rc.1.fix", "prerelease", "0.4.0-rc.2.fix"),
        ]:
            with self.subTest(version=version, kind=kind):
                self.assertEqual(str(release_version.bump(release_version.Version(version), kind)), expected)

    def test_greater(self):
        for first, second, greater in [
            ("0.3.0", "0.3.0-rc.1", True),
            ("0.3.0-rc.1", "0.3.0", False),
            ("0.3.0-rc.10", "0.3.0-rc.2", True),
            ("0.3.0-rc.1", "0.3.0-rc", True),
            ("0.10.0", "0.9.0", True),
            ("0.2.0", "0.2.0", False),
            ("1.0.0-alpha.beta", "1.0.0-alpha.1", True),
            ("1.0.0-alpha.1", "1.0.0-alpha", True),
            ("1.0.0-beta.11", "1.0.0-beta.2", True),
        ]:
            with self.subTest(first=first, second=second):
                self.assertIs(release_version.main(["greater", first, second]) == 0, greater)

    def test_newest_tag_by_semver(self):
        tags = ["v0.1.0", "v0.3.0-rc.1", "v0.3.0", "0.9.0", "vnext", "v0.2.0"]
        self.assertEqual(release_version.newest(tags), "v0.3.0")
        self.assertEqual(release_version.newest(["v0.3.0", "v0.3.1-rc.0"]), "v0.3.1-rc.0")
        self.assertIsNone(release_version.newest([]))
        self.assertEqual(release_version.newest(["v0.2.0", "v0.9.0-rc..1", "v09.0.0"]), "v0.2.0")

    def test_refuses_what_isnt_a_version(self):
        for text in ["0.3", "0.3.0rc1"]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                release_version.Version(text)

    def test_newest_reads_the_tags_from_stdin(self):
        # The workflows pipe `git tag -l 'v*'` in, one tag a line, and print nothing when no tag is a version.
        for tags, expected in [("v0.2.0\nv0.3.0-rc.1\nv0.3.0\n", "v0.3.0\n"), ("vnext\n", ""), ("", "")]:
            with self.subTest(tags=tags), mock.patch("sys.stdin", io.StringIO(tags)):
                with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
                    self.assertEqual(release_version.main(["newest"]), 0)
                self.assertEqual(out.getvalue(), expected)

    def test_refuses_a_bad_bump_or_version_with_exit_status_2(self):
        # prepare-release's workflow_call takes bump as any string, so a kind that isn't one fails the step, saying why.
        for args in [["bump", "0.3.0", "huge"], ["bump", "0.3", "patch"], ["greater", "0.3.0rc1", "0.3.0"], ["set"]]:
            with self.subTest(args=args), mock.patch("sys.stderr", new_callable=io.StringIO) as err:
                self.assertEqual(release_version.main(args), 2)
                self.assertNotEqual(err.getvalue(), "")

    def test_set_rewrites_the_project_version(self):
        with tempfile.TemporaryDirectory() as root:
            pyproject = Path(root) / "pyproject.toml"
            pyproject.write_text('[project]\nname = "pi-pilgrim"\nversion = "0.2.0"\n\n[tool.x]\ntarget-version = "py311"\n')
            release_version.set_version(pyproject, release_version.Version("0.3.0-rc.1"))
            self.assertIn('version = "0.3.0-rc.1"', pyproject.read_text())
            self.assertIn('target-version = "py311"', pyproject.read_text())

    def test_set_refuses_another_tables_version(self):
        with tempfile.TemporaryDirectory() as root:
            pyproject = Path(root) / "pyproject.toml"
            original = '[project]\nname = "pi-pilgrim"\nversion="0.2.0"\n\n[tool.x]\nversion = "1.0"\n'
            pyproject.write_text(original)
            with self.assertRaises(ValueError):
                release_version.set_version(pyproject, release_version.Version("0.3.0"))
            self.assertEqual(pyproject.read_text(), original)

    def test_set_bumps_this_repositorys_pyproject(self):
        # Prepare release runs `set` on this pyproject.toml: it has one version line, and that is [project]'s.
        with tempfile.TemporaryDirectory() as root:
            pyproject = Path(root) / "pyproject.toml"
            original = (_root / "pyproject.toml").read_text()
            pyproject.write_text(original)
            release_version.set_version(pyproject, release_version.Version("99.0.0-rc.1"))
            changed = tomllib.loads(pyproject.read_text())
            self.assertEqual(changed["project"]["version"], "99.0.0-rc.1")
            self.assertEqual(changed["tool"], tomllib.loads(original)["tool"])


if __name__ == "__main__":
    unittest.main()
