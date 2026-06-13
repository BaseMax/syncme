from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from queue import Queue
from typing import Callable, Dict, List, Optional

from ..models import LocalFile, RemoteFile
from ..utils.ignore import build_ignore, is_ignored
from ..utils.logger import log_action, log_verbose, log_warning, log_error


class _Pool:
    """Fixed-size connection pool — one slot per worker thread."""

    def __init__(self, factory: Callable, size: int) -> None:
        self._q: Queue = Queue()
        self._clients: list = []
        for _ in range(size):
            c = factory()
            self._q.put(c)
            self._clients.append(c)

    def acquire(self):
        return self._q.get()

    def release(self, client) -> None:
        self._q.put(client)

    def close_all(self) -> None:
        for c in self._clients:
            try:
                c.close()
            except Exception:
                pass


class SyncEngine:
    def __init__(
        self,
        client,
        config,
        client_factory: Optional[Callable] = None,
        workers: int = 1,
        retries: int = 3,
    ) -> None:
        self.client = client
        self.config = config
        self.client_factory = client_factory
        self.workers = max(1, workers)
        self.retries = max(1, retries)
        self.ignore_spec = build_ignore(config.ignore)

    # ------------------------------------------------------------------- scan

    def _scan_local(self) -> List[LocalFile]:
        base = Path.cwd()
        result: List[LocalFile] = []

        def walk(directory: Path) -> None:
            try:
                entries = sorted(directory.iterdir(), key=lambda p: p.name)
            except PermissionError:
                return
            for entry in entries:
                rel = entry.relative_to(base).as_posix()
                if is_ignored(self.ignore_spec, rel):
                    continue
                if entry.is_dir():
                    walk(entry)
                elif entry.is_file():
                    st = entry.stat()
                    result.append(LocalFile(
                        path=entry, rel=rel,
                        mtime=st.st_mtime, size=st.st_size,
                    ))

        walk(base)
        return result

    # --------------------------------------------------------------- transfer

    def _upload_one(self, file: LocalFile, client, dry_run: bool) -> None:
        log_action("Uploading", file.rel)
        if dry_run:
            return
        for attempt in range(self.retries):
            try:
                client.upload(file.path, f"{self.config.remote_path}/{file.rel}")
                return
            except Exception as e:
                if attempt < self.retries - 1:
                    log_warning(f"    Retry {attempt + 1} — {file.rel}: {e}")
                else:
                    raise

    def _download_one(self, rel: str, client, dry_run: bool) -> None:
        log_action("Downloading", rel)
        if dry_run:
            return
        for attempt in range(self.retries):
            try:
                client.download(f"{self.config.remote_path}/{rel}", Path.cwd() / rel)
                return
            except Exception as e:
                if attempt < self.retries - 1:
                    log_warning(f"    Retry {attempt + 1} — {rel}: {e}")
                else:
                    raise

    # ---------------------------------------------------------- upload batch

    def _upload_batch(self, files: List[LocalFile], dry_run: bool) -> Dict[str, int]:
        """Upload a list of files, using a thread pool when workers > 1."""
        if not files:
            return {"uploaded": 0, "failed": 0}
        if self.workers > 1 and self.client_factory:
            return self._upload_parallel(files, dry_run)
        return self._upload_sequential(files, dry_run)

    def _upload_sequential(self, files: List[LocalFile], dry_run: bool) -> Dict[str, int]:
        stats: Dict[str, int] = {"uploaded": 0, "failed": 0}
        for file in files:
            try:
                self._upload_one(file, self.client, dry_run)
                stats["uploaded"] += 1
            except Exception as e:
                log_error(f"  Error: {e}")
                stats["failed"] += 1
        return stats

    def _upload_parallel(self, files: List[LocalFile], dry_run: bool) -> Dict[str, int]:
        results: List[bool] = []
        pool = _Pool(self.client_factory, self.workers)  # type: ignore[arg-type]
        try:
            def do(file: LocalFile) -> bool:
                client = pool.acquire()
                try:
                    self._upload_one(file, client, dry_run)
                    return True
                except Exception as e:
                    log_error(f"  Error: {e}")
                    return False
                finally:
                    pool.release(client)

            with ThreadPoolExecutor(max_workers=self.workers) as ex:
                results = list(ex.map(do, files))
        finally:
            pool.close_all()
        return {"uploaded": sum(results), "failed": results.count(False)}

    # ---------------------------------------------------------------- commands

    def push(self, dry_run: bool = False) -> Dict[str, int]:
        return self._upload_batch(self._scan_local(), dry_run)

    def pull(self, dry_run: bool = False) -> Dict[str, int]:
        stats: Dict[str, int] = {"downloaded": 0, "failed": 0, "skipped": 0}
        for rf in self.client.list_files(self.config.remote_path):
            if is_ignored(self.ignore_spec, rf.path):
                log_verbose(f"Skipping {rf.path}")
                stats["skipped"] += 1
                continue
            try:
                self._download_one(rf.path, self.client, dry_run)
                stats["downloaded"] += 1
            except Exception as e:
                log_error(f"  Error: {e}")
                stats["failed"] += 1
        return stats

    def auto(self, force: bool = False, dry_run: bool = False) -> Dict[str, int]:
        local_files = self._scan_local()
        remote_map: Dict[str, RemoteFile] = {
            f.path: f for f in self.client.list_files(self.config.remote_path)
        }

        to_upload: List[LocalFile] = []
        skipped = 0
        for file in local_files:
            remote = remote_map.get(file.rel)
            if force or not remote or file.mtime > remote.mtime:
                to_upload.append(file)
            else:
                log_verbose(f"Skipping {file.rel} (up to date)")
                skipped += 1

        stats = self._upload_batch(to_upload, dry_run)
        stats["skipped"] = skipped
        return stats
