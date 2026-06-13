from datetime import datetime, timezone
from ftplib import FTP, error_perm
from pathlib import Path
from typing import List, Optional
from .base import BaseClient
from ..models import RemoteFile
from ..utils.ignore import is_dir_ignored

# 256 KB chunks — much faster than the 8 KB ftplib default on high-latency links.
_BLOCK_SIZE = 256 * 1024


def _join(current: str, name: str) -> str:
    """Join an FTP path segment without introducing double slashes."""
    return f"{current.rstrip('/')}/{name}"


def _parse_mlsd_time(modify: str) -> float:
    """Convert MLSD 'modify' fact (YYYYMMDDHHMMSS, UTC) to a Unix timestamp."""
    try:
        dt = datetime.strptime(modify[:14], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return 0.0


class FTPClient(BaseClient):
    def __init__(self, config) -> None:
        super().__init__()
        self._config = config
        self.ftp = FTP()
        self._mlsd_supported: bool = True  # cleared on first MLSD failure
        self._connect()

    # ----------------------------------------------------------------- connect

    def _connect(self) -> None:
        self.ftp = FTP()
        self.ftp.connect(self._config.host, self._config.port, timeout=20)
        self.ftp.login(self._config.username, self._config.password)
        self.ftp.set_pasv(True)

    def _reconnect(self) -> None:
        """Re-establish a dead FTP control connection."""
        try:
            self.ftp.close()
        except Exception:
            pass
        self._connect()

    def _ensure_connected(self) -> None:
        """Verify the control connection is alive; reconnect silently if not.

        Handles NoneType socket, timed-out object, and WinError 10054
        transparently before any data command is issued.
        """
        try:
            self.ftp.voidcmd("NOOP")
        except Exception:
            self._reconnect()

    # ------------------------------------------------------------------ clone

    def clone(self) -> "FTPClient":
        """Open a brand-new FTP connection using the same credentials."""
        c = FTPClient(self._config)
        c._mlsd_supported = self._mlsd_supported
        return c

    # ----------------------------------------------------- remote path check

    def remote_is_dir(self, path: str) -> bool:
        """Return True if *path* exists on the server and is a directory."""
        if path == "/":
            return True
        # Try MLST (RFC 3659): no CWD change, returns metadata about a path.
        try:
            resp = self.ftp.sendcmd(f"MLST {path}")
            lower = resp.lower()
            return "type=dir" in lower or "type=cdir" in lower
        except Exception:
            pass
        # Fall back to a CWD round-trip.
        try:
            orig = self.ftp.pwd()
            self.ftp.cwd(path)
            self.ftp.cwd(orig)
            return True
        except Exception:
            return False

    # ----------------------------------------------------------- dir creation

    def _try_mkdir(self, path: str) -> None:
        try:
            self.ftp.mkd(path)
        except error_perm:
            pass  # directory already exists

    # ----------------------------------------------------------------- listing

    def list_dir_flat(self, remote_dir: str, base: str) -> List[RemoteFile]:
        """List files in remote_dir (one level, no recursion), paths relative to base."""
        self._ensure_connected()
        if self._mlsd_supported:
            try:
                return self._flat_mlsd(remote_dir, base)
            except Exception:
                self._mlsd_supported = False
        return self._flat_list(remote_dir, base)

    def _flat_mlsd(self, remote_dir: str, base: str) -> List[RemoteFile]:
        out: List[RemoteFile] = []
        entries = list(self.ftp.mlsd(remote_dir, facts=["type", "size", "modify"]))
        for name, facts in entries:
            if not name or name in (".", ".."):
                continue
            if facts.get("type", "file") in ("dir", "cdir", "pdir"):
                continue
            full = _join(remote_dir, name)
            rel = full[len(base):].lstrip("/")
            out.append(RemoteFile(
                path=rel,
                mtime=_parse_mlsd_time(facts.get("modify", "")),
                size=int(facts.get("size", 0)),
            ))
        return out

    def _flat_list(self, remote_dir: str, base: str) -> List[RemoteFile]:
        entries: list = []

        def parse(line: str) -> None:
            parts = line.split(None, 8)
            if len(parts) >= 9 and not parts[0].startswith("d"):
                entries.append((parts[8].strip(), int(parts[4]) if parts[4].isdigit() else 0))

        try:
            self.ftp.retrlines(f"LIST {remote_dir}", parse)
        except Exception:
            return []

        out: List[RemoteFile] = []
        for name, size in entries:
            if name in (".", ".."):
                continue
            full = _join(remote_dir, name)
            rel = full[len(base):].lstrip("/")
            out.append(RemoteFile(path=rel, mtime=0.0, size=size))
        return out

    def list_files(self, remote_path: str, ignore_spec=None) -> List[RemoteFile]:
        files: List[RemoteFile] = []
        if self._mlsd_supported:
            try:
                self._collect_mlsd(remote_path, remote_path, files, ignore_spec)
                return files
            except Exception:
                self._mlsd_supported = False
                files = []
        self._collect_list(remote_path, remote_path, files, ignore_spec)
        return files

    def _collect_mlsd(
        self, base: str, current: str, out: List[RemoteFile], ignore_spec=None
    ) -> None:
        """Recursive listing via MLSD (RFC 3659) — provides real mtimes."""
        entries = list(self.ftp.mlsd(current, facts=["type", "size", "modify"]))
        for name, facts in entries:
            if not name or name in (".", ".."):
                continue
            entry_type = facts.get("type", "file")
            full = _join(current, name)
            rel = full[len(base):].lstrip("/")
            if entry_type == "dir":
                if ignore_spec and is_dir_ignored(ignore_spec, rel):
                    continue
                self._collect_mlsd(base, full, out, ignore_spec)
            elif entry_type not in ("cdir", "pdir"):
                out.append(RemoteFile(
                    path=rel,
                    mtime=_parse_mlsd_time(facts.get("modify", "")),
                    size=int(facts.get("size", 0)),
                ))

    def _collect_list(
        self, base: str, current: str, out: List[RemoteFile], ignore_spec=None
    ) -> None:
        """Recursive listing via LIST — fallback when MLSD unavailable (mtime=0)."""
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
            full = _join(current, name)
            rel = full[len(base):].lstrip("/")
            if is_dir:
                if ignore_spec and is_dir_ignored(ignore_spec, rel):
                    continue
                self._collect_list(base, full, out, ignore_spec)
            else:
                out.append(RemoteFile(path=rel, mtime=0.0, size=size))

    # ----------------------------------------------------------------- upload

    def upload(self, local: Path, remote: str) -> None:
        self._ensure_connected()
        remote = self._pre_upload(remote)
        with open(local, "rb") as f:
            self.ftp.storbinary(f"STOR {remote}", f, blocksize=_BLOCK_SIZE)

    # --------------------------------------------------------------- download

    def download(self, remote: str, local: Path) -> None:
        self._ensure_connected()
        local.parent.mkdir(parents=True, exist_ok=True)
        with open(local, "wb") as f:
            self.ftp.retrbinary(f"RETR {remote}", f.write, blocksize=_BLOCK_SIZE)

    # ------------------------------------------------------------------- close

    def close(self) -> None:
        try:
            self.ftp.quit()
        except Exception:
            self.ftp.close()
