from __future__ import annotations

import os
from pathlib import Path


def default_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / "AppData" / "Local"
    return root / "XianyuLite"
