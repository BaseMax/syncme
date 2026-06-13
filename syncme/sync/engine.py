import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from queue import Queue
from typing import Dict, FrozenSet, List, Set, Tuple

from ..models import LocalFile, RemoteFile
from ..utils.ignore import build_ignore, is_ignored
from ..utils.logger import (
    log_info, log_verbose, log_warning, log_error,
    log_file_ok, log_file_fail, log_file_skip, log_file_retry, log_checking,
    fmt_size, fmt_dt,
)

# Set by KeyboardInterrupt; workers check this before starting each task.
_shutdown = threading.Event()


class _Progress:
    """Thread-safe completion counter that produces '[n/total]' tags."""

    def __init__(self, total: int) -> None:
        self._lock = threading.Lock()
        self._done = 0
        self.total = total

    def tick(self) -> str:
        with self._lock:
            self._done += 1
            return f"[{self._done}/{self.total}]"


class _Pool:
    """Fixed-size connection pool backed by a thread-safe Queue."""

    def __init__(
        self,
        clone_fn,
        size: int,
        known_dirs: FrozenSet[str] = frozenset(),
        seed_client=None,
    ) -> None:
        self._q: Queue = Queue()
        self._clients: list = []
        self._seed_client = seed_client

        # The seed client (engine's main connection) joins the pool so that
        # total connections = workers instead of workers + 1, keeping within
        # the server's per-IP connection limit.
        if seed_client is not None:
            seed_client._dir_cache.update(known_dirs)
            self._q.put(seed_client)
            self._clients.append(seed_client)

        if size > 0:
            with ThreadPoolExecutor(max_workers=size) as ex:
                futures = [ex.submit(clone_fn) for _ in range(size)]

            failed = 0
            for f in futures:
                try:
                    c = f.result()
                    c._dir_cache.update(known_dirs)
                    self._q.put(c)
                    self._clients.append(c)
                except Exception as e:
                    failed += 1
                    log_warning(f"Pool connection failed: {e}")

            if failed > 0:
                log_warning(
                    f"Pool degraded: {len(self._clients)} of "
                    f"{len(self._clients) + failed} connection(s) available. "
                    "Reduce workers in config if this persists."
                )

        if not self._clients:
            raise RuntimeError(
                "No pool connections available — check host/port/credentials."
            )

    def acquire(self):
        return self._q.get()

    def release(self, client) -> None:
        self._q.put(client)

    def close_all(self) -> None:
        for c in self._clients:
            if c is self._seed_client:
                continue  # owned by _session; don't double-close
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
        # Pre-populate the dir cache with every component of remote_path so
        # makedirs never tries to MKD them.  On chrooted FTP accounts (cPanel,
        # Plesk) the session root / maps to the account home, so attempting
        # MKD /home/user/public_html would create a nested home/ inside the
        # account root — duplicating the full path tree inside itself.
        self._seed_remote_base()

    # ----------------------------------------------------------------- helpers

    def _remote(self, rel: str) -> str:
        """Build the absolute remote path for a local-relative path."""
        return f"{self.config.remote_path.rstrip('/')}/{rel}"

    def _seed_remote_base(self) -> None:
        """Mark every component of remote_path as already-existing in the cache."""
        parts = [p for p in self.config.remote_path.replace("\\", "/").split("/") if p]
        current = ""
        for part in parts:
            current = f"/{part}" if not current else f"{current}/{part}"
            self.client._dir_cache.add(current)

    # --------------------------------------------------------- remote path check

    def verify_connection(self) -> None:
        """Verify that remote_path exists on the server; raise RuntimeError if not."""
        path = self.config.remote_path
        if path == "/":
            return  # root always exists
        if not self.client.remote_is_dir(path):
            raise RuntimeError(
                f"Remote path not found: {path}\n"
                f"  → Create it on the server first, or update remote_path in .syncme.yaml"
            )

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
                        path=entry,
                        rel=rel,
                        mtime=st.st_mtime,
                        size=st.st_size,
                    ))

        walk(base)
        log_info(f"Found {len(result)} local file(s).")
        return result

    # ----------------------------------------------------------------- upload

    def _upload_one(
        self,
        file: LocalFile,
        client,
        dry_run: bool,
        progress: "_Progress | None" = None,
    ) -> bool:
        if dry_run:
            tag = progress.tick() if progress else ""
            log_file_ok(file.rel, file.size, tag)
            return True

        for attempt in range(self.retries):
            try:
                client.upload(file.path, self._remote(file.rel))
                tag = progress.tick() if progress else ""
                log_file_ok(file.rel, file.size, tag)
                return True
            except Exception as e:
                if attempt < self.retries - 1:
                    log_file_retry(file.rel, attempt + 1, str(e))
                else:
                    tag = progress.tick() if progress else ""
                    log_file_fail(file.rel, str(e), tag)
                    return False

        return False

    def _pre_create_dirs(self, files: List[LocalFile]) -> None:
        """Serial pass before the parallel flood — workers never stall on makedirs."""
        seen: Set[str] = set()
        for file in files:
            parent = self._remote(file.rel).rsplit("/", 1)[0]
            if not parent or parent == self.config.remote_path or parent in seen:
                continue
            seen.add(parent)
            try:
                self.client.makedirs(parent)
            except Exception as e:
                log_warning(f"mkdir failed: {parent} → {e}")
        log_verbose(f"Directories prepared: {len(seen)}")

    def _upload_parallel(
        self, files: List[LocalFile], dry_run: bool
    ) -> Dict[str, int]:
        _shutdown.clear()
        ordered = sorted(files, key=lambda f: f.size, reverse=True)

        if not dry_run:
            self._pre_create_dirs(ordered)

        # Snapshot the dir cache (remote_path components + created dirs) and
        # inject it into every pool client — makedirs calls become pure cache
        # lookups during the parallel phase, zero extra round-trips.
        known_dirs: FrozenSet[str] = frozenset(self.client._dir_cache)

        # Reuse main connection as the first pool slot.
        # Total connections = self.workers (1 seed + workers-1 clones).
        n_clones = max(0, self.workers - 1)
        pool = _Pool(self.client.clone, n_clones, known_dirs, seed_client=self.client)

        log_info(
            f"Uploading {len(ordered)} file(s) with "
            f"{len(pool._clients)} parallel connection(s)..."
        )

        progress = _Progress(len(ordered))
        results: List[Tuple[bool, int]] = []

        def task(file: LocalFile) -> Tuple[bool, int]:
            if _shutdown.is_set():
                return False, 0
            client = pool.acquire()
            try:
                ok = self._upload_one(file, client, dry_run, progress)
                return ok, (file.size if ok else 0)
            finally:
                pool.release(client)

        ex = ThreadPoolExecutor(max_workers=len(pool._clients))
        futures = {ex.submit(task, f): f for f in ordered}
        try:
            for fut in as_completed(futures):
                results.append(fut.result())
        except KeyboardInterrupt:
            _shutdown.set()
            for fut in futures:
                fut.cancel()
            log_warning("\nInterrupted — waiting for active uploads to finish...")
            raise
        finally:
            ex.shutdown(wait=True)
            pool.close_all()

        return {
            "uploaded": sum(ok for ok, _ in results),
            "failed": sum(not ok for ok, _ in results),
            "bytes": sum(sz for _, sz in results),
        }

    def _upload_batch(
        self, files: List[LocalFile], dry_run: bool
    ) -> Dict[str, int]:
        if not files:
            log_info("Nothing to upload.")
            return {"uploaded": 0, "failed": 0, "bytes": 0}

        if self.workers > 1:
            return self._upload_parallel(files, dry_run)

        progress = _Progress(len(files))
        stats = {"uploaded": 0, "failed": 0, "bytes": 0}
        for f in files:
            ok = self._upload_one(f, self.client, dry_run, progress)
            if ok:
                stats["uploaded"] += 1
                stats["bytes"] += f.size
            else:
                stats["failed"] += 1
        return stats

    # ---------------------------------------------------------------- commands

    def push(self, dry_run: bool = False) -> Dict[str, int]:
        t0 = time.monotonic()
        stats = self._upload_batch(self._scan_local(), dry_run)
        stats["elapsed"] = time.monotonic() - t0
        return stats

    def pull(self, dry_run: bool = False) -> Dict[str, int]:
        t0 = time.monotonic()
        stats = {"downloaded": 0, "failed": 0, "skipped": 0, "bytes": 0}
        progress = _Progress(0)  # total unknown until listing done; updated below

        remote_files = self.client.list_files(self.config.remote_path)
        visible = [rf for rf in remote_files if not is_ignored(self.ignore_spec, rf.path)]
        skipped = len(remote_files) - len(visible)
        progress = _Progress(len(visible))

        for rf in visible:
            try:
                if not dry_run:
                    self.client.download(self._remote(rf.path), Path.cwd() / rf.path)
                tag = progress.tick()
                log_file_ok(rf.path, rf.size, tag)
                stats["downloaded"] += 1
                stats["bytes"] += rf.size
            except Exception as e:
                tag = progress.tick()
                log_file_fail(rf.path, str(e), tag)
                stats["failed"] += 1

        stats["skipped"] = skipped
        stats["elapsed"] = time.monotonic() - t0
        return stats

    def auto(self, force: bool = False, dry_run: bool = False) -> Dict[str, int]:
        t0 = time.monotonic()
        local_files = self._scan_local()

        log_info("Listing remote files...")
        remote_map: Dict[str, RemoteFile] = {
            f.path: f for f in self.client.list_files(self.config.remote_path)
        }
        log_verbose(f"Found {len(remote_map)} remote file(s).")

        to_upload: List[LocalFile] = []
        skipped = 0

        for file in local_files:
            remote = remote_map.get(file.rel)

            if force or not remote:
                needs_upload = True
            elif remote.mtime > 0:
                # Real timestamps available (SFTP or FTP with MLSD).
                needs_upload = file.mtime > remote.mtime
            else:
                # Only sizes available (FTP LIST fallback).
                # Same size → assume unchanged; different → re-upload.
                needs_upload = file.size != remote.size

            if needs_upload:
                if not force:
                    remote_dt = fmt_dt(remote.mtime) if remote else "none"
                    remote_sz = fmt_size(remote.size) if remote else "0 B"
                    log_checking(
                        file.rel,
                        fmt_dt(file.mtime), fmt_size(file.size),
                        remote_dt, remote_sz,
                        "upload",
                    )
                to_upload.append(file)
            else:
                log_file_skip(
                    file.rel,
                    f"{fmt_dt(file.mtime)}  {fmt_size(file.size)}",
                )
                skipped += 1

        stats = self._upload_batch(to_upload, dry_run)
        stats["skipped"] = skipped
        stats["elapsed"] = time.monotonic() - t0
        return stats
