from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Paths:
    data: Path
    db: Path
    logs: Path
    qr: Path
    fixtures: Path
    browser: Path

    def ensure(self) -> Paths:
        for path in (self.data, self.logs, self.qr, self.fixtures, self.browser):
            path.mkdir(parents=True, exist_ok=True)
        return self


def build_paths(data_dir: Path) -> Paths:
    data = data_dir.expanduser().resolve()
    return Paths(
        data=data,
        db=data / "app.db",
        logs=data / "logs",
        qr=data / "qr",
        fixtures=data / "fixtures",
        browser=data / "browser",
    )
