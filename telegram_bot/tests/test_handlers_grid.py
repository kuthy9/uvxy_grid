# telegram_bot/tests/test_handlers_grid.py
from telegram_bot.handlers import grid as G


def test_grid_shows_center_and_levels(grid_json_sample, tmp_path):
    p = grid_json_sample(filename="trades_uvxy.db.grid.json")
    out = G.handle(args=[], ctx={
        "grid_json_paths": {"UVXY": p},
    })
    assert "UVXY" in out
    assert "12.34" in out  # center
    assert "14" in out  # 14 levels in fixture


def test_grid_when_missing(tmp_path):
    out = G.handle(args=[], ctx={
        "grid_json_paths": {"UVXY": tmp_path / "absent.json"},
    })
    assert "no grid" in out.lower() or "missing" in out.lower()
