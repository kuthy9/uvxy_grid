"""
trade_logger.py — 交易日志和数据库 (v2)

新增表:
  - state_transitions: 状态机转换历史
  - entry_evaluations: 每次入场评估的详细记录
  - grid_recenters: 中轴重置历史
"""

import logging
import sqlite3
from datetime import datetime
from pathlib import Path

import config


def setup_logging() -> logging.Logger:
    logger = logging.getLogger("GridTrader")
    if logger.handlers:
        return logger
    logger.setLevel(getattr(logging, config.LOG_LEVEL))
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logger.addHandler(console)
    file_h = logging.FileHandler(config.LOG_FILE, encoding="utf-8")
    file_h.setFormatter(fmt)
    logger.addHandler(file_h)
    return logger


class TradeDatabase:
    def __init__(self, db_path: str = None):
        self.db_path = db_path or config.DB_FILE
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    action TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    price REAL NOT NULL,
                    order_type TEXT,
                    grid_level INTEGER,
                    commission REAL DEFAULT 0,
                    pnl REAL DEFAULT 0,
                    note TEXT
                );

                CREATE TABLE IF NOT EXISTS state_transitions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    from_state TEXT,
                    to_state TEXT,
                    reason TEXT
                );

                CREATE TABLE IF NOT EXISTS entry_evaluations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    price REAL,
                    adx REAL,
                    atr_pct REAL,
                    bb_width_pct REAL,
                    ema REAL,
                    allow_entry INTEGER,
                    rejection_reasons TEXT
                );

                CREATE TABLE IF NOT EXISTS grid_recenters (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    old_center REAL,
                    new_center REAL,
                    old_spacing_pct REAL,
                    new_spacing_pct REAL,
                    atr_value REAL,
                    reason TEXT
                );

                CREATE TABLE IF NOT EXISTS daily_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL UNIQUE,
                    state TEXT,
                    total_equity REAL,
                    position_shares REAL,
                    position_value REAL,
                    cash REAL,
                    unrealized_pnl REAL,
                    realized_pnl_today REAL,
                    grid_center REAL,
                    note TEXT
                );

                CREATE TABLE IF NOT EXISTS risk_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    details TEXT,
                    action_taken TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_trades_ts ON trades(timestamp);
                CREATE INDEX IF NOT EXISTS idx_snapshots_date ON daily_snapshots(date);
            """)

    def log_trade(self, action, symbol, quantity, price,
                  order_type="", grid_level=0, commission=0, pnl=0, note=""):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO trades (timestamp, action, symbol, quantity, price, 
                   order_type, grid_level, commission, pnl, note)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (datetime.now().isoformat(), action, symbol, quantity, price,
                 order_type, grid_level, commission, pnl, note)
            )

    def log_state_transition(self, from_state, to_state, reason=""):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO state_transitions (timestamp, from_state, to_state, reason)
                   VALUES (?, ?, ?, ?)""",
                (datetime.now().isoformat(), from_state, to_state, reason)
            )

    def log_entry_evaluation(self, price, adx, atr_pct, bb_width, ema,
                              allow, reasons=""):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO entry_evaluations 
                   (timestamp, price, adx, atr_pct, bb_width_pct, ema, 
                    allow_entry, rejection_reasons)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (datetime.now().isoformat(), price, adx, atr_pct, bb_width, ema,
                 1 if allow else 0, reasons)
            )

    def log_grid_recenter(self, old_center, new_center, old_spacing,
                          new_spacing, atr, reason=""):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO grid_recenters 
                   (timestamp, old_center, new_center, old_spacing_pct, 
                    new_spacing_pct, atr_value, reason)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (datetime.now().isoformat(), old_center, new_center,
                 old_spacing, new_spacing, atr, reason)
            )

    def log_daily_snapshot(self, state, total_equity, position_shares,
                           position_value, cash, unrealized_pnl,
                           realized_pnl_today, grid_center, note=""):
        today = datetime.now().strftime("%Y-%m-%d")
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO daily_snapshots
                   (date, state, total_equity, position_shares, position_value,
                    cash, unrealized_pnl, realized_pnl_today, grid_center, note)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (today, state, total_equity, position_shares, position_value,
                 cash, unrealized_pnl, realized_pnl_today, grid_center, note)
            )

    def log_risk_event(self, event_type, details, action_taken):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO risk_events (timestamp, event_type, details, action_taken)
                   VALUES (?, ?, ?, ?)""",
                (datetime.now().isoformat(), event_type, details, action_taken)
            )

    # ──────────────────────────
    #  查询
    # ──────────────────────────

    def get_today_realized_pnl(self) -> float:
        today = datetime.now().strftime("%Y-%m-%d")
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(pnl), 0) FROM trades WHERE timestamp LIKE ?",
                (f"{today}%",)
            ).fetchone()
            return row[0] if row else 0.0

    def get_total_realized_pnl(self) -> float:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT COALESCE(SUM(pnl), 0) FROM trades").fetchone()
            return row[0] if row else 0.0

    def get_today_trades(self) -> list:
        today = datetime.now().strftime("%Y-%m-%d")
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT timestamp, action, quantity, price, pnl, note 
                   FROM trades WHERE timestamp LIKE ? ORDER BY timestamp""",
                (f"{today}%",)
            ).fetchall()
            return rows

    def get_recent_state_transitions(self, n: int = 10) -> list:
        with sqlite3.connect(self.db_path) as conn:
            return conn.execute(
                """SELECT timestamp, from_state, to_state, reason 
                   FROM state_transitions ORDER BY id DESC LIMIT ?""",
                (n,)
            ).fetchall()
