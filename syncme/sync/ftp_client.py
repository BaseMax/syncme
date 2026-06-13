from ftplib import FTP, error_perm
from pathlib import Path
from typing import List
from .base import BaseClient
from ..models import RemoteFile


class FTPClient(BaseClient):
    def __init__(self, config) -> None:
        super().__init__()
        self.ftp = FTP()
        self.ftp.connect(config.host, config.port, timeout=30)
        self.ftp.login(config.username, config.password)
        self.ftp.set_pasv(True)

    def _try_mkdir(self, path: str) -> None:
        try:
            self.ftp.mkd(path)
        except error_perm:
            pass

    def list_files(self, remote_path: str) -> List[RemoteFile]:
        files: List[RemoteFile] = []
        self._collect(remote_path, remote_path, files)
        return files

    def _collect(self, base: str, current: str, out: List[RemoteFile]) -> None:
        entries: list = []

        def parse(line: str) -> None:
            parts = line.split(None, 8)
            if len(parts) >= 9:
                entries.append((
                    parts[0].startswith("d"),
                    parts[8].strip(),
                    int(parts[4]) if parts[4].isdigit() else 0,
                ))

        try:
            self.ftp.retrlines(f"LIST {current}", parse)
        except Exception:
            return

        for is_dir, name, size in entries:
            if name in (".", ".."):
                continue
            full = f"{current}/{name}"
            rel = full[len(base):].lstrip("/")
            if is_dir:
                self._collect(base, full, out)
            else:
                out.append(RemoteFile(path=rel, mtime=0, size=size))

    def upload(self, local: Path, remote: str) -> None:
        remote = self._pre_upload(remote)
        with open(local, "rb") as f:
            self.ftp.storbinary(f"STOR {remote}", f)

    def download(self, remote: str, local: Path) -> None:
        local.parent.mkdir(parents=True, exist_ok=True)
        with open(local, "wb") as f:
            self.ftp.retrbinary(f"RETR {remote}", f.write)

    def close(self) -> None:
        try:
            self.ftp.quit()
        except Exception:
            self.ftp.close()
