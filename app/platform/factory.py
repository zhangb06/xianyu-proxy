from __future__ import annotations

import sys
from pathlib import Path

from .base import Paths, build_paths


def make_paths(data_dir: str | Path | None = None) -> Paths:
    if data_dir:
        return build_paths(Path(data_dir))
    if sys.platform == "win32":
        from .windows import default_data_dir
    else:
        from .linux import default_data_dir
    return build_paths(default_data_dir())
