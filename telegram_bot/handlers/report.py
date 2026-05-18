# telegram_bot/handlers/report.py
"""/report — send newest weekly HTML report from REPORT_DIR."""
from __future__ import annotations

from pathlib import Path
from typing import Union
from telegram_bot.handlers import register


@register("/report", "send the newest weekly_*.html report")
def handle(args: list, ctx: dict) -> Union[str, dict]:
    report_dir: Path = Path(ctx["report_dir"])
    if not report_dir.exists():
        return "no report directory"
    candidates = sorted(report_dir.glob("weekly_*.html"))
    if not candidates:
        return "no weekly report yet"
    latest = candidates[-1]
    return {"document": latest, "caption": f"weekly report: {latest.stem}"}
