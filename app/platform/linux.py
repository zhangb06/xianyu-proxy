from __future__ import annotations

import os
from pathlib import Path


def default_data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME")
    root = Path(base).expanduser() if base else Path.home() / ".local" / "share"
    return root / "xianyu-lite"
