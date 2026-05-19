# telegram_bot/handlers/positions.py
"""/positions — IBKR portfolio table."""
from __future__ import annotations

from telegram_bot.handlers import register


@register("/positions", "current positions from IBKR (read-only, forces fresh sync)")
def handle(args: list, ctx: dict) -> str:
    ibkr = ctx["ibkr"]
    # refresh=True: 主动重新订阅 reqAccountUpdates, 避免长连接下 ib_insync
    # 的 portfolio 缓存停留在数小时前快照 — 实战见过 22+ 小时无 update 事件,
    # 用户看到陈旧持仓 (见 IBKRReadOnly.portfolio docstring).
    items = ibkr.portfolio(refresh=True)
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
