from dataclasses import dataclass
from pathlib import Path
from typing import List


@dataclass
class Config:
    protocol: str
    host: str
    port: int
    username: str
    password: str
    remote_path: str
    ignore: List[str]


@dataclass
class RemoteFile:
    path: str
    mtime: float
    size: int


@dataclass
class LocalFile:
    path: Path
    rel: str
    mtime: float
    size: int
