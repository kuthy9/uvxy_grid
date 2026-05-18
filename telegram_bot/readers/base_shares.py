# telegram_bot/readers/base_shares.py
"""Parse {db}.base_shares.txt — plain float file."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("telegram_bot.readers.base_shares")


def parse(path: Path) -> Optional[float]:
    if not path.exists():
        return None
    try:
        text = path.read_text().strip()
    except OSError as e:
        logger.warning("base_shares read error: %s", e)
        return None
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError as e:
        logger.warning("base_shares not numeric: %s", e)
        return None
