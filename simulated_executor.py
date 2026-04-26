"""
simulated_executor.py — 回测执行层

实现 Executor 协议, 用 OHLC 历史数据模拟订单撮合。
核心原则: 与 IBKRExecutor 行为尽可能一致, 让主程序 main.py 不感知差异。

真实化回测 (BT_REALISTIC_FILLS=True):
  - 市价单扣 half-spread 滑点 (BT_MARKET_SLIP_BPS)
  - 限价单仅触价时按概率成交 (BT_LIMIT_FILL_PROB_TOUCH), 跳空穿越按 BT_GAP_FILL_PROB
  - SEC Section 31 / FINRA TAF 费按卖出名义金额/股数计算
  - 所有随机性通过 BT_RANDOM_SEED 可复现
"""

import logging
import random
from datetime import datetime
from typing import Optional

import pandas as pd

import config
from interfaces import Executor

logger = logging.getLogger("GridTrader.Sim")


def _ibkr_commission(qty: float) -> float:
    """IBKR Tiered 散户佣金 (per-share + min)"""
    return max(config.IBKR_COMMISSION_MIN,
               abs(qty) * config.IBKR_COMMISSION_PER_SHARE)


def _sell_regulatory_fees(qty: float, price: float) -> float:
    """卖出附加费: SEC Section 31 + FINRA TAF"""
    notional = abs(qty) * price
    sec = notional * config.SEC_FEE_RATE
    taf_raw = abs(qty) * config.TAF_FEE_PER_SHARE
    taf = min(config.TAF_FEE_MAX, max(config.TAF_FEE_MIN, taf_raw))
    return sec + taf


def _calc_commission(qty: float, price: float = 0.0, side: str = "BUY") -> float:
    """
    完整交易成本 (佣金 + 监管费). 卖出方向才计监管费.
    price=0 时退化为只算佣金 (向后兼容).
    """
    base = _ibkr_commission(qty)
    if side.upper() == "SELL" and price > 0:
        base += _sell_regulatory_fees(qty, price)
    return base


