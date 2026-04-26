"""
pnl_tracker.py — 简化版盈亏追踪器

设计原则:
  实盘 PnL 以 IBKR 的 realized PnL 为权威。本地只负责两件事:
    1. 胜率统计 (哪些平仓赚了/亏了, 用于月度复盘)
    2. 紧急清算 (FIFO 队列跟踪每笔网格买入, 硬止损时一次性清算)

简化 vs 旧版 (552 行 → ~200 行):
  - 删除: 与 IBKR 对账逻辑 (直接信任 IBKR)
  - 删除: 本地累计 realized PnL 字段 (通过 Executor.get_realized_pnl() 读)
  - 保留: 胜率统计 (FIFO 配对仍然是最精确的方式)
  - 保留: force_liquidate_queue (紧急场景必需)
  - 保留: 未配对检测 (Bug A 防御)
"""

import json
import logging
import sqlite3
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import config

logger = logging.getLogger("GridTrader.PnL")


@dataclass
class BuyLot:
    timestamp: str
    quantity: float
    price: float
    commission: float
    level_index: int
    order_id: Optional[int] = None


@dataclass
class TradeCloseResult:
    """一笔平仓结果 (一次 SELL 可能配对多笔 BUY)"""
    timestamp: str
    sell_quantity: float
    sell_price: float
    sell_commission: float
    sell_level_index: int

    matched_cost: float = 0.0
    matched_commission: float = 0.0
    matched_buy_count: int = 0
    unmatched_quantity: float = 0.0

    @property
    def matched_quantity(self) -> float:
        return self.sell_quantity - self.unmatched_quantity

    @property
    def gross_pnl(self) -> float:
        return self.sell_price * self.matched_quantity - self.matched_cost

    @property
    def net_pnl(self) -> float:
        return self.gross_pnl - self.matched_commission - self.sell_commission

    @property
    def is_win(self) -> bool:
        return self.net_pnl > 0


