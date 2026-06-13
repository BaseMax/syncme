from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Set
from ..models import RemoteFile


class BaseClient(ABC):
    def __init__(self) -> None:
        self._dir_cache: Set[str] = set()

    # ---------------------------------------------------------------- makedirs

    def makedirs(self, remote_dir: str) -> None:
        """Recursively create remote directories, skipping ones already seen."""
        parts = [p for p in remote_dir.replace("\\", "/").split("/") if p]
        current = ""
        for part in parts:
            current = f"/{part}" if not current else f"{current}/{part}"
            if current not in self._dir_cache:
                self._try_mkdir(current)
                self._dir_cache.add(current)

    def _pre_upload(self, remote: str) -> str:
        """Normalise the remote path and ensure its parent directory exists."""
        remote = remote.replace("\\", "/")
        parent = remote.rsplit("/", 1)[0] if "/" in remote else ""
        if parent:
            self.makedirs(parent)
        return remote

    # ------------------------------------------------------------- clone hook

    @abstractmethod
    def clone(self) -> "BaseClient":
        """Return a new client sharing expensive resources where possible.

        FTP:  opens a fresh TCP connection.
        SFTP: opens a new SFTP channel over the same SSH transport — no
              repeated handshake or authentication.
        """

    # ---------------------------------------------------------- abstract hooks

    @abstractmethod
    def _try_mkdir(self, path: str) -> None:
        """Create a single remote directory; silently ignore if it already exists."""

    @abstractmethod
    def remote_is_dir(self, path: str) -> bool:
        """Return True if *path* exists on the server and is a directory."""

    @abstractmethod
    def list_files(self, remote_path: str) -> List[RemoteFile]:
        pass

    @abstractmethod
    def upload(self, local: Path, remote: str) -> None:
        pass

    @abstractmethod
    def download(self, remote: str, local: Path) -> None:
        pass

    @abstractmethod
    def close(self) -> None:
        pass
