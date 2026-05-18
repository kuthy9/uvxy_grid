# telegram_bot/handlers/orders.py
"""/orders — open orders from IBKR (read-only)."""
from __future__ import annotations
from telegram_bot.handlers import register


@register("/orders", "open orders from IBKR")
def handle(args: list, ctx: dict) -> str:
    ibkr = ctx["ibkr"]
    trades = ibkr.open_orders()
    if not trades:
        return "no open orders"
    lines = [f"{'SYMBOL':<6} {'SIDE':<5} {'QTY':>8} {'LMT':>10} {'TIF':>5} {'STATUS':<12}"]
    for t in trades:
        c, o, s = t.contract, t.order, t.orderStatus
        lines.append(
            f"{c.symbol:<6} {o.action:<5} {o.totalQuantity:>8.2f} "
            f"{o.lmtPrice:>10.4f} {o.tif:>5} {s.status:<12}"
        )
    return "\n".join(lines)
