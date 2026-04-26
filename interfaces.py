"""
interfaces.py — 执行层抽象

定义两个核心抽象:
  - Clock: 时间源 (实盘用系统时钟, 回测用历史时钟)
  - Executor: 订单执行 (实盘通过 IBKR, 回测通过模拟 OHLC 撮合)

主程序 main.py 通过这些抽象与底层解耦, 实盘和回测共用同一套业务逻辑。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional, Protocol


# ─────────────────────────────────────
# Clock: 时间源
# ─────────────────────────────────────

class Clock(Protocol):
    """时间源协议"""

    def now(self) -> datetime:
        """当前时间"""
        ...

    def sleep(self, seconds: float) -> None:
        """休眠 (回测里是 no-op)"""
        ...


class LiveClock:
    """实盘时钟 — 使用系统真实时间"""

    def now(self) -> datetime:
        return datetime.now()

    def sleep(self, seconds: float) -> None:
        import time
        time.sleep(seconds)


class HistoricalClock:
    """回测时钟 — 由外部 driver 推进当前时间"""

    def __init__(self):
        self._current: Optional[datetime] = None

    def set(self, t: datetime) -> None:
        self._current = t

    def now(self) -> datetime:
        if self._current is None:
            raise RuntimeError("HistoricalClock 未初始化, 请先调用 set()")
        return self._current

    def sleep(self, seconds: float) -> None:
        return None


# ─────────────────────────────────────
# Executor: 订单执行
# ─────────────────────────────────────

class Executor(ABC):
    """
    订单执行接口
    
    实现类:
      - IBKRExecutor: 通过 IBKR API 下单
      - SimulatedExecutor: 回测中按 OHLC 模拟成交
    """

    # ── 连接管理 ──
    @abstractmethod
    def connect(self) -> bool: ...

    @abstractmethod
    def disconnect(self) -> None: ...

    @abstractmethod
    def is_connected(self) -> bool: ...

    def reconnect(self) -> bool:
        """默认重连逻辑: disconnect + connect"""
        self.disconnect()
        return self.connect()

    # ── 订单 ──
    @abstractmethod
    def place_limit_order(self, action: str, quantity: float, price: float,
                          level_index: int = 0,
                          order_type_label: str = "") -> Optional[int]: ...

    @abstractmethod
    def place_market_order(self, action: str, quantity: float,
                           level_index: int = 0,
                           order_type_label: str = "") -> Optional[int]: ...

    @abstractmethod
    def cancel_order(self, order_id: int) -> bool: ...

    @abstractmethod
    def cancel_all_orders(self) -> None: ...

    def batch_cancel_orders(self, order_ids: list[int]) -> int:
        """批量撤单 — 默认实现"""
        count = 0
        for oid in order_ids:
            if self.cancel_order(oid):
                count += 1
        return count

    # ── 成交查询 ──
    @abstractmethod
    def check_order_fills(self) -> list[dict]: ...

    @abstractmethod
    def wait_for_order_fill(self, order_id: int,
                            timeout_sec: int = 60) -> Optional[dict]: ...

    @abstractmethod
    def get_order_progress(self, order_id: int) -> Optional[dict]: ...

    # ── 持仓 & 账户 ──
    @abstractmethod
    def get_current_price(self) -> Optional[float]: ...

    @abstractmethod
    def get_prev_close(self) -> Optional[float]:
        """
        返回**上一根已完成日线**的收盘价.
        实盘 (IBKRExecutor): reqHistoricalData 拉近 N 个日线, 过滤掉今天 (ET) 的未完成 bar,
                              返回最近一根 date < today 的 close.
        回测 (SimulatedExecutor): 返回 current_index-1 的 Close; 起点 bar 返回 None.
        取不到返回 None (闪崩保护会降级到其他恢复路径).
        """
        ...

    @abstractmethod
    def get_position_details(self) -> dict: ...

    @abstractmethod
    def get_account_summary(self) -> dict: ...

    def get_cash(self) -> float:
        return float(self.get_account_summary().get("TotalCashValue", 0.0))

    # ── IBKR 特有: 获取真实 realized PnL (回测模拟返回本地计算) ──
    @abstractmethod
    def get_realized_pnl(self) -> Optional[float]:
        """
        返回账户层面的累计已实现盈亏。
        实盘: 从 IBKR reqPnL 获取
        回测: 返回 SimulatedExecutor 内部的 realized_pnl
        """
        ...
