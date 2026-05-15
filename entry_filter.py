"""
entry_filter.py — 入场筛选器

核心职责: 判断"现在"是否适合在当前标的上开一套当前策略周期的网格

两层判断：
  Layer 1 (CONDITION_CHECK): 市场状态评估
    - 趋势强度 (ADX < 25)
    - 波动率合理 (ATR/Price 在 0.5%-2.5%)
    - 不在事件窗口内 (财报前后)
    - 流动性充足
    
  Layer 2 (TIMING_CHECK): 入场时机判断 (条件满足后)
    - 价格在 EMA ± 0.5 ATR 内 (近"公允价值")
    - 当前K线不是大阴/大阳 (避免追高/抄底)

输出:
  EntryDecision 对象，包含:
    - allow_entry: bool
    - suggested_center: float
    - suggested_atr: float
    - entry_zone: (low, high)
    - rejection_reasons: list[str]
"""

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

import pandas as pd

import config
import indicators

logger = logging.getLogger("GridTrader.Entry")


@dataclass
class EntryEvaluation:
    """单次评估结果"""
    timestamp: datetime
    current_price: float

    # Layer 1: 条件检查
    conditions_passed: bool = False
    adx_value: float = 0
    atr_pct: float = 0
    bb_width_pct: float = 0
    ema_value: float = 0
    ema_distance_atr: float = 0

    # Layer 2: 时机检查
    timing_passed: bool = False

    # 建议参数
    suggested_center: float = 0
    suggested_atr: float = 0
    suggested_spacing_pct: float = 0
    entry_zone_low: float = 0
    entry_zone_high: float = 0

    # 失败原因
    rejection_reasons: list[str] = field(default_factory=list)

    @property
    def allow_entry(self) -> bool:
        return self.conditions_passed and self.timing_passed

    def __repr__(self):
        if self.allow_entry:
            return (f"EntryEval(✓ ALLOW @ ${self.current_price:.2f} | "
                    f"center=${self.suggested_center:.2f} | "
                    f"ATR={self.suggested_atr:.2f})")
        else:
            return f"EntryEval(✗ REJECT: {'; '.join(self.rejection_reasons[:2])})"