class SimulatedExecutor(Executor):
    """回测执行器"""

    def __init__(self, df: pd.DataFrame, initial_cash: float, clock,
                 realistic: Optional[bool] = None,
                 seed: Optional[int] = None):
        self.df = df.sort_index().copy()
        self.initial_cash = initial_cash
        self.cash = initial_cash
        self.position_shares = 0.0
        self.position_cost = 0.0
        self.realized_pnl = 0.0  # 账户级已实现PnL
        self._clock = clock  # HistoricalClock
        self.active_orders: dict[int, dict] = {}
        self._connected = True
        self._next_order_id = 1
        self.current_index = 0

        # 真实化开关 + 随机性 (种子化, 可复现)
        self.realistic = (config.BT_REALISTIC_FILLS if realistic is None else realistic)
        self._rng = random.Random(seed if seed is not None else config.BT_RANDOM_SEED)

    def set_bar_index(self, index: int):
        """driver 推进当前 bar"""
        self.current_index = index

    def _row(self) -> pd.Series:
        return self.df.iloc[self.current_index]

    # ───── 连接 ─────
    def connect(self) -> bool:
        self._connected = True
        return True

    def disconnect(self):
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def sleep(self, seconds: float):
        return None

    # ───── 价格 ─────
    def get_current_price(self) -> Optional[float]:
        return float(self._row()["Close"])

    def get_prev_close(self) -> Optional[float]:
        """回测: 上一根已完成 bar 的 Close. current_index==0 时无上一根."""
        if self.current_index <= 0:
            return None
        return float(self.df.iloc[self.current_index - 1]["Close"])

    # ───── 下单 ─────
    def place_limit_order(self, action: str, quantity: float, price: float,
                          level_index: int = 0,
                          order_type_label: str = "") -> Optional[int]:
        order_id = self._next_order_id
        self._next_order_id += 1
        self.active_orders[order_id] = {
            "order_id": order_id,
            "action": action,
            "quantity": float(quantity),
            "price": float(price),
            "level_index": level_index,
            "order_type": order_type_label,
            "placed_index": self.current_index,
            "placed_time": self._clock.now().isoformat(),
            "market": False,
        }
        return order_id

    def place_market_order(self, action: str, quantity: float,
                           level_index: int = 0,
                           order_type_label: str = "") -> Optional[int]:
        order_id = self._next_order_id
        self._next_order_id += 1
        self.active_orders[order_id] = {
            "order_id": order_id,
            "action": action,
            "quantity": float(quantity),
            "price": 0.0,
            "level_index": level_index,
            "order_type": order_type_label,
            "placed_index": self.current_index,
            "placed_time": self._clock.now().isoformat(),
            "market": True,
        }
        return order_id

    def cancel_order(self, order_id: int) -> bool:
        return self.active_orders.pop(order_id, None) is not None

    def cancel_all_orders(self):
        self.active_orders.clear()

    # ───── 成交 (核心: OHLC 撮合) ─────

    # ───── 滑点/概率辅助 ─────

    def _market_slip_price(self, mid_price: float, side: str) -> float:
        """市价单按 half-spread 不利方向滑价"""
        if not self.realistic or config.BT_MARKET_SLIP_BPS <= 0:
            return mid_price
        bps = config.BT_MARKET_SLIP_BPS / 1e4
        return mid_price * (1 + bps) if side.upper() == "BUY" else mid_price * (1 - bps)

    def _limit_slip_price(self, limit_price: float, side: str) -> float:
        """限价单按 BT_LIMIT_SLIP_BPS 做微小不利滑点 (模拟排队/延迟)"""
        if not self.realistic or config.BT_LIMIT_SLIP_BPS <= 0:
            return limit_price
        bps = config.BT_LIMIT_SLIP_BPS / 1e4
        return limit_price * (1 + bps) if side.upper() == "BUY" else limit_price * (1 - bps)

    def _fill_prob(self, kind: str) -> float:
        """返回给定 fill 场景的成交概率"""
        if not self.realistic:
            return 1.0
        if kind == "touch":
            return max(0.0, min(1.0, config.BT_LIMIT_FILL_PROB_TOUCH))
        if kind == "cross":
            return max(0.0, min(1.0, config.BT_LIMIT_FILL_PROB_CROSS))
        if kind == "gap":
            return max(0.0, min(1.0, config.BT_GAP_FILL_PROB))
        return 1.0

    def _roll(self, prob: float) -> bool:
        if prob >= 1.0:
            return True
        if prob <= 0.0:
            return False
        return self._rng.random() < prob

    def _execute_fill(self, info: dict, fill_price: float) -> Optional[dict]:
        """实际完成一笔成交的资金/持仓变动"""
        qty = float(info["quantity"])
        if info["action"] == "SELL":
            qty = min(qty, self.position_shares)
            if qty < 0.0001:
                return None

        commission = _calc_commission(qty, price=fill_price, side=info["action"])
        if info["action"] == "BUY":
            total_cost = qty * fill_price + commission
            if self.cash + 1e-9 < total_cost:
                return None
            self.cash -= total_cost
            # cost_basis 把买入佣金算进去, 这样 realized_pnl 与 PnLTracker.net_pnl
            # 在卖出时口径一致 (= 卖出净所得 - 买入总成本含佣 - 卖出佣金).
            self.position_cost += total_cost
            self.position_shares += qty
        else:
            avg_cost = (self.position_cost / self.position_shares
                        if self.position_shares > 0 else fill_price)
            realized = (fill_price - avg_cost) * qty - commission
            self.cash += qty * fill_price - commission
            self.realized_pnl += realized  # 账户级PnL累计
            self.position_cost -= avg_cost * qty
            self.position_shares -= qty
            if self.position_shares < 0.0001:
                self.position_shares = 0.0
                self.position_cost = 0.0

        return {
            "order_id": info["order_id"],
            "level_index": info["level_index"],
            "action": info["action"],
            "quantity": qty,
            "fill_price": fill_price,
            "order_type": info["order_type"],
            "commission": commission,
        }

    def _try_immediate_fill(self, info: dict) -> Optional[dict]:
        """wait_for_order_fill / 市价单用: 按当前价立即成交"""
        ref = self.get_current_price()
        if info["market"]:
            # 市价单: half-spread 不利滑点
            return self._execute_fill(info, self._market_slip_price(ref, info["action"]))
        # 限价单 marketable case (即价格已越过 limit)
        if info["action"] == "BUY" and ref <= info["price"] + 1e-9:
            fill_price = self._limit_slip_price(min(ref, info["price"]), info["action"])
            return self._execute_fill(info, fill_price)
        if info["action"] == "SELL" and ref >= info["price"] - 1e-9:
            fill_price = self._limit_slip_price(max(ref, info["price"]), info["action"])
            return self._execute_fill(info, fill_price)
        return None

    def _fill_gap_at_open(self, open_price: float) -> list[dict]:
        """新 bar 开盘跳空成交检查 — 按 gap 概率"""
        fills = []
        for oid, info in list(self.active_orders.items()):
            if info["placed_index"] >= self.current_index:
                continue
            if info["market"]:
                continue
            crossed = False
            if info["action"] == "BUY" and open_price <= info["price"] + 1e-9:
                crossed = True
            elif info["action"] == "SELL" and open_price >= info["price"] - 1e-9:
                crossed = True
            if not crossed:
                continue
            if not self._roll(self._fill_prob("gap")):
                continue
            fill_price = self._limit_slip_price(info["price"], info["action"])
            fill = self._execute_fill(info, fill_price)
            if fill:
                fills.append(fill)
                del self.active_orders[oid]
        return fills

    def check_order_fills(self) -> list[dict]:
        """按当前bar的OHLC路径撮合所有挂单"""
        row = self._row()
        open_p = float(row["Open"])
        high = float(row["High"])
        low = float(row["Low"])
        close = float(row["Close"])

        fills = self._fill_gap_at_open(open_p)
        # 按收盘方向确定 bar 内价格路径
        path = [open_p, low, high, close] if close >= open_p else [open_p, high, low, close]

        for seg_start, seg_end in zip(path, path[1:]):
            moving_up = seg_end > seg_start
            candidates = []
            for oid, info in self.active_orders.items():
                if info["placed_index"] >= self.current_index or info["market"]:
                    continue
                if moving_up and info["action"] == "SELL":
                    if seg_start < info["price"] <= seg_end + 1e-9:
                        candidates.append(info)
                elif (not moving_up) and info["action"] == "BUY":
                    if seg_end - 1e-9 <= info["price"] < seg_start:
                        candidates.append(info)

            if moving_up:
                candidates.sort(key=lambda x: x["price"])
            else:
                candidates.sort(key=lambda x: x["price"], reverse=True)

            for info in candidates:
                oid = info["order_id"]
                if oid not in self.active_orders:
                    continue
                # 判定 touch vs cross: |seg_end - limit| < 10% 的 seg 长度视作 touch
                seg_len = abs(seg_end - seg_start)
                near = seg_len > 0 and abs(seg_end - info["price"]) < 0.1 * seg_len
                prob = self._fill_prob("touch" if near else "cross")
                if not self._roll(prob):
                    continue
                fill_price = self._limit_slip_price(info["price"], info["action"])
                fill = self._execute_fill(info, fill_price)
                if fill:
                    fills.append(fill)
                    del self.active_orders[oid]

        return fills

    def wait_for_order_fill(self, order_id: int,
                            timeout_sec: int = 60) -> Optional[dict]:
        """回测中: 当前 bar 能成交就成交, 否则返回 None"""
        info = self.active_orders.get(order_id)
        if not info:
            return None
        fill = self._try_immediate_fill(info)
        if fill:
            del self.active_orders[order_id]
            return fill
        return None

    def get_order_progress(self, order_id: int) -> Optional[dict]:
        info = self.active_orders.get(order_id)
        if not info:
            return None
        return {
            "status": "Submitted",
            "filled_qty": 0.0,
            "remaining_qty": float(info["quantity"]),
            "avg_fill_price": 0.0,
            "commission": 0.0,
            "action": info["action"],
            "level_index": info["level_index"],
            "order_type": info["order_type"],
        }

    # ───── 持仓/账户 ─────

    def get_position_details(self) -> dict:
        price = self.get_current_price() or 0.0
        avg_cost = (self.position_cost / self.position_shares
                    if self.position_shares > 0 else 0.0)
        mkt_val = self.position_shares * price
        unrl = ((price - avg_cost) * self.position_shares
                if self.position_shares > 0 else 0.0)
        return {
            "shares": self.position_shares,
            "avg_cost": avg_cost,
            "market_value": mkt_val,
            "unrealized_pnl": unrl,
        }

    def get_account_summary(self) -> dict:
        pos = self.get_position_details()
        net_liq = self.cash + pos["market_value"]
        return {
            "TotalCashValue": self.cash,
            "NetLiquidation": net_liq,
            "GrossPositionValue": pos["market_value"],
            "UnrealizedPnL": pos["unrealized_pnl"],
            "RealizedPnL": self.realized_pnl,
            "BuyingPower": max(0.0, self.cash * 2),
        }

    def get_cash(self) -> float:
        return self.cash

    def get_realized_pnl(self) -> Optional[float]:
        """回测: 返回内部累计的 realized_pnl"""
        return self.realized_pnl
