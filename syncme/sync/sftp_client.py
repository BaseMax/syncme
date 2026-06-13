import stat as stat_mod
import paramiko
from pathlib import Path
from typing import List
from .base import BaseClient
from ..models import RemoteFile


def _make_transport(config) -> paramiko.Transport:
    t = paramiko.Transport((config.host, config.port))
    # Increase SSH window to reduce flow-control stalls on fast links.
    t.window_size = 3 * 1024 * 1024
    # Suppress mid-transfer rekeying - each rekey adds ~100 ms stall.
    t.packetizer.REKEY_BYTES = pow(2, 40)
    t.packetizer.REKEY_PACKETS = pow(2, 40)
    t.connect(username=config.username, password=config.password)
    return t


class SFTPClient(BaseClient):
    def __init__(self, config, transport: "paramiko.Transport | None" = None) -> None:
        super().__init__()
        self._config = config
        if transport is None:
            self._transport = _make_transport(config)
            self._owns_transport = True
        else:
            # Cloned clients share the existing transport - zero SSH handshake cost.
            self._transport = transport
            self._owns_transport = False
        self.sftp = paramiko.SFTPClient.from_transport(self._transport)

    # ------------------------------------------------------------------ clone

    def clone(self) -> "SFTPClient":
        """Open a new SFTP channel over the shared transport.

        Cost: ~1 ms channel negotiation, versus ~300 ms for a full SSH reconnect.
        All 20 workers therefore share one TCP connection and one authentication.
        """
        return SFTPClient(self._config, transport=self._transport)

    # ----------------------------------------------------------- dir creation

    def _try_mkdir(self, path: str) -> None:
        try:
            self.sftp.mkdir(path)
        except IOError:
            pass  # directory already exists

    # ----------------------------------------------------------------- listing

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

    # ----------------------------------------------------------------- upload

    def upload(self, local: Path, remote: str) -> None:
        remote = self._pre_upload(remote)
        self.sftp.put(str(local), remote)

    # --------------------------------------------------------------- download

    def download(self, remote: str, local: Path) -> None:
        local.parent.mkdir(parents=True, exist_ok=True)
        self.sftp.get(remote, str(local))

    # ------------------------------------------------------------------- close

    def close(self) -> None:
        try:
            self.sftp.close()
        except Exception:
            pass
        if self._owns_transport:
            try:
                self._transport.close()
            except Exception:
                pass
