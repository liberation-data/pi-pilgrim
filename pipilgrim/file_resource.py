from pathlib import Path
import os

from pipilgrim.instance_provider import InstanceProvider


class FileResource(InstanceProvider):

    def __init__(self, root_dir: str, path: str) -> str:
        self.rootDir = root_dir
        self.path = path

    def provide(self) -> str:
        return Path(self.rootDir + os.sep + self.path).read_text()
