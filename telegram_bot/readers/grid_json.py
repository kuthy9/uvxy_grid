# telegram_bot/readers/grid_json.py
"""Parse {db}.grid.json. Mid-write race → 100ms retry once, then give up."""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("telegram_bot.readers.grid_json")


def parse(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    for attempt in range(2):
        text = path.read_text()
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            if attempt == 0:
                logger.debug("grid_json parse attempt %d failed (%s) — retry in 100ms",
                             attempt + 1, e)
                time.sleep(0.1)
                continue
            logger.warning("grid_json parse failed twice: %s", e)
            return None
    return None
