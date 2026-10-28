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


def _wheel_without_pipilgrim(directory: Path) -> Path:
    # A wheel pip installs, but that has no pipilgrim in it: the package left out of the build.
    name, dist_info = "pi_pilgrim-0.0.0-py3-none-any.whl", "pi_pilgrim-0.0.0.dist-info"
    files = {
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
            wheel = _wheel_without_pipilgrim(Path(root))
            env = {**os.environ, "PYTHONPATH": str(_root), "PYTHONOPTIMIZE": "1", "TMPDIR": root}
            run = subprocess.run([sys.executable, str(_script), str(wheel)], env=env, capture_output=True, text=True)
            self.assertNotEqual(run.returncode, 0, run.stdout)
            self.assertIn("No module named 'pipilgrim'", run.stderr)


if __name__ == "__main__":
    unittest.main()
