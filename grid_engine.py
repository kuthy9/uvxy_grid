"""
grid_engine.py — v2 动态网格引擎

主要变化 (vs v1):
  1. 中轴动态: 跟随20根策略周期EMA，超过阈值自动重置
  2. 间距动态: 基于ATR百分比，每次重置时重算
  3. 退出检测: 集成趋势判断，主动建议退出
  4. 状态完整: 支持持久化(JSON导出/导入)
"""

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Optional

import config

logger = logging.getLogger("GridTrader.Grid")


class GridSide(Enum):
    ABOVE = "above"
    BELOW = "below"


class LevelState(Enum):
    IDLE = "idle"
    ORDER_PENDING = "order_pending"
    FILLED = "filled"


@dataclass
class GridLevel:
    level_index: int
    side: GridSide
    price: float
    quantity: float
    state: LevelState = LevelState.IDLE
    order_id: Optional[int] = None
    filled_price: float = 0.0
    filled_time: str = ""


@dataclass
class GridSnapshot:
    """网格快照，用于持久化和报告"""
    center_price: float
    spacing_pct: float
    atr_at_init: float
    levels_above_count: int
    levels_below_count: int
    total_filled_buys: int
    total_filled_sells: int
    last_recenter_time: str
    grid_init_time: str


class DynamicGridEngine:
    """动态网格引擎"""

    def __init__(self, center_price: float, atr: float,
                 spacing_pct: float = None,
                 grid_capital: float = None,
                 current_time: Optional[datetime] = None):
        """
        初始化网格
        
        Args:
            center_price: 初始中轴价
            atr: 当前ATR (用于动态间距)
            spacing_pct: 间距百分比 (None=自动计算)
            grid_capital: 网格可用资金 (None=使用config默认)
        """
        self.center_price = center_price
        self.atr_at_init = atr
        self.spacing_pct = spacing_pct or self._compute_spacing(atr, center_price)
        self.grid_capital = grid_capital or (
            config.TOTAL_CAPITAL * config.GRID_CAPITAL_RATIO
        )

        self.levels: dict[int, GridLevel] = {}
        self.total_filled_buys = 0
        self.total_filled_sells = 0
        self.is_frozen = False
        self.freeze_reason = ""
        current_time = current_time or datetime.now()
        self.grid_init_time = current_time.isoformat()
        self.last_recenter_time = self.grid_init_time

        self._build_grid()

        logger.info(f"⚡ 动态网格初始化")
        logger.info(f"   中轴: ${center_price:.2f} | ATR: ${atr:.2f}")
        logger.info(f"   档距: {self.spacing_pct*100:.2f}% | 档数: {config.GRID_LEVELS}×2")
        logger.info(f"   网格资金: ${self.grid_capital:.2f}")

    @staticmethod
    def _compute_spacing(atr: float, price: float) -> float:
        """根据ATR计算间距百分比"""
        atr_pct = atr / price
        raw = config.GRID_SPACING_ATR_MULTIPLIER * atr_pct
        return max(config.GRID_MIN_SPACING_PCT,
                   min(config.GRID_MAX_SPACING_PCT, raw))

    def _build_grid(self):
        """根据当前中轴和间距构建所有档位"""
        per_level_capital = self.grid_capital / (config.GRID_LEVELS * 2)
        self.levels.clear()

        for i in range(1, config.GRID_LEVELS + 1):
            # 上方卖出档
            sell_price = self.center_price * (1 + self.spacing_pct * i)
            sell_qty = config.round_quantity(per_level_capital / sell_price)
            self.levels[i] = GridLevel(
                level_index=i,
                side=GridSide.ABOVE,
                price=round(sell_price, 2),
                quantity=sell_qty,
            )

            # 下方买入档
            buy_price = self.center_price * (1 - self.spacing_pct * i)
            if buy_price <= 0:
                continue
            buy_qty = config.round_quantity(per_level_capital / buy_price)
            self.levels[-i] = GridLevel(
                level_index=-i,
                side=GridSide.BELOW,
                price=round(buy_price, 2),
                quantity=buy_qty,
            )

    # ──────────────────────────────
    #  核心: 信号生成
    # ──────────────────────────────

    def check_signals(self, current_price: float) -> list[dict]:
        """检查当前价格触发的信号"""
        if self.is_frozen:
            return []

        signals = []
        for idx, lv in self.levels.items():
            if lv.state != LevelState.IDLE:
                continue

            order_value = lv.price * lv.quantity
            if order_value < config.MIN_ORDER_VALUE:
                continue

            if lv.side == GridSide.BELOW and current_price <= lv.price:
                signals.append({
                    "action": "BUY", "level_index": idx,
                    "price": lv.price, "quantity": lv.quantity,
                    "order_type": "GRID_BUY",
                })
            elif lv.side == GridSide.ABOVE and current_price >= lv.price:
                signals.append({
                    "action": "SELL", "level_index": idx,
                    "price": lv.price, "quantity": lv.quantity,
                    "order_type": "GRID_SELL",
                })

        return signals

    def check_filled_resets(self, current_price: float):
        """已成交档位的重置逻辑"""
        for idx, lv in self.levels.items():
            if lv.state != LevelState.FILLED:
                continue
            if lv.side == GridSide.BELOW and current_price >= self.center_price:
                lv.state = LevelState.IDLE
                lv.filled_price = 0.0
                lv.order_id = None
            elif lv.side == GridSide.ABOVE and current_price <= self.center_price:
                lv.state = LevelState.IDLE
                lv.filled_price = 0.0
                lv.order_id = None

    # ──────────────────────────────
    #  动态调整: 中轴和间距
    # ──────────────────────────────

    def should_recenter(self, current_price: float, current_ema: float,
                        current_atr: float,
                        current_time: Optional[datetime] = None) -> tuple[bool, str]:
        """
        判断是否应该重置中轴
        
        触发条件:
          1. EMA偏离当前中轴 > 1.5 × ATR
          2. 距上次重置 > 最小间隔时间
        """
        # 检查时间间隔（防止过频抖动）
        current_time = current_time or datetime.now()
        last_recenter = datetime.fromisoformat(self.last_recenter_time)
        strategy_hours = config.strategy_interval_hours()
        elapsed_bars = (current_time - last_recenter).total_seconds() / (strategy_hours * 3600)
        if elapsed_bars < config.GRID_RECENTER_MIN_BARS:
            return False, f"距上次重置仅{elapsed_bars:.1f} bars，等待"

        # 检查偏离度
        ema_drift = abs(current_ema - self.center_price)
        drift_in_atr = ema_drift / current_atr if current_atr > 0 else 0

        if drift_in_atr >= config.GRID_RECENTER_THRESHOLD_ATR:
            return True, (f"EMA${current_ema:.2f}偏离中轴${self.center_price:.2f} "
                          f"达{drift_in_atr:.2f}×ATR")

        return False, ""

    def recenter(self, new_center: float, new_atr: float,
                 active_order_ids: list[int] = None,
                 current_time: Optional[datetime] = None) -> dict:
        """
        重置中轴，重建所有档位
        
        Returns:
            重置信息字典 (含需要撤销的订单ID列表)
        """
        old_center = self.center_price
        old_spacing = self.spacing_pct

        # 收集需要撤销的订单
        orders_to_cancel = []
        for lv in self.levels.values():
            if lv.state == LevelState.ORDER_PENDING and lv.order_id:
                orders_to_cancel.append(lv.order_id)

        # 重新计算
        current_time = current_time or datetime.now()
        self.center_price = new_center
        self.atr_at_init = new_atr
        self.spacing_pct = self._compute_spacing(new_atr, new_center)
        self._build_grid()
        self.last_recenter_time = current_time.isoformat()

        logger.info(f"📐 网格重置")
        logger.info(f"   中轴: ${old_center:.2f} → ${new_center:.2f}")
        logger.info(f"   间距: {old_spacing*100:.2f}% → {self.spacing_pct*100:.2f}%")
        logger.info(f"   ATR: ${new_atr:.2f}")
        logger.info(f"   需撤销订单: {len(orders_to_cancel)} 个")

        return {
            "old_center": old_center,
            "new_center": new_center,
            "old_spacing": old_spacing,
            "new_spacing": self.spacing_pct,
            "orders_to_cancel": orders_to_cancel,
        }

    # ──────────────────────────────
    #  退出建议
    # ──────────────────────────────

    def should_exit(self, current_price: float, current_adx: float,
                    current_atr_pct: float) -> tuple[bool, str]:
        """
        判断是否应该退出网格模式
        """
        # 1. ADX超过退出阈值
        if current_adx > config.EXIT_MAX_ADX:
            return True, f"ADX={current_adx:.1f} > {config.EXIT_MAX_ADX} (趋势确立)"

        # 2. 波动率爆炸
        if current_atr_pct > config.EXIT_MAX_ATR_PCT:
            return True, (f"ATR%={current_atr_pct*100:.1f}% > "
                          f"{config.EXIT_MAX_ATR_PCT*100:.1f}% (波动爆炸)")

        # 3. 价格远离中轴
        deviation = abs(current_price - self.center_price)
        atr = self.atr_at_init
        if atr > 0 and deviation / atr > config.EXIT_PRICE_DEVIATION_ATR:
            return True, (f"价格${current_price:.2f}偏离中轴 "
                          f"{deviation/atr:.1f}×ATR (>{config.EXIT_PRICE_DEVIATION_ATR})")

        return False, ""

    # ──────────────────────────────
    #  订单状态管理
    # ──────────────────────────────

    def mark_order_placed(self, level_index: int, order_id: int):
        if level_index in self.levels:
            self.levels[level_index].state = LevelState.ORDER_PENDING
            self.levels[level_index].order_id = order_id

    def mark_order_filled(self, level_index: int, fill_price: float, fill_time: str):
        if level_index in self.levels:
            lv = self.levels[level_index]
            lv.state = LevelState.FILLED
            lv.filled_price = fill_price
            lv.filled_time = fill_time
            if lv.side == GridSide.BELOW:
                self.total_filled_buys += 1
            else:
                self.total_filled_sells += 1

    def mark_order_cancelled(self, level_index: int):
        if level_index in self.levels:
            self.levels[level_index].state = LevelState.IDLE
            self.levels[level_index].order_id = None

    def freeze(self, reason: str):
        self.is_frozen = True
        self.freeze_reason = reason
        logger.warning(f"🧊 网格冻结: {reason}")

    def unfreeze(self):
        self.is_frozen = False
        self.freeze_reason = ""
        logger.info("🔥 网格解冻")

    # ──────────────────────────────
    #  状态查询和持久化
    # ──────────────────────────────

    def get_snapshot(self) -> GridSnapshot:
        return GridSnapshot(
            center_price=self.center_price,
            spacing_pct=self.spacing_pct,
            atr_at_init=self.atr_at_init,
            levels_above_count=sum(1 for lv in self.levels.values()
                                   if lv.side == GridSide.ABOVE),
            levels_below_count=sum(1 for lv in self.levels.values()
                                   if lv.side == GridSide.BELOW),
            total_filled_buys=self.total_filled_buys,
            total_filled_sells=self.total_filled_sells,
            last_recenter_time=self.last_recenter_time,
            grid_init_time=self.grid_init_time,
        )

    def get_grid_summary(self) -> str:
        """文字形式的网格布局"""
        lines = [
            "",
            f"{'='*55}",
            f"  动态网格 | 中轴 ${self.center_price:.2f} | "
            f"档距 {self.spacing_pct*100:.2f}% | ATR ${self.atr_at_init:.2f}",
            f"{'='*55}",
        ]
        for i in range(config.GRID_LEVELS, 0, -1):
            lv = self.levels[i]
            lines.append(f"  ▲ +{i} | ${lv.price:>8.2f} | "
                         f"卖 {lv.quantity:.4f}股 | {lv.state.value}")
        lines.append(f"  ── 中轴 ── ${self.center_price:.2f} ──")
        for i in range(1, config.GRID_LEVELS + 1):
            lv = self.levels[-i]
            lines.append(f"  ▼ -{i} | ${lv.price:>8.2f} | "
                         f"买 {lv.quantity:.4f}股 | {lv.state.value}")
        lines.append(f"{'='*55}")
        return "\n".join(lines)

    def save_state(self, path: str):
        """保存状态到JSON"""
        state = {
            "center_price": self.center_price,
            "atr_at_init": self.atr_at_init,
            "spacing_pct": self.spacing_pct,
            "grid_capital": self.grid_capital,
            "total_filled_buys": self.total_filled_buys,
            "total_filled_sells": self.total_filled_sells,
            "is_frozen": self.is_frozen,
            "freeze_reason": self.freeze_reason,
            "grid_init_time": self.grid_init_time,
            "last_recenter_time": self.last_recenter_time,
            "levels": {
                str(idx): {
                    "level_index": lv.level_index,
                    "side": lv.side.value,
                    "price": lv.price,
                    "quantity": lv.quantity,
                    "state": lv.state.value,
                    "order_id": lv.order_id,
                    "filled_price": lv.filled_price,
                    "filled_time": lv.filled_time,
                }
                for idx, lv in self.levels.items()
            }
        }
        with open(path, "w") as f:
            json.dump(state, f, indent=2)

    @classmethod
    def load_state(cls, path: str) -> "DynamicGridEngine":
        """从JSON恢复状态"""
        with open(path) as f:
            state = json.load(f)

        engine = cls.__new__(cls)
        engine.center_price = state["center_price"]
        engine.atr_at_init = state["atr_at_init"]
        engine.spacing_pct = state["spacing_pct"]
        engine.grid_capital = state["grid_capital"]
        engine.total_filled_buys = state["total_filled_buys"]
        engine.total_filled_sells = state["total_filled_sells"]
        engine.is_frozen = state["is_frozen"]
        engine.freeze_reason = state["freeze_reason"]
        engine.grid_init_time = state["grid_init_time"]
        engine.last_recenter_time = state["last_recenter_time"]
        engine.levels = {}
        for idx_str, lv_data in state["levels"].items():
            engine.levels[int(idx_str)] = GridLevel(
                level_index=lv_data["level_index"],
                side=GridSide(lv_data["side"]),
                price=lv_data["price"],
                quantity=lv_data["quantity"],
                state=LevelState(lv_data["state"]),
                order_id=lv_data["order_id"],
                filled_price=lv_data["filled_price"],
                filled_time=lv_data["filled_time"],
            )
        return engine
