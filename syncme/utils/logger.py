from datetime import datetime
from rich.console import Console

_console = Console()
_err_console = Console(stderr=True)
_verbose = False
_quiet = False


def set_verbose(v: bool) -> None:
    global _verbose
    _verbose = v


def set_quiet(q: bool) -> None:
    global _quiet
    _quiet = q


# ------------------------------------------------------------------ formatters

def fmt_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def fmt_dt(ts: float) -> str:
    if not ts:
        return "none"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


# ------------------------------------------------------------------ log levels

def log(msg: str) -> None:
    if not _quiet:
        _console.print(msg, highlight=False)


def log_info(msg: str) -> None:
    if not _quiet:
        _console.print(f"[dim]{msg}[/dim]", highlight=False)


def log_verbose(msg: str) -> None:
    if _verbose and not _quiet:
        _console.print(f"[dim]  {msg}[/dim]", highlight=False)


def log_success(msg: str) -> None:
    if not _quiet:
        _console.print(f"[bold green]{msg}[/bold green]")


def log_warning(msg: str) -> None:
    if not _quiet:
        _console.print(f"[yellow]{msg}[/yellow]")


def log_error(msg: str) -> None:
    _err_console.print(f"[bold red]{msg}[/bold red]")


# ----------------------------------------------------------------- file events
# tag: optional "[n/total]" counter shown before each status line.

def log_file_ok(rel: str, size: int = 0, tag: str = "") -> None:
    """Successful upload/download."""
    if not _quiet:
        pfx = f"[dim]{tag}[/dim] " if tag else "  "
        size_str = f"  [dim]{fmt_size(size)}[/dim]" if size else ""
        _console.print(f"{pfx}[green]✓[/green]  [cyan]{rel}[/cyan]{size_str}", highlight=False)


def log_file_fail(rel: str, reason: str, tag: str = "") -> None:
    """Failed upload/download — always shown, even with --quiet."""
    pfx = f"[dim]{tag}[/dim] " if tag else "  "
    _err_console.print(
        f"{pfx}[red]✗[/red]  [cyan]{rel}[/cyan]  [red dim]→ {reason}[/red dim]",
        highlight=False,
    )


def log_file_skip(rel: str, reason: str = "") -> None:
    """Skipped file — verbose only."""
    if _verbose and not _quiet:
        suffix = f"  [dim]{reason}[/dim]" if reason else ""
        _console.print(f"  [dim]-  {rel}{suffix}[/dim]", highlight=False)


def log_file_retry(rel: str, attempt: int, reason: str) -> None:
    """Retry attempt warning."""
    if not _quiet:
        _console.print(
            f"  [yellow]↻[/yellow]  [cyan]{rel}[/cyan]  "
            f"[yellow dim]retry {attempt} — {reason}[/yellow dim]",
            highlight=False,
        )


def log_checking(
    rel: str,
    local_dt: str,
    local_size: str,
    remote_dt: str,
    remote_size: str,
    action: str,
) -> None:
    """File comparison result — verbose only."""
    if _verbose and not _quiet:
        action_style = "green" if action == "upload" else "dim"
        _console.print(
            f"  [dim]{rel}[/dim]  "
            f"[dim]local:{local_dt} ({local_size})[/dim]  "
            f"[dim]remote:{remote_dt} ({remote_size})[/dim]  "
            f"[{action_style}]→ {action}[/{action_style}]",
            highlight=False,
        )
