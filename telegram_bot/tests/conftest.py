# telegram_bot/tests/conftest.py
"""Shared fixtures: synthetic SQLite + JSON files for reader tests."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest


@pytest.fixture
def make_db(tmp_path):
    """Returns a factory: make_db(filename, rows) → Path.

    rows = { table_name: list[dict] } — order preserved.
    All tables get the relevant columns from the real schema (see spec §4.1 fix).
    """
    SCHEMA = {
        "state_machine_state": """
            CREATE TABLE state_machine_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                current_state TEXT NOT NULL,
                state_entered_at TEXT,
                last_evaluation_time TEXT,
                entry_window_started_at TEXT,
                grid_active_since TEXT,
                last_recenter_at TEXT,
                exit_initiated_at TEXT,
                exit_reason TEXT,
                total_grid_sessions INTEGER DEFAULT 0,
                total_recenters INTEGER DEFAULT 0,
                total_exits INTEGER DEFAULT 0,
                updated_at TEXT
            )
        """,
        "state_transitions": """
            CREATE TABLE state_transitions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL, from_state TEXT, to_state TEXT, reason TEXT
            )
        """,
        # 注: per-symbol DB (trades_*.db) 用的是 risk_events / risk_state;
        # 账户级 DB (account.db) 用的是 account_risk_events / account_risk_state.
        # 两套 schema 列结构不同, 不可混读 — 这里都建出来让单一 make_db()
        # 灵活模拟两种 DB 用途.
        "risk_events": """
            CREATE TABLE risk_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL, event_type TEXT NOT NULL,
                details TEXT, action_taken TEXT
            )
        """,
        "risk_state": """
            CREATE TABLE risk_state (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL, prev_close REAL, note TEXT
            )
        """,
        "account_risk_events": """
            CREATE TABLE account_risk_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL, event_type TEXT NOT NULL,
                details TEXT, action_taken TEXT
            )
        """,
        "account_risk_state": """
            CREATE TABLE account_risk_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                hard_stop_triggered INTEGER DEFAULT 0,
                daily_loss_triggered INTEGER DEFAULT 0,
                last_reset_date TEXT,
                updated_at TEXT
            )
        """,
        "account_trade_pnl": """
            CREATE TABLE account_trade_pnl (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL, date TEXT NOT NULL,
                symbol TEXT NOT NULL, net_pnl REAL NOT NULL, note TEXT
            )
        """,
        "trades": """
            CREATE TABLE trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL, action TEXT NOT NULL, symbol TEXT NOT NULL,
                quantity REAL NOT NULL, price REAL NOT NULL,
                order_type TEXT, grid_level INTEGER,
                commission REAL DEFAULT 0, pnl REAL DEFAULT 0, note TEXT
            )
        """,
        "daily_snapshots": """
            CREATE TABLE daily_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL UNIQUE, state TEXT, total_equity REAL,
                position_shares REAL, position_value REAL, cash REAL,
                unrealized_pnl REAL, realized_pnl_today REAL,
                grid_center REAL, note TEXT
            )
        """,
        "pnl_fifo_queue": """
            CREATE TABLE pnl_fifo_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL,
                quantity REAL NOT NULL, price REAL NOT NULL, commission REAL NOT NULL,
                level_index INTEGER, order_id INTEGER
            )
        """,
        "pnl_closes": """
            CREATE TABLE pnl_closes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sell_timestamp TEXT NOT NULL, sell_quantity REAL NOT NULL,
                sell_price REAL NOT NULL, sell_commission REAL NOT NULL, sell_level INTEGER,
                matched_cost REAL NOT NULL, matched_commission REAL NOT NULL,
                matched_buy_count INTEGER, unmatched_quantity REAL DEFAULT 0,
                gross_pnl REAL NOT NULL, net_pnl REAL NOT NULL, is_win INTEGER,
                matched_buys_json TEXT
            )
        """,
        "entry_evaluations": """
            CREATE TABLE entry_evaluations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL, price REAL, adx REAL, atr_pct REAL,
                bb_width_pct REAL, ema REAL, allow_entry INTEGER, rejection_reasons TEXT
            )
        """,
    }

    def _factory(filename: str = "trades.db", rows: dict | None = None) -> Path:
        rows = rows or {}
        path = tmp_path / filename
        with sqlite3.connect(path) as conn:
            for tbl, ddl in SCHEMA.items():
                conn.execute(ddl)
            for tbl, row_list in rows.items():
                if not row_list:
                    continue
                cols = list(row_list[0].keys())
                placeholders = ",".join("?" * len(cols))
                col_list = ",".join(cols)
                for r in row_list:
                    conn.execute(
                        f"INSERT INTO {tbl}({col_list}) VALUES ({placeholders})",
                        tuple(r[c] for c in cols),
                    )
        return path

    return _factory


@pytest.fixture
def grid_json_sample(tmp_path):
    def _factory(filename: str = "trades.db.grid.json",
                 center: float = 12.34, n_levels: int = 7) -> Path:
        levels = {}
        for i in range(1, n_levels + 1):
            for side, sign in (("buy", -1), ("sell", 1)):
                idx = sign * i
                levels[str(idx)] = {
                    "level_index": idx,
                    "side": side,
                    "price": center + sign * i * 0.1,
                    "quantity": 10.0,
                    "state": "idle",
                    "order_id": None,
                    "filled_price": None,
                    "filled_time": None,
                }
        data = {
            "center_price": center,
            "atr_at_init": 0.5,
            "spacing_pct": 0.01,
            "grid_capital": 1000.0,
            "total_filled_buys": 0,
            "total_filled_sells": 0,
            "is_frozen": False,
            "freeze_reason": "",
            "grid_init_time": "2026-05-17T09:30:00",
            "last_recenter_time": None,
            "levels": levels,
        }
        path = tmp_path / filename
        path.write_text(json.dumps(data, indent=2))
        return path
    return _factory


@pytest.fixture
def log_file(tmp_path):
    """Return a path + a writer helper for sequenced log writes."""
    p = tmp_path / "grid_trader.log"

    def _write(lines: list[str]) -> None:
        with open(p, "a") as fh:
            for ln in lines:
                fh.write(ln.rstrip("\n") + "\n")
    return p, _write
