"""
Installs a built wheel into a clean virtual environment, outside the repository, and checks that it works: pipilgrim
imports from the wheel, and an assembly makes its components with their lifecycles and closes the shared ones. The
release workflow runs it on the wheel it publishes:

    python scripts/smoke_wheel.py dist/pi_pilgrim-X.Y.Z-py3-none-any.whl

It reads the standard library alone, so nothing is installed for it but the wheel, into the environment it makes.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import venv
from pathlib import Path

CHECK = """\
import sys

import pipilgrim
from pipilgrim import PilgrimAssembly

# pipilgrim is imported from the environment's site-packages, from the wheel, and not from a checkout.
assert pipilgrim.__file__.startswith(sys.prefix), pipilgrim.__file__


class Map:
    closed = False

    def close(self) -> None:
        self.closed = True


class Quest:
    def __init__(self, map: Map) -> None:
        self.map = map


class Knight:
    def __init__(self, quest: Quest) -> None:
        self.quest = quest


class QuestAssembly(PilgrimAssembly):
    def knight(self) -> Knight:
        return self.unshared(lambda: Knight(quest=self.quest()))

    def quest(self) -> Quest:
        return self.shared(lambda: Quest(map=self.map()))

    def map(self) -> Map:
        return self.shared(lambda: Map())


with QuestAssembly() as assembly:
    first, second = assembly.knight(), assembly.knight()
    assert first is not second, "unshared made one Knight"
    assert first.quest is second.quest, "shared made two Quests"
    shared_map = first.quest.map
assert shared_map.closed, "closing the assembly didn't close its shared Map"
print(pipilgrim.__file__)
"""


def main(argv: list[str]) -> int:
    if len(argv) != 1 or not argv[0].endswith(".whl"):
        print("Usage: python scripts/smoke_wheel.py <the wheel>", file=sys.stderr)
        return 2
    wheel = Path(argv[0]).resolve()
    with tempfile.TemporaryDirectory(prefix="pi-pilgrim-smoke-") as root:
        home = Path(root)
        # Linked to the interpreter, as `python -m venv` makes it, not copied: a copy of uv's relocatable Python can't
        # find its libpython, beside the original, and dies.
        venv.create(home / "venv", with_pip=True, symlinks=sys.platform != "win32")
        python = home / "venv" / ("Scripts" if sys.platform == "win32" else "bin") / "python"
        subprocess.run([python, "-m", "pip", "install", "--quiet", "--no-input", "--disable-pip-version-check", str(wheel)], check=True)
        # Run from the empty directory, isolated (-I), so that a checkout's pipilgrim can't be imported in the wheel's
        # place: -I leaves out the current directory and the user's site-packages, and ignores PYTHON* variables, so
        # neither PYTHONPATH nor PYTHONOPTIMIZE, which would skip the asserts, lets a broken wheel pass.
        subprocess.run([python, "-I", "-c", CHECK], check=True, cwd=home)
    print(f"{wheel.name}: installs, imports, and makes and closes an assembly")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
