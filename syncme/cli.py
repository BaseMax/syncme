from contextlib import contextmanager
from typing import Generator, Optional

import typer

from .constants import VERSION
from .config import load_config, init_config
from .sync.engine import SyncEngine
from .sync.ftp_client import FTPClient
from .sync.sftp_client import SFTPClient
from .utils.logger import (
    log, log_info, log_success, log_warning, log_error,
    set_verbose, set_quiet, fmt_size,
)

app = typer.Typer(
    help="Sync local files to a remote server via FTP or SFTP.",
    no_args_is_help=True,
)

# Shared option definitions. workers=None means "read from config" (default 20).
_DRY_RUN = typer.Option(False,  "--dry-run", "-n", help="Preview transfers without executing them.")
_WORKERS  = typer.Option(None,  "--workers", "-w", help="Parallel connections. Default: workers field in .syncme.yaml.")
_RETRIES  = typer.Option(3,     "--retries", "-r", help="Retry count per file on failure.")
_VERBOSE  = typer.Option(False, "--verbose",       help="Show skipped files and comparison details.")
_QUIET    = typer.Option(False, "--quiet",   "-q", help="Suppress all output except errors.")


# ------------------------------------------------------------------ helpers

def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"syncme {VERSION}")
        raise typer.Exit()


def create_client(config):
    if config.protocol == "ftp":
        return FTPClient(config)
    if config.protocol == "sftp":
        return SFTPClient(config)
    raise ValueError(f"Unsupported protocol: {config.protocol!r}")


def _fmt_summary(stats: dict, verb: str, dry_run: bool) -> str:
    """Build the final summary line including bytes transferred and speed."""
    _base = {"Uploaded": "upload", "Downloaded": "download"}
    action = f"Would {_base.get(verb, verb.lower())}" if dry_run else verb
    n = stats.get("uploaded", stats.get("downloaded", 0))
    byt = stats.get("bytes", 0)
    elapsed = stats.get("elapsed", 0.0)

    parts = [f"{n} file(s)"]
    if byt > 0:
        parts.append(fmt_size(byt))
    if elapsed > 0.1 and not dry_run:
        parts.append(f"{elapsed:.1f}s")
        if byt > 0 and elapsed > 0:
            parts.append(f"{fmt_size(int(byt / elapsed))}/s")

    failed = stats.get("failed", 0)
    skipped = stats.get("skipped")
    tail = f"  Failed: {failed}."
    if skipped is not None:
        tail = f"  Skipped: {skipped}.{tail}"

    return f"\n{action} {'  '.join(parts)}.{tail}"


@contextmanager
def _session(
    verbose: bool,
    quiet: bool,
    dry_run: bool,
    workers: Optional[int],
    retries: int,
) -> Generator[SyncEngine, None, None]:
    """Load config, verify remote path, yield a ready engine, close on exit."""
    set_verbose(verbose)
    set_quiet(quiet)

    try:
        config = load_config()
    except FileNotFoundError as e:
        log_error(str(e))
        raise typer.Exit(1)

    if dry_run:
        log("[yellow]Dry run — no files will be transferred.[/yellow]")

    log_info(
        f"{config.protocol.upper()}  {config.host}:{config.port}"
        f"  →  {config.remote_path}"
    )

    effective_workers = workers if workers is not None else config.workers
    client = create_client(config)
    engine = SyncEngine(client, config, workers=effective_workers, retries=retries)

    try:
        engine.verify_connection()
        yield engine
    except typer.Exit:
        raise
    except KeyboardInterrupt:
        log_warning("\nInterrupted.")
        raise typer.Exit(0)
    except Exception as e:
        log_error(str(e))
        raise typer.Exit(1)
    finally:
        client.close()


# ----------------------------------------------------------------- commands

@app.callback()
def _main(
    version: Optional[bool] = typer.Option(
        None, "--version", "-V",
        callback=_version_callback, is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    pass


@app.command()
def init() -> None:
    """Create a .syncme.yaml config file in the current directory."""
    init_config()


@app.command()
def push(
    dry_run: bool = _DRY_RUN,
    workers: Optional[int] = _WORKERS,
    retries: int = _RETRIES,
    verbose: bool = _VERBOSE,
    quiet: bool = _QUIET,
) -> None:
    """Upload all local files to the remote server."""
    with _session(verbose, quiet, dry_run, workers, retries) as engine:
        stats = engine.push(dry_run=dry_run)
        log_success(_fmt_summary(stats, "Uploaded", dry_run))


@app.command()
def pull(
    dry_run: bool = _DRY_RUN,
    retries: int = _RETRIES,
    verbose: bool = _VERBOSE,
    quiet: bool = _QUIET,
) -> None:
    """Download all remote files to the local directory."""
    with _session(verbose, quiet, dry_run, workers=None, retries=retries) as engine:
        stats = engine.pull(dry_run=dry_run)
        log_success(_fmt_summary(stats, "Downloaded", dry_run))


@app.command()
def auto(
    force: bool = typer.Option(False, "--force", "-f", help="Upload all files, ignoring timestamps."),
    dry_run: bool = _DRY_RUN,
    workers: Optional[int] = _WORKERS,
    retries: int = _RETRIES,
    verbose: bool = _VERBOSE,
    quiet: bool = _QUIET,
) -> None:
    """Smart sync: upload only new or modified local files."""
    with _session(verbose, quiet, dry_run, workers, retries) as engine:
        stats = engine.auto(force=force, dry_run=dry_run)
        log_success(_fmt_summary(stats, "Uploaded", dry_run))


if __name__ == "__main__":
    app()
