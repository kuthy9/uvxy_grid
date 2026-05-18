# telegram_bot/handlers/help.py
"""/help — list registered commands."""
from __future__ import annotations

from telegram_bot.handlers import HELP_LINES, register


@register("/help", "list available commands")
def handle(args: list, ctx: dict) -> str:
    lines = ["Available commands:"]
    for cmd in sorted(HELP_LINES):
        lines.append(f"{cmd:<12} — {HELP_LINES[cmd]}")
    return "\n".join(lines)
