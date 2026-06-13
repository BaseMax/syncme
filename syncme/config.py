from pathlib import Path
import yaml
from .models import Config
from .constants import CONFIG_FILE, DEFAULT_PORTS


def load_config() -> Config:
    path = Path(CONFIG_FILE)
    if not path.exists():
        raise FileNotFoundError(f"{CONFIG_FILE} not found. Run 'syncme init' to create it.")

    data = yaml.safe_load(path.read_text())
    protocol = data["protocol"].lower()

    return Config(
        protocol=protocol,
        host=data["host"],
        port=int(data.get("port") or DEFAULT_PORTS[protocol]),
        username=data["username"],
        password=data["password"],
        remote_path=data["remote_path"].rstrip("/"),
        ignore=data.get("ignore", []),
        workers=int(data.get("workers", 25)),
    )


def init_config() -> None:
    path = Path(CONFIG_FILE)
    if path.exists():
        print(f"{CONFIG_FILE} already exists.")
        return

    path.write_text("""\
protocol: sftp
host: 127.0.0.1
port: 22
username: user
password: pass
remote_path: /var/www/project
workers: 25

ignore:
  - .git
  - node_modules
  - "*.log"
  - .env
""")
    print(f"Created {CONFIG_FILE}")
