"""
scripts/smoke_wheel.py: the release workflow's last check of the wheel it publishes. It must fail when the wheel is
broken, even run where a checkout's pipilgrim could stand in for the wheel's.
"""

import base64
import hashlib
import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

_root = Path(__file__).parents[1]
_script = _root / "scripts" / "smoke_wheel.py"
_spec = importlib.util.spec_from_file_location("smoke_wheel", _script)
smoke_wheel = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(smoke_wheel)


def _wheel(directory: Path, package: dict[str, str]) -> Path:
    # A wheel pip installs, with the files given for its package: none, when the package is left out of the build.
    name, dist_info = "pi_pilgrim-0.0.0-py3-none-any.whl", "pi_pilgrim-0.0.0.dist-info"
    files = {
        **package,
        f"{dist_info}/METADATA": "Metadata-Version: 2.1\nName: pi-pilgrim\nVersion: 0.0.0\n",
        f"{dist_info}/WHEEL": "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    }
    record = []
    for path, text in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(text.encode()).digest()).rstrip(b"=").decode()
        record.append(f"{path},sha256={digest},{len(text.encode())}")
    files[f"{dist_info}/RECORD"] = "\n".join(record + [f"{dist_info}/RECORD,,"]) + "\n"
    wheel = directory / name
    with zipfile.ZipFile(wheel, "w") as archive:
        for path, text in files.items():
            archive.writestr(path, text)
    return wheel


# A pipilgrim that imports, but whose assembly doesn't close its shared components when it is closed.
_PILGRIM_THAT_DOESNT_CLOSE = """\
class PilgrimAssembly:
    def __init__(self):
        self._shared = {}

    def shared(self, factory):
        return self._shared.setdefault(factory.__code__, factory())

    def unshared(self, factory):
        return factory()

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        pass
"""


class TestSmokeWheel(unittest.TestCase):
    def test_refuses_anything_but_one_wheel_with_exit_status_2(self):
        for args in [[], ["dist/pi_pilgrim-0.3.0.tar.gz"], ["a.whl", "b.whl"]]:
            with self.subTest(args=args), mock.patch("sys.stderr", new_callable=io.StringIO) as err:
                self.assertEqual(smoke_wheel.main(args), 2)
                self.assertIn("Usage", err.getvalue())

    def test_fails_a_wheel_without_pipilgrim_even_with_a_checkout_on_pythonpath(self):
        # With the checkout on PYTHONPATH, and PYTHONOPTIMIZE skipping asserts, the checkout's pipilgrim would pass
        # for the wheel's if the check weren't isolated from them.
        with tempfile.TemporaryDirectory() as root:
            wheel = _wheel(Path(root), {})
            env = {**os.environ, "PYTHONPATH": str(_root), "PYTHONOPTIMIZE": "1", "TMPDIR": root}
            run = subprocess.run([sys.executable, str(_script), str(wheel)], env=env, capture_output=True, text=True)
            self.assertNotEqual(run.returncode, 0, run.stdout)
            self.assertIn("No module named 'pipilgrim'", run.stderr)

    def test_fails_a_wheel_whose_assembly_doesnt_close_even_with_pythonoptimize(self):
        # The check's asserts are what fail this wheel, so PYTHONOPTIMIZE, which would skip them, mustn't reach it.
        with tempfile.TemporaryDirectory() as root:
            wheel = _wheel(Path(root), {"pipilgrim/__init__.py": _PILGRIM_THAT_DOESNT_CLOSE})
            env = {**os.environ, "PYTHONOPTIMIZE": "1", "TMPDIR": root}
            run = subprocess.run([sys.executable, str(_script), str(wheel)], env=env, capture_output=True, text=True)
            self.assertNotEqual(run.returncode, 0, run.stdout)
            self.assertIn("closing the assembly didn't close its shared Map", run.stderr)


if __name__ == "__main__":
    unittest.main()
