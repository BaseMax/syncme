import stat as stat_mod
import paramiko
from pathlib import Path
from typing import List
from .base import BaseClient
from ..models import RemoteFile


class SFTPClient(BaseClient):
    def __init__(self, config) -> None:
        super().__init__()
        self._transport = paramiko.Transport((config.host, config.port))
        self._transport.connect(username=config.username, password=config.password)
        self.sftp = paramiko.SFTPClient.from_transport(self._transport)

    def _try_mkdir(self, path: str) -> None:
        try:
            self.sftp.mkdir(path)
        except IOError:
            pass

    def list_files(self, remote_path: str) -> List[RemoteFile]:
        files: List[RemoteFile] = []
        self._collect(remote_path, remote_path, files)
        return files

    def _collect(self, base: str, current: str, out: List[RemoteFile]) -> None:
        try:
            attrs = self.sftp.listdir_attr(current)
        except IOError:
            return

        for attr in attrs:
            full = f"{current}/{attr.filename}"
            rel = full[len(base):].lstrip("/")
            if stat_mod.S_ISDIR(attr.st_mode or 0):
                self._collect(base, full, out)
            else:
                out.append(RemoteFile(
                    path=rel,
                    mtime=float(attr.st_mtime or 0),
                    size=attr.st_size or 0,
                ))

    def upload(self, local: Path, remote: str) -> None:
        remote = self._pre_upload(remote)
        self.sftp.put(str(local), remote)

    def download(self, remote: str, local: Path) -> None:
        local.parent.mkdir(parents=True, exist_ok=True)
        self.sftp.get(remote, str(local))

    def close(self) -> None:
        for obj in (self.sftp, self._transport):
            try:
                obj.close()
            except Exception:
                pass
