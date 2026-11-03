from __future__ import annotations

import re
import sys
from pathlib import Path

from loguru import logger

_SECRET_PATTERN = re.compile(
    r"(?i)\b(cookie2|unb|_m_h5_tk|accessToken|token)=([^;\s&\"']+)"
)
_PHONE_PATTERN = re.compile(r"(?<!\d)(1[3-9]\d{9})(?!\d)")


def redact(message: str) -> str:
    message = _SECRET_PATTERN.sub(r"\1=***", message)
    return _PHONE_PATTERN.sub(lambda match: match.group(1)[:3] + "****" + match.group(1)[-4:], message)


def _redacting_filter(record: dict) -> bool:
    record["message"] = redact(record["message"])
    return True


def setup_logging(level: str, log_file: Path) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logger.remove()
    logger.add(
        sys.stderr,
        level=level,
        filter=_redacting_filter,
        backtrace=False,
        diagnose=False,
    )
    logger.add(
        log_file,
        level=level,
        filter=_redacting_filter,
        rotation="10 MB",
        retention="14 days",
        encoding="utf-8",
        enqueue=True,
        backtrace=False,
        diagnose=False,
    )
