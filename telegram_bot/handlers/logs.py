# telegram_bot/handlers/logs.py
"""/logs N — last N lines of grid_trader.log with sensitive-field masking."""
from __future__ import annotations

import re
from pathlib import Path
from telegram_bot.handlers import register
from telegram_bot.readers.log_tail import LogTail

DEFAULT_N = 50


@register("/logs", "tail of grid_trader.log (use: /logs 100); masked for account IDs")
def handle(args: list, ctx: dict) -> str:
    log_path: Path = Path(ctx["log_path"])
    max_lines: int = int(ctx.get("max_lines", 1000))
    patterns: list[str] = ctx.get("mask_patterns", [])

    try:
        n = int(args[0]) if args else DEFAULT_N
    except ValueError:
        return f"bad N (try integer 1..{max_lines})"
    n = max(1, min(n, max_lines))
    lines = LogTail(log_path).read_last_n(n)
    masked = _mask(lines, patterns)
    if not masked:
        return "log empty / missing"
    return "\n".join(masked)


def _mask(lines: list[str], patterns: list[str]) -> list[str]:
    compiled = [re.compile(p) for p in patterns]
    out: list[str] = []
    for ln in lines:
        for pat in compiled:
            ln = pat.sub("***", ln)
        out.append(ln)
    return out