class EntryFilter:
    """入场筛选器"""

    def __init__(self):
        self.last_evaluation: Optional[EntryEvaluation] = None

    def evaluate(self, hourly_df: pd.DataFrame,
                 evaluation_time: datetime) -> EntryEvaluation:
        """
        基于当前策略周期K线数据评估当前是否适合入场
        
        Args:
            hourly_df: 至少包含最近50条策略周期K线 (Open, High, Low, Close, Volume)
        
        Returns:
            EntryEvaluation 对象
        """
        if len(hourly_df) < 50:
            eval_result = EntryEvaluation(
                timestamp=evaluation_time,
                current_price=0.0,
                rejection_reasons=["数据不足 (需要至少50条策略周期K线)"]
            )
            self.last_evaluation = eval_result
            return eval_result

        # 计算所有指标
        df = indicators.compute_all_indicators(
            hourly_df,
            ema_period=config.GRID_CENTER_EMA_PERIOD,
            atr_period=14,
            adx_period=14,
            bb_period=20,
        )

        return self.evaluate_precomputed(df, evaluation_time=evaluation_time)

    def evaluate_precomputed(self, df: pd.DataFrame,
                             evaluation_time: datetime) -> EntryEvaluation:
        """
        基于已计算好指标的DataFrame评估当前是否适合入场。
        要求最新一行已经包含 EMA/ATR/ADX/BB 等字段。
        """
        if len(df) < 50:
            eval_result = EntryEvaluation(
                timestamp=evaluation_time,
                current_price=0.0,
                rejection_reasons=["数据不足 (需要至少50条策略周期K线)"]
            )
            self.last_evaluation = eval_result
            return eval_result

        # 取最新一行
        latest = df.iloc[-1]
        current_price = float(latest["Close"])

        eval_result = EntryEvaluation(
            timestamp=evaluation_time,
            current_price=current_price,
            adx_value=float(latest["ADX"]),
            atr_pct=float(latest["ATR_PCT"]),
            bb_width_pct=float(latest["BB_WIDTH_PCT"]),
            ema_value=float(latest["EMA"]),
            ema_distance_atr=float(latest["EMA_DIST_ATR"]),
        )

        # ────── Layer 1: 条件检查 ──────
        eval_result.conditions_passed = self._check_conditions(
            eval_result,
            df,
            evaluation_time=evaluation_time,
        )

        # ────── Layer 2: 时机检查 (仅在条件通过时) ──────
        if eval_result.conditions_passed:
            self._compute_grid_params(eval_result, latest)
            eval_result.timing_passed = self._check_timing(eval_result, df)

        self.last_evaluation = eval_result

        if eval_result.allow_entry:
            logger.info(f"✅ 入场条件满足 | 价格${current_price:.2f} | "
                        f"中轴${eval_result.suggested_center:.2f} | "
                        f"ATR={eval_result.suggested_atr:.2f}")
        else:
            logger.debug(f"入场拒绝: {eval_result.rejection_reasons}")

        return eval_result

    # ──────────────────────────────
    #  Layer 1: 条件检查
    # ──────────────────────────────

    def _check_conditions(self, ev: EntryEvaluation, df: pd.DataFrame,
                          evaluation_time: datetime) -> bool:
        """检查整体市场条件是否适合开网格"""
        passed = True

        # 1. 趋势强度
        if ev.adx_value > config.ENTRY_MAX_ADX:
            ev.rejection_reasons.append(
                f"ADX={ev.adx_value:.1f} 超过上限 {config.ENTRY_MAX_ADX} (趋势过强)"
            )
            passed = False
        elif ev.adx_value < config.ENTRY_MIN_ADX:
            ev.rejection_reasons.append(
                f"ADX={ev.adx_value:.1f} 低于下限 {config.ENTRY_MIN_ADX} (无波动)"
            )
            passed = False

        # T1: ADX 斜率 > 阈值 → 拒绝. 防止入场在 ADX 上升早期 (即将 break-out).
        # ADX_slope > 0 意味着趋势在加强, 即使当前值低于 ENTRY_MAX_ADX 也是
        # "ranging 即将结束". 把这种 entry 拦掉能减少 BUY-only 单边下跌 session.
        lookback_adx = max(2, int(getattr(config, "ENTRY_ADX_SLOPE_LOOKBACK_BARS", 3)))
        max_slope = float(getattr(config, "ENTRY_MAX_ADX_SLOPE", -1.0))
        if max_slope >= -100 and "ADX" in df.columns and len(df) >= lookback_adx + 1:
            recent_adx = df["ADX"].tail(lookback_adx + 1)
            # 简单斜率: (今 - lookback 前) / lookback
            adx_slope = float(
                (recent_adx.iloc[-1] - recent_adx.iloc[0]) / lookback_adx
            )
            if adx_slope > max_slope:
                ev.rejection_reasons.append(
                    f"ADX 斜率={adx_slope:+.2f}/bar (近{lookback_adx}bar) "
                    f"> 阈值 {max_slope:+.2f} (趋势正在加强, 跳过入场)"
                )
                passed = False

        # 2. 波动率范围
        if ev.atr_pct < config.ENTRY_MIN_ATR_PCT:
            ev.rejection_reasons.append(
                f"ATR%={ev.atr_pct*100:.2f}% 低于下限 "
                f"{config.ENTRY_MIN_ATR_PCT*100:.1f}% (无利润空间)"
            )
            passed = False
        elif ev.atr_pct > config.ENTRY_MAX_ATR_PCT:
            ev.rejection_reasons.append(
                f"ATR%={ev.atr_pct*100:.2f}% 超过上限 "
                f"{config.ENTRY_MAX_ATR_PCT*100:.1f}% (波动过大)"
            )
            passed = False

        # 3. 布林带宽度
        if ev.bb_width_pct > config.ENTRY_MAX_BB_WIDTH_PCT:
            ev.rejection_reasons.append(
                f"BB宽度={ev.bb_width_pct*100:.1f}% 超过上限 "
                f"{config.ENTRY_MAX_BB_WIDTH_PCT*100:.0f}%"
            )
            passed = False

        # S3: 实际震荡幅度过滤 — 过去 N bar 的 (max High - min Low) / ATR 必须 ≥ 阈值.
        # ADX 是预测性指标 (有 lag, 经常在临界点选中正在 break 的 chop);
        # 用实际历史震荡幅度反过来要求"过去一段时间确实在 ranging"再考虑入场.
        # 设 ENTRY_MIN_RECENT_RANGE_ATR <= 0 关闭该 check.
        # 这里 suggested_atr 还没算 (_compute_grid_params 在后面跑), 用 atr_pct × price 推算.
        lookback = max(1, int(getattr(config, "ENTRY_RECENT_RANGE_LOOKBACK_BARS", 20)))
        min_ratio = float(getattr(config, "ENTRY_MIN_RECENT_RANGE_ATR", 0.0))
        atr_abs = ev.atr_pct * ev.current_price if ev.current_price > 0 else 0.0
        if min_ratio > 0 and atr_abs > 0 and len(df) >= lookback:
            recent = df.tail(lookback)
            recent_range = float(recent["High"].max() - recent["Low"].min())
            range_in_atr = recent_range / atr_abs
            if range_in_atr < min_ratio:
                ev.rejection_reasons.append(
                    f"近{lookback}bar震荡幅度={range_in_atr:.2f}×ATR "
                    f"低于下限 {min_ratio:.1f}×ATR (不在 ranging)"
                )
                passed = False

        # 3.5. 价格不能离EMA过远，否则只适合继续观察，不适合准备建仓
        if abs(ev.ema_distance_atr) > config.ENTRY_MAX_EMA_DEVIATION_ATR:
            ev.rejection_reasons.append(
                f"价格偏离EMA达 {abs(ev.ema_distance_atr):.2f}×ATR，"
                f"超过条件上限 {config.ENTRY_MAX_EMA_DEVIATION_ATR}×ATR"
            )
            passed = False

        # 4. 财报窗口
        in_blackout, blackout_msg = self._check_earnings_blackout(
            current_date=evaluation_time.date()
        )
        if in_blackout:
            ev.rejection_reasons.append(blackout_msg)
            passed = False

        # 5. 流动性 — 按日聚合, 与日成交额阈值直接对比 (单位一致)
        if config.STRATEGY_INTERVAL.endswith("d"):
            recent_daily_avg = float(
                (df["Volume"].tail(20) * df["Close"].tail(20)).mean()
            )
        else:
            bar_dollar = df["Volume"] * df["Close"]
            by_day = bar_dollar.groupby(df.index.date).sum()
            recent_daily_avg = float(by_day.tail(20).mean()) if len(by_day) > 0 else 0.0

        if recent_daily_avg < config.ENTRY_MIN_DAILY_VOLUME_USD:
            ev.rejection_reasons.append(
                f"流动性不足: 日均成交额 ${recent_daily_avg/1e6:.1f}M "
                f"(需 ${config.ENTRY_MIN_DAILY_VOLUME_USD/1e6:.1f}M)"
            )
            passed = False

        return passed

    def _check_earnings_blackout(self, current_date: Optional[date] = None) -> tuple[bool, str]:
        """检查是否在财报冻结期"""
        today = current_date or date.today()
        for earnings_str in config.EARNINGS_DATES:
            try:
                ed = date.fromisoformat(earnings_str)
            except ValueError:
                continue
            start = ed - timedelta(days=config.ENTRY_BLACKOUT_DAYS_BEFORE_EARNINGS)
            end = ed + timedelta(days=config.ENTRY_BLACKOUT_DAYS_AFTER_EARNINGS)
            if start <= today <= end:
                return True, f"财报冻结期 ({start} ~ {end}, 财报日 {ed})"
        return False, ""

    # ──────────────────────────────
    #  Layer 2: 时机检查
    # ──────────────────────────────

    def _compute_grid_params(self, ev: EntryEvaluation, latest: pd.Series):
        """计算建议的网格参数"""
        ev.suggested_center = float(latest["EMA"])
        ev.suggested_atr = float(latest["ATR"])

        # 间距 = 0.7 × (ATR / Price)，限制在 [0.8%, 4%]
        raw_spacing = config.GRID_SPACING_ATR_MULTIPLIER * ev.atr_pct
        ev.suggested_spacing_pct = max(
            config.GRID_MIN_SPACING_PCT,
            min(config.GRID_MAX_SPACING_PCT, raw_spacing)
        )

        # 入场区间 = 中轴 ± 0.5 × ATR
        band = config.ENTRY_PRICE_BAND_ATR * ev.suggested_atr
        ev.entry_zone_low = ev.suggested_center - band
        ev.entry_zone_high = ev.suggested_center + band

    def _check_timing(self, ev: EntryEvaluation, df: pd.DataFrame) -> bool:
        """检查当前是否是好的入场时机"""
        passed = True

        # 1. 价格在入场区间内
        if not (ev.entry_zone_low <= ev.current_price <= ev.entry_zone_high):
            distance = abs(ev.current_price - ev.suggested_center) / ev.suggested_atr
            ev.rejection_reasons.append(
                f"价格${ev.current_price:.2f} 偏离EMA ${ev.suggested_center:.2f} "
                f"达 {distance:.2f}×ATR (限制 {config.ENTRY_PRICE_BAND_ATR}×ATR)"
            )
            passed = False

        # 2. 当前K线不是异常波动
        latest = df.iloc[-1]
        bar_range = (latest["High"] - latest["Low"]) / latest["Close"]
        normal_range = ev.atr_pct * 1.5
        if bar_range > normal_range * 2:
            ev.rejection_reasons.append(
                f"当前K线波动 {bar_range*100:.1f}% 异常 (正常范围 {normal_range*100:.1f}%)"
            )
            passed = False

        # 3. 最近3根K线方向一致性 (避免追涨杀跌)
        recent3 = df.tail(4)["Close"]
        if len(recent3) >= 3:
            changes = recent3.pct_change().dropna()
            if all(c > 0.01 for c in changes):  # 连续3根涨幅 > 1%
                ev.rejection_reasons.append("最近3根K线连续大涨，疑似追高")
                passed = False
            elif all(c < -0.01 for c in changes):
                ev.rejection_reasons.append("最近3根K线连续大跌，疑似抄底")
                passed = False

        return passed

    # ──────────────────────────────
    #  辅助方法
    # ──────────────────────────────

    def format_evaluation_report(self, ev: EntryEvaluation) -> str:
        """生成可读的评估报告"""
        lines = []
        lines.append("=" * 60)
        status = "✅ 允许入场" if ev.allow_entry else "✗ 拒绝入场"
        lines.append(f"  入场评估 [{ev.timestamp.strftime('%Y-%m-%d %H:%M')}] - {status}")
        lines.append("=" * 60)
        lines.append(f"  当前价格: ${ev.current_price:.2f}")
        lines.append(f"")
        lines.append(f"  指标快照:")
        lines.append(f"    ADX(14):     {ev.adx_value:.1f}  (阈值<{config.ENTRY_MAX_ADX})")
        lines.append(f"    ATR/Price:   {ev.atr_pct*100:.2f}%  (范围 "
                     f"{config.ENTRY_MIN_ATR_PCT*100:.1f}%-{config.ENTRY_MAX_ATR_PCT*100:.1f}%)")
        lines.append(f"    BB宽度:      {ev.bb_width_pct*100:.1f}%  (阈值<{config.ENTRY_MAX_BB_WIDTH_PCT*100:.0f}%)")
        lines.append(f"    EMA(20):     ${ev.ema_value:.2f}")
        lines.append(f"    距EMA:       {ev.ema_distance_atr:+.2f}×ATR")

        if ev.conditions_passed:
            lines.append(f"")
            lines.append(f"  建议网格参数:")
            lines.append(f"    中轴:        ${ev.suggested_center:.2f}")
            lines.append(f"    ATR:         ${ev.suggested_atr:.2f}")
            lines.append(f"    档距:        {ev.suggested_spacing_pct*100:.2f}%")
            lines.append(f"    入场区间:    ${ev.entry_zone_low:.2f} ~ ${ev.entry_zone_high:.2f}")

        if ev.rejection_reasons:
            lines.append(f"")
            lines.append(f"  拒绝原因:")
            for r in ev.rejection_reasons:
                lines.append(f"    • {r}")

        lines.append("=" * 60)
        return "\n".join(lines)
