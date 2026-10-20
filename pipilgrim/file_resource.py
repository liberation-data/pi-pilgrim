from pathlib import Path

from pipilgrim.instance_provider import InstanceProvider


class FileResource(InstanceProvider[str]):
    """
    Provides the text of a file, relative to a root directory: often `os.path.dirname(__file__)`.
    """

    def __init__(self, root_dir: str, path: str, encoding: str = "utf-8"):
        self.root_dir = root_dir
        self.path = path
        self.encoding = encoding

    def provide(self) -> str:
        return (Path(self.root_dir) / self.path).read_text(encoding=self.encoding)
