"""周报对 NaN/Inf realized_pnl / unrealized_pnl 的兜底.

Bug 1 复现条件: IBKR reqPnL 在账户无任何已平仓对时,
`pnl.realizedPnL` 返回 NaN (不是 None). 旧逻辑 `if realized_pnl is None`
对 NaN 不成立, NaN 直接进入 f-string 渲染为 "+nan".

修复后:
  - realized_pnl 为 None/NaN/Inf -> 回退到本地 SUM(trades.pnl) (即 total_pnl_local)
  - unrealized_pnl 为 None/NaN/Inf -> 回退到 0.0
  - 生成的 HTML 中不出现裸 "nan" 字面量
"""
from __future__ import annotations

import math
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from report_generator import ReportGenerator, _is_finite_number  # noqa: E402


def _seed_db(db_path: Path) -> None:
    """建一个空但 schema 完整的 trades.db, 让 generate_weekly_report 可以 SELECT."""
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE trades (
                id INTEGER PRIMARY KEY,
                timestamp TEXT, action TEXT, quantity REAL,
                price REAL, pnl REAL, note TEXT
            );
            CREATE TABLE state_transitions (
                id INTEGER PRIMARY KEY,
                timestamp TEXT, from_state TEXT, to_state TEXT, reason TEXT
            );
            CREATE TABLE daily_snapshots (
                date TEXT PRIMARY KEY,
                total_equity REAL, realized_pnl_today REAL
            );
            """
        )


def test_is_finite_number_helper():
    assert _is_finite_number(0.0) is True
    assert _is_finite_number(1.5) is True
    assert _is_finite_number(-3) is True
    assert _is_finite_number(None) is False
    assert _is_finite_number(float("nan")) is False
    assert _is_finite_number(float("inf")) is False
    assert _is_finite_number(float("-inf")) is False
    assert _is_finite_number("not a number") is False


def test_weekly_report_realized_pnl_nan_falls_back_to_local(tmp_path):
    """模拟 IBKR get_realized_pnl() 返回 NaN — 渲染不能含 'nan'."""
    db = tmp_path / "trades.db"
    _seed_db(db)
    report_dir = tmp_path / "reports"

    gen = ReportGenerator(db_path=str(db), report_dir=str(report_dir),
                          symbol="UVXY")
    account_data = {
        "state": "EXIT_PENDING",
        "equity": 5000.00,
        "shares": 137.0,
        "cash": 100.0,
        "unrealized_pnl": 16.47,
        "realized_pnl": float("nan"),  # ← IBKR 空账户时的真实表现
    }
    path = gen.generate_weekly_report(account_data, grid_data=None)
    html = Path(path).read_text(encoding="utf-8")

    # 不能渲染出裸 "nan" / "+nan"
    assert "nan" not in html.lower(), \
        "周报模板对 NaN realized_pnl 没有兜底, 已经被渲染为字面 'nan'"
    # 应该回退到本地 SUM(pnl), 这里 trades 为空 -> 0.00
    assert "$+0.00" in html or "$0.00" in html, \
        "fallback 应当让 realized 显示 $0.00"


def test_weekly_report_realized_pnl_none_still_falls_back(tmp_path):
    """回归: 旧行为 (None -> fallback) 不能被新逻辑破坏."""
    db = tmp_path / "trades.db"
    _seed_db(db)
    # 提前塞一笔 trade.pnl, 让 fallback 拿到非零值
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO trades(timestamp, action, quantity, price, pnl, note) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("2026-05-15T10:00:00", "SELL", 10, 35.0, 42.50, "test"),
        )

    gen = ReportGenerator(db_path=str(db), report_dir=str(tmp_path / "reports"),
                          symbol="UVXY")
    account_data = {
        "state": "SCANNING",
        "equity": 5000.0, "shares": 0.0, "cash": 5000.0,
        "unrealized_pnl": 0.0,
        # realized_pnl 缺失 — 走 fallback
    }
    path = gen.generate_weekly_report(account_data, grid_data=None)
    html = Path(path).read_text(encoding="utf-8")
    assert "$+42.50" in html, "None fallback 应当取本地 SUM(pnl) = 42.50"


def test_weekly_report_unrealized_pnl_nan_renders_zero(tmp_path):
    db = tmp_path / "trades.db"
    _seed_db(db)
    gen = ReportGenerator(db_path=str(db), report_dir=str(tmp_path / "reports"),
                          symbol="UVXY")
    account_data = {
        "state": "ACTIVE_GRID",
        "equity": 5000.0, "shares": 100.0, "cash": 100.0,
        "unrealized_pnl": float("nan"),  # 也兜底
        "realized_pnl": 0.0,
    }
    path = gen.generate_weekly_report(account_data, grid_data=None)
    html = Path(path).read_text(encoding="utf-8")
    assert "nan" not in html.lower()


def test_weekly_report_valid_realized_pnl_passes_through(tmp_path):
    """正常路径不被破坏: 有效数字直接渲染, label 标记为 IBKR 权威."""
    db = tmp_path / "trades.db"
    _seed_db(db)
    gen = ReportGenerator(db_path=str(db), report_dir=str(tmp_path / "reports"),
                          symbol="UVXY")
    account_data = {
        "state": "ACTIVE_GRID",
        "equity": 5200.0, "shares": 50.0, "cash": 100.0,
        "unrealized_pnl": 12.34,
        "realized_pnl": 88.99,   # 与本地 (0) 不同, 应触发 IBKR 标签
    }
    path = gen.generate_weekly_report(account_data, grid_data=None)
    html = Path(path).read_text(encoding="utf-8")
    assert "$+88.99" in html
    assert "$+12.34" in html
    assert "账户累计已实现 (IBKR)" in html
