import stat as stat_mod
import paramiko
from pathlib import Path
from typing import List
from .base import BaseClient
from ..models import RemoteFile
from ..utils.ignore import is_dir_ignored


def _make_transport(config) -> paramiko.Transport:
    t = paramiko.Transport((config.host, config.port))
    t.window_size = 3 * 1024 * 1024
    t.packetizer.REKEY_BYTES = pow(2, 40)
    t.packetizer.REKEY_PACKETS = pow(2, 40)
    t.connect(username=config.username, password=config.password)
    return t


class SFTPClient(BaseClient):
    def __init__(self, config, transport=None) -> None:
        super().__init__()
        self._config = config

        if transport is None:
            self._transport = _make_transport(config)
            self._owns = True
        else:
            self._transport = transport
            self._owns = False

        self.sftp = paramiko.SFTPClient.from_transport(self._transport)

    def clone(self) -> "SFTPClient":
        return SFTPClient(self._config, transport=self._transport)

    # ----------------------------------------------------- remote path check

    def remote_is_dir(self, path: str) -> bool:
        """Return True if *path* exists on the server and is a directory."""
        if path == "/":
            return True
        try:
            attr = self.sftp.stat(path)
            return stat_mod.S_ISDIR(attr.st_mode or 0)
        except Exception:
            return False

    # ----------------------------------------------------------- dir creation

    def _try_mkdir(self, path: str) -> None:
        try:
            self.sftp.mkdir(path)
        except IOError:
            pass

    # ----------------------------------------------------------------- listing

    def list_dir_flat(self, remote_dir: str, base: str) -> List[RemoteFile]:
        """List files in remote_dir (one level, no recursion), paths relative to base."""
        out: List[RemoteFile] = []
        try:
            for attr in self.sftp.listdir_attr(remote_dir):
                if stat_mod.S_ISDIR(attr.st_mode or 0):
                    continue
                full = f"{remote_dir.rstrip('/')}/{attr.filename}"
                rel = full[len(base):].lstrip("/")
                out.append(RemoteFile(
                    path=rel,
                    mtime=float(attr.st_mtime or 0),
                    size=attr.st_size or 0,
                ))
        except Exception:
            pass
        return out

    def list_files(self, remote_path: str, ignore_spec=None) -> List[RemoteFile]:
        out: List[RemoteFile] = []

        def walk(base: str, current: str) -> None:
            try:
                for attr in self.sftp.listdir_attr(current):
                    full = f"{current.rstrip('/')}/{attr.filename}"
                    rel = full[len(base):].lstrip("/")
                    if stat_mod.S_ISDIR(attr.st_mode or 0):
                        if ignore_spec and is_dir_ignored(ignore_spec, rel):
                            continue
                        walk(base, full)
                    else:
                        out.append(RemoteFile(
                            path=rel,
                            mtime=float(attr.st_mtime or 0),
                            size=attr.st_size or 0,
                        ))
            except IOError:
                return

        walk(remote_path, remote_path)
        return out

    # ----------------------------------------------------------------- upload

    def upload(self, local: Path, remote: str) -> None:
        if not self._transport.is_active():
            raise IOError("SSH transport closed — server may have timed out the connection")
        remote = self._pre_upload(remote)
        self.sftp.put(str(local), remote)

    # --------------------------------------------------------------- download

    def download(self, remote: str, local: Path) -> None:
        if not self._transport.is_active():
            raise IOError("SSH transport closed — server may have timed out the connection")
        local.parent.mkdir(parents=True, exist_ok=True)
        self.sftp.get(remote, str(local))

    # ------------------------------------------------------------------- close

    def close(self) -> None:
        try:
            self.sftp.close()
        except Exception:
            pass
        if self._owns:
            self._transport.close()
