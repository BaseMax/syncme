from pathlib import Path
import yaml
from .models import Config
from .constants import CONFIG_FILE, DEFAULT_PORTS


def load_config() -> Config:
    path = Path(CONFIG_FILE)
    if not path.exists():
        raise FileNotFoundError(f"{CONFIG_FILE} not found")

    data = yaml.safe_load(path.read_text())

    protocol = data["protocol"].lower()
    port = data.get("port") or DEFAULT_PORTS.get(protocol)

    return Config(
        protocol=protocol,
        host=data["host"],
        port=port,
        username=data["username"],
        password=data["password"],
        remote_path=data["remote_path"].rstrip("/"),
        ignore=data.get("ignore", []),
    )


def init_config():
    path = Path(CONFIG_FILE)
    if path.exists():
        print(f"{CONFIG_FILE} already exists")
        return

    template = """protocol: sftp
host: 127.0.0.1
port: 22
username: user
password: pass
remote_path: /var/www/project

ignore:
  - .git
  - node_modules
  - "*.log"
"""
    path.write_text(template)
    print(f"Created {CONFIG_FILE}")
