# telegram_bot/handlers/grid.py
"""/grid — per-sub-bot grid snapshot from {db}.grid.json."""
from __future__ import annotations

from pathlib import Path
from telegram_bot.handlers import register
from telegram_bot.readers import grid_json


@register("/grid", "current grid center + levels per sub-bot")
def handle(args: list, ctx: dict) -> str:
    paths: dict[str, Path] = ctx["grid_json_paths"]
    if not paths:
        return "no sub-bots configured"
    blocks: list[str] = []
    for sym in sorted(paths):
        data = grid_json.parse(paths[sym])
        if data is None:
            blocks.append(f"{sym}: no grid snapshot")
            continue
        levels = data.get("levels", {})
        frozen = " FROZEN" if data.get("is_frozen") else ""
        head = (
            f"{sym}: center={data.get('center_price'):.4f} "
            f"spacing={data.get('spacing_pct')*100:.2f}% "
            f"capital=${data.get('grid_capital'):.0f} "
            f"levels={len(levels)} "
            f"fills_buy={data.get('total_filled_buys')} "
            f"fills_sell={data.get('total_filled_sells')}"
            f"{frozen}"
        )
        # show up to 3 buy + 3 sell closest to center
        buys = sorted(
            [v for v in levels.values() if v.get("side") == "buy"],
            key=lambda v: -v.get("level_index", 0),
        )[:3]
        sells = sorted(
            [v for v in levels.values() if v.get("side") == "sell"],
            key=lambda v: v.get("level_index", 0),
        )[:3]
        rows = [head]
        for lv in buys + sells:
            rows.append(
                f"  {lv['side']:<4} idx={lv['level_index']:>+3} "
                f"px={lv['price']:.4f} qty={lv['quantity']:.4f} state={lv['state']}"
            )
        blocks.append("\n".join(rows))
    return "\n\n".join(blocks)