class PnLTracker:
    """
    简化版 PnL 追踪器
    
    实盘: 账户 PnL 以 IBKR realized PnL 为准, 本地仅做胜率统计和紧急清算
    回测: 没有 IBKR, 本地计算就是权威
    """

    def __init__(self, db_path: str = None, clock=None):
        self.db_path = db_path or config.DB_FILE
        self.buy_queue: deque[BuyLot] = deque()
        self._clock = clock  # 可选, 回测时提供 HistoricalClock
        self._init_db()

    def _now_iso(self) -> str:
        if self._clock is not None:
            return self._clock.now().isoformat()
        return datetime.now().isoformat()

    def _today_str(self) -> str:
        if self._clock is not None:
            return self._clock.now().strftime("%Y-%m-%d")
        return datetime.now().strftime("%Y-%m-%d")

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS pnl_fifo_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    price REAL NOT NULL,
                    commission REAL NOT NULL,
                    level_index INTEGER,
                    order_id INTEGER
                );

                CREATE TABLE IF NOT EXISTS pnl_closes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sell_timestamp TEXT NOT NULL,
                    sell_quantity REAL NOT NULL,
                    sell_price REAL NOT NULL,
                    sell_commission REAL NOT NULL,
                    sell_level INTEGER,
                    matched_cost REAL NOT NULL,
                    matched_commission REAL NOT NULL,
                    matched_buy_count INTEGER,
                    unmatched_quantity REAL DEFAULT 0,
                    gross_pnl REAL NOT NULL,
                    net_pnl REAL NOT NULL,
                    is_win INTEGER,
                    matched_buys_json TEXT
                );
            """)

    # ───── 买入/卖出 ─────

    def record_buy(self, quantity: float, price: float, commission: float,
                   level_index: int = 0, order_id: int = None) -> None:
        if quantity <= 0 or price <= 0:
            logger.warning(f"跳过无效买入: qty={quantity} price={price}")
            return
        lot = BuyLot(
            timestamp=self._now_iso(),
            quantity=quantity,
            price=price,
            commission=commission,
            level_index=level_index,
            order_id=order_id,
        )
        self.buy_queue.append(lot)

    def record_sell(self, quantity: float, price: float, commission: float,
                    level_index: int = 0) -> Optional[TradeCloseResult]:
        if quantity <= 0 or price <= 0:
            logger.warning(f"跳过无效卖出: qty={quantity} price={price}")
            return None

        result = TradeCloseResult(
            timestamp=self._now_iso(),
            sell_quantity=quantity,
            sell_price=price,
            sell_commission=commission,
            sell_level_index=level_index,
        )

        remaining = quantity
        matched_buys = []

        while remaining > 1e-8 and self.buy_queue:
            head = self.buy_queue[0]
            if head.quantity <= remaining + 1e-8:
                consumed = head.quantity
                result.matched_cost += consumed * head.price
                result.matched_commission += head.commission
                remaining -= consumed
                matched_buys.append({
                    "level": head.level_index,
                    "qty": consumed, "price": head.price,
                    "commission": head.commission,
                })
                self.buy_queue.popleft()
            else:
                consumed = remaining
                ratio = consumed / head.quantity
                consumed_com = head.commission * ratio
                result.matched_cost += consumed * head.price
                result.matched_commission += consumed_com
                head.quantity -= consumed
                head.commission -= consumed_com
                matched_buys.append({
                    "level": head.level_index,
                    "qty": consumed, "price": head.price,
                    "commission": consumed_com,
                })
                remaining = 0

        result.matched_buy_count = len(matched_buys)

        # Bug A 防御
        if remaining > 1e-6:
            result.unmatched_quantity = remaining
            logger.error(
                f"⚠️ FIFO下溢: 卖{quantity:.4f} 配{quantity-remaining:.4f} "
                f"未配对{remaining:.4f} level={level_index}"
            )

        self._save_close(result, matched_buys)
        suffix = f" 未配对{remaining:.4f}" if remaining > 1e-6 else ""
        logger.info(f"💰 平仓 {quantity:.4f}@${price:.2f} 档{level_index} | "
                    f"配{len(matched_buys)}笔 | 净PnL=${result.net_pnl:+.2f}{suffix}")
        return result

    # ───── 紧急清算 ─────

    def force_liquidate_queue(self, fill_price: float,
                              total_commission: float,
                              reason: str = "") -> Optional[TradeCloseResult]:
        """硬止损场景专用: 一次性清空队列, 保证下次启动不留幽灵"""
        if not self.buy_queue:
            return None

        total_qty = sum(lot.quantity for lot in self.buy_queue)
        if total_qty < 1e-8:
            self.buy_queue.clear()
            return None

        logger.warning(f"⚠️ 强制清算 {len(self.buy_queue)}笔 共{total_qty:.4f}股 "
                       f"@${fill_price:.2f} ({reason})")

        result = TradeCloseResult(
            timestamp=self._now_iso(),
            sell_quantity=total_qty,
            sell_price=fill_price,
            sell_commission=total_commission,
            sell_level_index=-9999,
        )

        matched_buys = []
        for lot in self.buy_queue:
            result.matched_cost += lot.quantity * lot.price
            result.matched_commission += lot.commission
            matched_buys.append({
                "level": lot.level_index,
                "qty": lot.quantity, "price": lot.price,
                "commission": lot.commission, "reason": reason,
            })

        result.matched_buy_count = len(matched_buys)
        self.buy_queue.clear()
        self._save_close(result, matched_buys)
        self.save_state()
        return result

    # ───── 查询 ─────

    def get_win_rate(self) -> dict:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT net_pnl FROM pnl_closes WHERE sell_level != -9999"
            ).fetchall()
            if not rows:
                return {"total": 0, "wins": 0, "losses": 0,
                        "win_rate": 0, "avg_win": 0, "avg_loss": 0,
                        "total_pnl": 0}
            pnls = [r[0] for r in rows]
            wins = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p <= 0]
            return {
                "total": len(pnls),
                "wins": len(wins),
                "losses": len(losses),
                "win_rate": len(wins) / len(pnls) * 100 if pnls else 0,
                "avg_win": sum(wins) / len(wins) if wins else 0,
                "avg_loss": sum(losses) / len(losses) if losses else 0,
                "total_pnl": sum(pnls),
            }

    def get_today_pnl(self) -> float:
        """今日平仓PnL (用于胜率统计和日内风控, 账户真实PnL以IBKR为准)"""
        today = self._today_str()
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(net_pnl), 0) FROM pnl_closes WHERE sell_timestamp LIKE ?",
                (f"{today}%",)
            ).fetchone()
            return float(row[0]) if row else 0.0

    def get_queue_summary(self) -> dict:
        """FIFO队列摘要 (EXIT 时用来区分网格仓和底仓)"""
        if not self.buy_queue:
            return {"count": 0, "total_qty": 0.0, "total_cost": 0.0,
                    "avg_price": 0.0}
        total_qty = sum(l.quantity for l in self.buy_queue)
        total_cost = sum(l.quantity * l.price for l in self.buy_queue)
        return {
            "count": len(self.buy_queue),
            "total_qty": total_qty,
            "total_cost": total_cost,
            "avg_price": total_cost / total_qty if total_qty > 0 else 0,
        }

    # ───── 持久化 ─────

    def save_state(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM pnl_fifo_queue")
            for lot in self.buy_queue:
                conn.execute(
                    """INSERT INTO pnl_fifo_queue 
                       (timestamp, quantity, price, commission, level_index, order_id)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (lot.timestamp, lot.quantity, lot.price,
                     lot.commission, lot.level_index, lot.order_id)
                )

    def load_state(self):
        self.buy_queue.clear()
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT timestamp, quantity, price, commission, level_index, order_id
                   FROM pnl_fifo_queue ORDER BY id"""
            ).fetchall()
            for row in rows:
                self.buy_queue.append(BuyLot(
                    timestamp=row[0], quantity=row[1], price=row[2],
                    commission=row[3], level_index=row[4], order_id=row[5]
                ))
        if self.buy_queue:
            logger.info(f"📂 队列恢复: {len(self.buy_queue)}笔")

    def _save_close(self, result: TradeCloseResult, matched_buys: list):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO pnl_closes 
                   (sell_timestamp, sell_quantity, sell_price, sell_commission, sell_level,
                    matched_cost, matched_commission, matched_buy_count, unmatched_quantity,
                    gross_pnl, net_pnl, is_win, matched_buys_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (result.timestamp, result.sell_quantity, result.sell_price,
                 result.sell_commission, result.sell_level_index,
                 result.matched_cost, result.matched_commission,
                 result.matched_buy_count, result.unmatched_quantity,
                 result.gross_pnl, result.net_pnl,
                 1 if result.is_win else 0,
                 json.dumps(matched_buys))
            )
