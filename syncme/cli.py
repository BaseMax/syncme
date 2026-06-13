from typing import Optional

import typer

from .constants import VERSION
from .config import load_config, init_config
from .sync.engine import SyncEngine
from .sync.ftp_client import FTPClient
from .sync.sftp_client import SFTPClient
from .utils.logger import log, log_success, log_error, set_verbose, set_quiet

app = typer.Typer(
    help="Sync local files to a remote server via FTP or SFTP.",
    no_args_is_help=True,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"syncme {VERSION}")
        raise typer.Exit()


def create_client(config):
    if config.protocol == "ftp":
        return FTPClient(config)
    elif config.protocol == "sftp":
        return SFTPClient(config)
    else:
        raise ValueError(f"Unsupported protocol: {config.protocol!r}")


@app.callback()
def _main(
    version: Optional[bool] = typer.Option(
        None,
        "--version",
        "-V",
        callback=_version_callback,
        is_eager=True,
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
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Show what would be uploaded without transferring."),
    workers: int = typer.Option(1, "--workers", "-w", help="Parallel upload threads (uses multiple connections)."),
    retries: int = typer.Option(3, "--retries", "-r", help="Retry count per file on failure."),
    verbose: bool = typer.Option(False, "--verbose", help="Show skipped files."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress all output except errors."),
) -> None:
    """Upload all local files to the remote server."""
    set_verbose(verbose)
    set_quiet(quiet)

    try:
        config = load_config()
    except FileNotFoundError as e:
        log_error(str(e))
        raise typer.Exit(1)

    client = create_client(config)
    engine = SyncEngine(
        client,
        config,
        client_factory=lambda: create_client(config),
        workers=workers,
        retries=retries,
    )

    try:
        if dry_run:
            log("[yellow]Dry run — no files will be transferred.[/yellow]")
        stats = engine.push(dry_run=dry_run)
        verb = "Would upload" if dry_run else "Uploaded"
        log_success(f"\n{verb} {stats['uploaded']} file(s).  Failed: {stats['failed']}.")
    except Exception as e:
        log_error(f"Push failed: {e}")
        raise typer.Exit(1)
    finally:
        client.close()


@app.command()
def pull(
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Show what would be downloaded without transferring."),
    retries: int = typer.Option(3, "--retries", "-r", help="Retry count per file on failure."),
    verbose: bool = typer.Option(False, "--verbose", help="Show skipped files."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress all output except errors."),
) -> None:
    """Download all remote files to the local directory."""
    set_verbose(verbose)
    set_quiet(quiet)

    try:
        config = load_config()
    except FileNotFoundError as e:
        log_error(str(e))
        raise typer.Exit(1)

    client = create_client(config)
    engine = SyncEngine(client, config, retries=retries)

    try:
        if dry_run:
            log("[yellow]Dry run — no files will be transferred.[/yellow]")
        stats = engine.pull(dry_run=dry_run)
        verb = "Would download" if dry_run else "Downloaded"
        log_success(
            f"\n{verb} {stats['downloaded']} file(s).  "
            f"Skipped: {stats['skipped']}.  Failed: {stats['failed']}."
        )
    except Exception as e:
        log_error(f"Pull failed: {e}")
        raise typer.Exit(1)
    finally:
        client.close()


@app.command()
def auto(
    force: bool = typer.Option(False, "--force", "-f", help="Upload all files, ignoring timestamps."),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Show what would be synced without transferring."),
    workers: int = typer.Option(1, "--workers", "-w", help="Parallel upload threads (uses multiple connections)."),
    retries: int = typer.Option(3, "--retries", "-r", help="Retry count per file on failure."),
    verbose: bool = typer.Option(False, "--verbose", help="Show skipped files."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress all output except errors."),
) -> None:
    """Smart sync: upload only new or modified local files."""
    set_verbose(verbose)
    set_quiet(quiet)

    try:
        config = load_config()
    except FileNotFoundError as e:
        log_error(str(e))
        raise typer.Exit(1)

    client = create_client(config)
    engine = SyncEngine(
        client,
        config,
        client_factory=lambda: create_client(config),
        workers=workers,
        retries=retries,
    )

    try:
        if dry_run:
            log("[yellow]Dry run — no files will be transferred.[/yellow]")
        stats = engine.auto(force=force, dry_run=dry_run)
        verb = "Would upload" if dry_run else "Uploaded"
        log_success(
            f"\n{verb} {stats['uploaded']} file(s).  "
            f"Skipped: {stats['skipped']}.  Failed: {stats['failed']}."
        )
    except Exception as e:
        log_error(f"Auto sync failed: {e}")
        raise typer.Exit(1)
    finally:
        client.close()


if __name__ == "__main__":
    app()
