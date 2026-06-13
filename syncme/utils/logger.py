from rich.console import Console
from rich.text import Text

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


def log(msg: str) -> None:
    if not _quiet:
        _console.print(msg, highlight=False)


def log_action(verb: str, rel: str) -> None:
    if not _quiet:
        t = Text()
        t.append(f"  {verb} ", style="bold")
        t.append(rel, style="cyan")
        _console.print(t)


def log_verbose(msg: str) -> None:
    if _verbose and not _quiet:
        _console.print(f"[dim]  {msg}[/dim]")


def log_success(msg: str) -> None:
    if not _quiet:
        _console.print(f"[bold green]{msg}[/bold green]")


def log_warning(msg: str) -> None:
    if not _quiet:
        _console.print(f"[yellow]{msg}[/yellow]")


def log_error(msg: str) -> None:
    _err_console.print(f"[bold red]{msg}[/bold red]")
