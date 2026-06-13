# syncme

A fast CLI tool to sync your local project files to a remote server over **FTP** or **SFTP**.

Supports parallel transfers, smart diff-based syncing, dry-run previews, and automatic retries.

## Installation

```bash
pip install -e .
```

Requires Python 3.11+.

## Quick start

```bash
# 1. Create a config file in your project root
syncme init

# 2. Edit .syncme.yaml with your server details (see Configuration below)

# 3. Upload everything
syncme push
```

## Configuration

`syncme init` creates a `.syncme.yaml` template in the current directory. Edit it:

```yaml
protocol: sftp          # ftp  or  sftp
host: example.com
port: 22                # optional — defaults: ftp=21, sftp=22
username: user
password: secret
remote_path: /var/www/myproject

ignore:
  - .git
  - node_modules
  - "*.log"
  - .env
```

## Commands

| Command | Description |
|---|---|
| `syncme init` | Create a `.syncme.yaml` config template |
| `syncme push` | Upload **all** local files to the remote |
| `syncme pull` | Download **all** remote files to local |
| `syncme auto` | Upload only **new or modified** files (diff sync) |

## Options

All transfer commands (`push`, `pull`, `auto`) accept:

| Flag | Short | Default | Description |
|---|---|---|---|
| `--dry-run` | `-n` | off | Preview what would transfer — no files moved |
| `--workers N` | `-w N` | `1` | Parallel connections for faster transfers |
| `--retries N` | `-r N` | `3` | Retry count per file on network failure |
| `--verbose` | | off | Show skipped (up-to-date) files |
| `--quiet` | `-q` | off | Suppress all output except errors |

`auto` also accepts `--force` / `-f` to re-upload all files regardless of timestamps.

## Examples

```bash
# Preview what push would do
syncme push --dry-run

# Upload with 4 parallel connections
syncme push --workers 4

# Smart sync — only changed files, show skipped ones
syncme auto --verbose

# Force full re-upload, no output
syncme auto --force --quiet

# Download remote files (dry run first)
syncme pull --dry-run
syncme pull

# Check version
syncme --version
```

## How it works

**`push`** — scans the local directory recursively (respecting `.gitignore`-style `ignore` patterns), then uploads every file to the matching path on the remote. Remote directories are created automatically.

**`pull`** — lists the remote directory recursively, then downloads every file to the matching local path, creating subdirectories as needed.

**`auto`** — combines both: compares local mtimes against remote mtimes and only uploads files that are newer locally. Use `--force` to skip the comparison and upload everything.

**Parallel transfers** (`--workers N`) open N simultaneous connections. Each worker gets its own dedicated connection from a pool, so there is no lock contention and throughput scales linearly up to the server's connection limit.

**Retries** — each file is retried up to N times on failure before being counted as an error. The final summary always shows how many files succeeded, were skipped, and failed.

## Project structure

```
syncme/
├── cli.py              # Typer commands + _session context manager
├── config.py           # YAML config loading + init_config
├── constants.py        # VERSION, CONFIG_FILE, DEFAULT_PORTS
├── models.py           # Config, LocalFile, RemoteFile dataclasses
├── sync/
│   ├── base.py         # BaseClient ABC — makedirs, _pre_upload shared logic
│   ├── ftp_client.py   # FTP implementation
│   ├── sftp_client.py  # SFTP implementation (paramiko)
│   └── engine.py       # SyncEngine — scan, upload/download, _Pool
└── utils/
    ├── ignore.py       # pathspec-based gitignore matching
    └── logger.py       # rich-based coloured output, verbose/quiet globals
```

## License

GPL-3.0 © 2026 Seyyed Ali Mohammadiyeh (MAX BASE)
