# telegram_bot/handlers/positions.py
"""/positions — IBKR portfolio table."""
from __future__ import annotations

from telegram_bot.handlers import register


@register("/positions", "current positions from IBKR (read-only)")
def handle(args: list, ctx: dict) -> str:
    ibkr = ctx["ibkr"]
    items = ibkr.portfolio()
    if not items:
        return "no positions"
    lines = [f"{'SYMBOL':<6} {'SHARES':>10} {'AVG':>8} {'MKT':>8} {'VALUE':>10} {'UNR':>8}"]
    for p in items:
        lines.append(
            f"{p.contract.symbol:<6} {p.position:>10.2f} "
            f"{p.averageCost:>8.2f} {p.marketPrice:>8.2f} "
            f"{p.marketValue:>10.2f} {p.unrealizedPNL:>8.2f}"
        )
    return "\n".join(lines)
