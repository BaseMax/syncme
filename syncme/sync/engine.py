from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from queue import Queue
from typing import Dict, FrozenSet, List, Set

from ..models import LocalFile, RemoteFile
from ..utils.ignore import build_ignore, is_ignored
from ..utils.logger import log_action, log_verbose, log_warning, log_error


class _Pool:
    """
    Fixed-size connection pool.

    All connections are opened in parallel (warm-up), so setup latency equals
    one connection's time rather than N times that.  The pre-built dir-cache is
    injected into every client so pool workers never issue a makedirs network
    call — they hit the local set and move straight to the file transfer.
    """

    def __init__(self, clone_fn, size: int, known_dirs: FrozenSet[str] = frozenset()) -> None:
        self._q: Queue = Queue()
        self._clients: list = []

        # Open all connections concurrently — critical for FTP where each
        # connection needs a TCP handshake + login round-trip.
        with ThreadPoolExecutor(max_workers=size) as ex:
            futures = [ex.submit(clone_fn) for _ in range(size)]

        for f in futures:
            try:
                c = f.result()
                c._dir_cache.update(known_dirs)   # skip makedirs network calls
                self._q.put(c)
                self._clients.append(c)
            except Exception as e:
                log_error(f"  Pool: failed to open connection: {e}")

        if not self._clients:
            raise RuntimeError("Could not open any pool connections.")

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
    def __init__(self, client, config, workers: int = 20, retries: int = 3) -> None:
        self.client = client
        self.config = config
        self.workers = max(1, workers)
        self.retries = max(1, retries)
        self.ignore_spec = build_ignore(config.ignore)


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


    def _pre_create_dirs(self, files: List[LocalFile]) -> None:
        """
        Create every remote directory that will be needed, in a single serial
        pass on the main connection, before the parallel flood starts.

        Workers then inherit a pre-populated dir-cache and skip all makedirs
        network calls entirely, going straight to the data transfer.
        """
        seen: Set[str] = set()
        for file in files:
            parent = f"{self.config.remote_path}/{file.rel}".rsplit("/", 1)[0]
            if parent and parent not in seen:
                seen.add(parent)
                self.client.makedirs(parent)


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


    def _upload_batch(self, files: List[LocalFile], dry_run: bool) -> Dict[str, int]:
        if not files:
            return {"uploaded": 0, "failed": 0}
        if self.workers > 1:
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
        # Largest-first (LPT rule): maximises worker utilisation by ensuring the
        # longest jobs start immediately, so short files fill in the tail gaps.
        ordered = sorted(files, key=lambda f: f.size, reverse=True)

        # Pre-create all remote directories once on the main connection,
        # then snapshot the cache so pool workers inherit it instantly.
        if not dry_run:
            self._pre_create_dirs(ordered)
        known_dirs: FrozenSet[str] = frozenset(self.client._dir_cache)

        results: List[bool] = []
        pool = _Pool(self.client.clone, self.workers, known_dirs)
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

            # Use as_completed so a worker picks up the next file the instant
            # it finishes — no batch boundaries, no idle time between waves.
            with ThreadPoolExecutor(max_workers=len(pool._clients)) as ex:
                future_map = {ex.submit(do, f): f for f in ordered}
                results = [fut.result() for fut in as_completed(future_map)]
        finally:
            pool.close_all()

        return {"uploaded": sum(results), "failed": results.count(False)}


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
