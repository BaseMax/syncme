# syncme

A fast CLI tool to sync your local project files to a remote server over **FTP** or **SFTP**.

Supports 20 parallel transfers by default, smart diff-based syncing, dry-run previews, and automatic retries.

## Installation

```bash
pip install -e .
```

Requires Python 3.11+.

## Quick start

```bash
# 1. Create a config file in your project root
syncme init

# 2. Edit .syncme.yaml with your server details

# 3. Upload everything (20 parallel connections by default)
syncme push
```

## Configuration

`syncme init` creates a `.syncme.yaml` template. Edit it:

```yaml
protocol: sftp          # ftp  or  sftp
host: example.com
port: 22                # optional -- defaults: ftp=21, sftp=22
username: user
password: secret
remote_path: /var/www/myproject
workers: 20             # parallel connections (see Performance section)

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
| `--dry-run` | `-n` | off | Preview what would transfer -- no files moved |
| `--workers N` | `-w N` | from config | Override `workers` from config for this run |
| `--retries N` | `-r N` | `3` | Retry count per file on network failure |
| `--verbose` | | off | Show skipped (up-to-date) files |
| `--quiet` | `-q` | off | Suppress all output except errors |

`auto` also accepts `--force` / `-f` to re-upload all files regardless of timestamps.

## Examples

```bash
# Preview what push would do
syncme push --dry-run

# Override workers just for this run
syncme push --workers 40

# Smart sync -- only changed files
syncme auto

# Force full re-upload, silent output
syncme auto --force --quiet

# Download remote files
syncme pull --dry-run
syncme pull
```

## Performance

syncme is designed to max out your available bandwidth rather than wait for one file at a time.

### What happens during a push with workers=20

1. **Directory pre-creation** -- before touching the thread pool, the engine makes one serial pass to create every remote directory that will be needed. Workers never stall on `makedirs`.

2. **Parallel pool warm-up** -- all 20 connections are opened *simultaneously* (not one after another), so setup cost equals one connection's time regardless of pool size.

3. **SFTP: one SSH handshake for all workers** -- `clone()` opens a new SFTP *channel* over the shared SSH transport instead of a new SSH connection. Cost: ~1 ms per extra worker, versus ~300 ms per full reconnect. All 20 workers share one TCP connection and one authentication handshake.

4. **FTP: separate connections opened in parallel** -- FTP cannot multiplex channels, so each worker gets its own TCP connection. They are all established at the same time in step 2.

5. **LPT scheduling** -- files are sorted largest-first (Longest Processing Time rule) before being dispatched. Large files start immediately, and small files fill in the gaps at the end, keeping all workers busy until the last byte is sent.

6. **Zero-cost makedirs during upload** -- the dir-cache built in step 1 is injected into every pool client. When a worker calls `upload()`, the parent-directory check is a local set lookup with no network round-trip.

7. **as_completed dispatch** -- workers pick up the next file the instant they finish the previous one, with no batch boundaries or idle gaps.

### Tuning `workers`

| Scenario | Recommended `workers` |
|---|---|
| SFTP, fast server | 20--50 (channels are cheap) |
| FTP, permissive server | 10--20 (check server connection limit) |
| FTP, strict server | 5--10 |
| Slow / metered link | 1--4 |

If the server rejects connections, reduce `workers` in `.syncme.yaml`.

### Transfer chunk size

FTP transfers use 256 KB chunks (vs the 8 KB ftplib default), which reduces per-syscall overhead on high-latency links. SFTP uses paramiko defaults with the SSH window enlarged to 3 MB and mid-transfer rekeying suppressed.

## Project structure

```
syncme/
+-- cli.py              # Typer commands + _session context manager
+-- config.py           # YAML config loading + init_config
+-- constants.py        # VERSION, CONFIG_FILE, DEFAULT_PORTS
+-- models.py           # Config, LocalFile, RemoteFile dataclasses
+-- sync/
|   +-- base.py         # BaseClient ABC -- makedirs, _pre_upload, clone()
|   +-- ftp_client.py   # FTP -- new connection per clone(), 256 KB blocks
|   +-- sftp_client.py  # SFTP -- shared transport, ~1 ms clone()
|   +-- engine.py       # SyncEngine -- scan, _Pool, _pre_create_dirs, LPT
+-- utils/
    +-- ignore.py       # pathspec gitignore matching
    +-- logger.py       # rich coloured output, verbose/quiet globals
```

## License

GPL-3.0 (c) 2026 Seyyed Ali Mohammadiyeh (MAX BASE)
