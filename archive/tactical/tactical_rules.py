"""
tactical_rules.py — 战术网格的纯策略规则

设计原则:
  - 所有函数都是 pure: 输入 → 输出, 无副作用 (不读 wall-clock, 不写 DB, 不下单)
  - 业务模块 (grid_bot / session_manager) 收集所需上下文后调用这里, 这样:
      * 回测和实盘共用同一套规则 (与 GridBot 的设计一致)
      * 单元测试可以直接喂任意输入, 无需 mock 整个系统
  - 不依赖具体的 SessionManager 实例形态, 输入用 dataclass-like dict / 命名参数

只暴露纯函数 + 几个轻量 dataclass 装载评估上下文.

主要 API:
  - calculate_trend_risk_score(ctx) -> float (0-100)
  - calculate_confidence(ctx) -> str (low/normal/high)
  - should_enter_defensive(session, ctx) -> tuple[bool, str]
  - should_force_exit(session, ctx) -> tuple[bool, str]
  - should_protect_profit(session) -> tuple[bool, str, dict]  (含 partial_exit_ratio 等动作)
  - should_allow_buy(session, ctx) -> tuple[bool, str]
  - should_allow_sell(session, ctx) -> tuple[bool, str]
  - should_disable_recenter(session, ctx) -> tuple[bool, str]
  - calculate_dynamic_position_cap(confidence) -> tuple[float, int]  (pct, max_buy_levels)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import tactical_config as tcfg


# ════════════════════════════════════════════
#  Trend risk score 加权常数 (0-100 分制)
#
#  每个 component 的 max 贡献写在 _MAX_*; 缩放系数写在 _GAIN_*.
#  这些是评分函数的"形状参数", 调参不是为了 alpha (那归 tcfg 配置), 而是为了
#  保证 (a) 各分项贡献相对合理, (b) 极端市场总分能逼近 100, (c) 普通市场不噪音化.
#  改这些值会改变 DEFENSIVE / FORCE_EXIT 阈值的触发难度, 等同于改 tcfg 阈值,
#  请通过回测验证后再调.
# ════════════════════════════════════════════

# ADX (趋势强度)
_RISK_ADX_BASE_SCORE = 15.0     # 达到阈值的起步分
_RISK_ADX_MAX_SCORE = 25.0       # ADX 极强时的封顶
_RISK_ADX_OVER_GAIN = 0.8        # 阈值之上每 1 ADX = 0.8 分
_RISK_ADX_UNDER_FRAC = 0.7       # ADX 至少要到阈值的 70% 才开始计入

# EMA slope (向下)
_RISK_EMA_SLOPE_MAX = 15.0
_RISK_EMA_SLOPE_GAIN = 750.0     # 折算: |slope|=2% → 15 分, |slope|=0.5% → ~3.75 分

# 价格低于 EMA 的连续 bar
_RISK_PRICE_BELOW_EMA_MAX = 10.0

# 连续下跌 bar
_RISK_CONSEC_DOWN_MAX = 20.0

# 价格远离 grid_center (向下)
_RISK_DEVIATION_MAX = 15.0
_RISK_DEVIATION_FULL_ATR = 3.0   # 距 center 3×ATR 以下封顶
_RISK_DEVIATION_GAIN = 5.0       # 每 1×ATR 加 5 分

# ATR 扩张
_RISK_ATR_EXPANSION_MAX = 15.0
_RISK_ATR_EXPANSION_GAIN = 15.0  # (expansion - 1.0) * 15
_RISK_ATR_EXPANSION_TRIGGER = 1.5  # ATR 较入场扩张 50% 以上才开始计入

# Confidence bonus: 稳定市场加分
_CONFIDENCE_STABILITY_BONUS = 10.0
_CONFIDENCE_STABILITY_PRICE_BAND_ATR = 0.5
_CONFIDENCE_STABILITY_ADX_FRAC = 0.6
_CONFIDENCE_DEFENSIVE_RISK_FRAC = 0.8  # risk >= 80% of DEFENSIVE threshold → LOW


# ════════════════════════════════════════════
#  输入上下文 (来自 grid_bot 拼装)
# ════════════════════════════════════════════

@dataclass
class MarketContext:
    """市场层面的瞬时上下文 — grid_bot 每个 step 计算一次, 喂给规则函数."""
    current_price: float = 0.0
    ema: float = 0.0
    atr: float = 0.0
    atr_pct: float = 0.0
    adx: float = 0.0
    grid_center: float = 0.0

    # EMA 斜率: (ema_now - ema_lookback) / ema_lookback, 单位无量纲
    ema_slope: float = 0.0
    # 连续下跌 / 价格低于 EMA 的连续 bar 数
    consecutive_down_bars: int = 0
    price_below_ema_bars: int = 0
    # 近期 N 根 bar 的 momentum / return, 仅用于评分微调
    recent_return: float = 0.0
    # ATR 扩张: 当前 ATR / 入场时的 ATR
    atr_expansion: float = 1.0


@dataclass
class SessionStateView:
    """SessionManager 对外暴露给 rules 用的"快照视图".
    避免 rules 直接依赖 SessionManager 类形态."""
    session_id: str = ""
    started_at: str = ""
    age_bars: float = 0.0
    start_equity: float = 0.0
    start_price: float = 0.0
    current_equity: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    total_pnl: float = 0.0
    peak_pnl: float = 0.0
    max_drawdown: float = 0.0
    max_position_value: float = 0.0
    position_value: float = 0.0
    filled_buy_levels: list[int] = field(default_factory=list)  # 已成交的下方档 (-1/-2/...)
    mode: str = tcfg.SESSION_MODE_OFFENSIVE
    recenter_used_count: int = 0  # session 内已用过的 recenter 次数 (S1 用)

    @property
    def total_pnl_pct(self) -> float:
        if self.start_equity <= 0:
            return 0.0
        return self.total_pnl / self.start_equity

    @property
    def peak_pnl_pct(self) -> float:
        if self.start_equity <= 0:
            return 0.0
        return self.peak_pnl / self.start_equity


# ════════════════════════════════════════════
#  评分函数
# ════════════════════════════════════════════

def calculate_trend_risk_score(ctx: MarketContext) -> float:
    """单边趋势风险评分 (0-100), 越大越危险.

    各 component 加权 (总和约 100):
      * ADX 超阈值: 0-25
      * EMA 斜率向下: 0-15
      * 价格低于 EMA: 0-10
      * 连续下跌 bar: 0-20
      * 价格远离 grid_center (向下): 0-15
      * ATR 扩张: 0-15

    这只是一个启发式打分, 不应被当作精确预测; 用于 DEFENSIVE / FORCE_EXIT 阈值判定.
    """
    score = 0.0

    # 1. ADX (趋势强度)
    if ctx.adx > 0:
        if ctx.adx >= tcfg.TREND_RISK_ADX_THRESHOLD:
            score += min(
                _RISK_ADX_MAX_SCORE,
                _RISK_ADX_BASE_SCORE
                + (ctx.adx - tcfg.TREND_RISK_ADX_THRESHOLD) * _RISK_ADX_OVER_GAIN,
            )
        elif ctx.adx >= tcfg.TREND_RISK_ADX_THRESHOLD * _RISK_ADX_UNDER_FRAC:
            # 接近阈值但未到, 按比例给一点分 (≤ base 的一半)
            score += (ctx.adx / tcfg.TREND_RISK_ADX_THRESHOLD) * (_RISK_ADX_BASE_SCORE * 0.5)

    # 2. EMA 斜率向下 (slope 为负代表 EMA 在下行)
    if ctx.ema_slope < 0:
        score += min(_RISK_EMA_SLOPE_MAX, abs(ctx.ema_slope) * _RISK_EMA_SLOPE_GAIN)

    # 3. 价格低于 EMA 的连续 bar
    threshold_bars = max(1, tcfg.TREND_RISK_PRICE_BELOW_EMA_BARS)
    if ctx.price_below_ema_bars >= threshold_bars:
        score += _RISK_PRICE_BELOW_EMA_MAX
    elif ctx.price_below_ema_bars >= 1:
        score += float(ctx.price_below_ema_bars) * (_RISK_PRICE_BELOW_EMA_MAX / threshold_bars)

    # 4. 连续下跌 bar
    consec_threshold = max(1, tcfg.TREND_RISK_CONSECUTIVE_DOWN_BARS)
    if ctx.consecutive_down_bars >= consec_threshold:
        score += _RISK_CONSEC_DOWN_MAX
    elif ctx.consecutive_down_bars > 0:
        score += float(ctx.consecutive_down_bars) * (_RISK_CONSEC_DOWN_MAX / consec_threshold)

    # 5. 价格远离 grid_center (向下)
    if ctx.grid_center > 0 and ctx.atr > 0 and ctx.current_price < ctx.grid_center:
        deviation_atr = (ctx.grid_center - ctx.current_price) / ctx.atr
        if deviation_atr >= _RISK_DEVIATION_FULL_ATR:
            score += _RISK_DEVIATION_MAX
        elif deviation_atr >= 1.0:
            score += deviation_atr * _RISK_DEVIATION_GAIN

    # 6. ATR 扩张
    if ctx.atr_expansion >= _RISK_ATR_EXPANSION_TRIGGER:
        score += min(
            _RISK_ATR_EXPANSION_MAX,
            (ctx.atr_expansion - 1.0) * _RISK_ATR_EXPANSION_GAIN,
        )

    return min(100.0, max(0.0, score))


def calculate_confidence(ctx: MarketContext) -> str:
    """LOW / NORMAL / HIGH — 决定单 session 仓位上限.

    思路:
      - ADX 低 + 稳定 + ATR 适中 + 价格靠近 EMA → HIGH
      - 任意明显异常 (ADX 高 / 价格远离 EMA / ATR 扩张 / 连续单边) → LOW
      - 其他 → NORMAL
    """
    # 用 trend_risk 反向估算: 风险高 → 信心低
    risk = calculate_trend_risk_score(ctx)
    if risk >= tcfg.TREND_RISK_SCORE_DEFENSIVE * _CONFIDENCE_DEFENSIVE_RISK_FRAC:
        return tcfg.CONFIDENCE_LOW

    confidence_score = 100.0 - risk
    # 稳定性奖励: 价格在 EMA ± 0.5 ATR 内 + ADX 低 + 无连续下跌 → bonus
    if (ctx.atr > 0 and ctx.ema > 0
            and abs(ctx.current_price - ctx.ema)
                <= _CONFIDENCE_STABILITY_PRICE_BAND_ATR * ctx.atr
            and ctx.adx < tcfg.TREND_RISK_ADX_THRESHOLD * _CONFIDENCE_STABILITY_ADX_FRAC
            and ctx.consecutive_down_bars == 0):
        confidence_score += _CONFIDENCE_STABILITY_BONUS

    if confidence_score >= tcfg.CONFIDENCE_HIGH_SCORE:
        return tcfg.CONFIDENCE_HIGH
    if confidence_score <= tcfg.CONFIDENCE_LOW_SCORE:
        return tcfg.CONFIDENCE_LOW
    return tcfg.CONFIDENCE_NORMAL


#  LOW confidence 下相对 NORMAL 的收紧系数: 仓位减半 + 档位减 1.
#  保留为模块常量, 避免散落"魔法数字". 调整时需通过回测验证.
_LOW_CONFIDENCE_POSITION_RATIO = 0.5
_LOW_CONFIDENCE_LEVELS_DELTA = 1
_LOW_CONFIDENCE_MIN_LEVELS = 1


def calculate_dynamic_position_cap(confidence: str) -> tuple[float, int]:
    """根据 confidence 返回 (max_position_value_pct, max_buy_levels).

    DEFENSIVE 模式下 caller 应直接用 SESSION_MAX_BUY_LEVELS_DEFENSIVE (0), 不走这里.
    """
    if confidence == tcfg.CONFIDENCE_HIGH:
        return (
            tcfg.SESSION_MAX_POSITION_VALUE_PCT_HIGH_CONFIDENCE,
            tcfg.SESSION_MAX_BUY_LEVELS_HIGH_CONFIDENCE,
        )
    if confidence == tcfg.CONFIDENCE_LOW:
        return (
            tcfg.SESSION_MAX_POSITION_VALUE_PCT_NORMAL * _LOW_CONFIDENCE_POSITION_RATIO,
            max(_LOW_CONFIDENCE_MIN_LEVELS,
                tcfg.SESSION_MAX_BUY_LEVELS_NORMAL - _LOW_CONFIDENCE_LEVELS_DELTA),
        )
    return (
        tcfg.SESSION_MAX_POSITION_VALUE_PCT_NORMAL,
        tcfg.SESSION_MAX_BUY_LEVELS_NORMAL,
    )


# ════════════════════════════════════════════
#  状态决策函数
# ════════════════════════════════════════════

def should_enter_defensive(
    session: SessionStateView,
    ctx: MarketContext,
    trend_risk_score: Optional[float] = None,
) -> tuple[bool, str]:
    """是否应该从 OFFENSIVE 切到 DEFENSIVE."""
    if session.mode != tcfg.SESSION_MODE_OFFENSIVE:
        return False, ""

    # 软止损
    if session.start_equity > 0:
        loss_pct = -session.total_pnl_pct  # 负 pnl_pct → 正 loss_pct
        if loss_pct >= tcfg.SESSION_SOFT_STOP_PCT:
            return True, (
                f"软止损 session_pnl={session.total_pnl:+.2f} "
                f"({loss_pct*100:.2f}%) >= {tcfg.SESSION_SOFT_STOP_PCT*100:.1f}%"
            )

    # 趋势风险分
    risk = trend_risk_score if trend_risk_score is not None else calculate_trend_risk_score(ctx)
    if risk >= tcfg.TREND_RISK_SCORE_DEFENSIVE:
        return True, f"趋势风险评分 {risk:.1f} >= {tcfg.TREND_RISK_SCORE_DEFENSIVE:.0f}"

    # 单一硬触发: ADX 超阈值 且价格低于 EMA
    if (ctx.adx >= tcfg.TREND_RISK_ADX_THRESHOLD
            and ctx.ema > 0 and ctx.current_price < ctx.ema):
        return True, (
            f"ADX={ctx.adx:.1f} >= {tcfg.TREND_RISK_ADX_THRESHOLD:.0f} "
            f"且价格${ctx.current_price:.2f} < EMA${ctx.ema:.2f}"
        )

    # 持仓时间 + 未盈利
    if (session.age_bars > tcfg.SESSION_MAX_AGE_BARS
            and session.total_pnl < tcfg.SESSION_MIN_PROFIT_TO_PROTECT_PCT * session.start_equity):
        return True, (
            f"age={session.age_bars:.1f}bars > {tcfg.SESSION_MAX_AGE_BARS} "
            f"且未达到最小盈利保护线"
        )

    return False, ""


def should_force_exit(
    session: SessionStateView,
    ctx: MarketContext,
    trend_risk_score: Optional[float] = None,
) -> tuple[bool, str]:
    """是否应该强制进入 EXIT_PENDING (跳过 DEFENSIVE).

    覆盖 grid_engine.should_exit 旧路径的三个触发 (战术 override 把它们静音了,
    这里独立提供等价兜底, 但不绑定特定 grid_center 实例):
      - ADX 通过 trend_risk_score 隐式覆盖
      - ATR% 爆炸 → 独立检查 (S6)
      - 持仓未实现亏损过大 → 独立检查 (S6, 救 BUY-only 一路跌的 session)
    """
    # 硬止损
    if session.start_equity > 0:
        loss_pct = -session.total_pnl_pct
        if loss_pct >= tcfg.SESSION_HARD_STOP_PCT:
            return True, (
                f"硬止损 session_pnl={session.total_pnl:+.2f} "
                f"({loss_pct*100:.2f}%) >= {tcfg.SESSION_HARD_STOP_PCT*100:.1f}%"
            )

    # S6.1: 持仓 unrealized 亏损 > X% of position_value → force_exit.
    # 修补 "BUY 填了几档, 价格一路跌, soft/hard_stop 因为账户分母够大没触发"
    # 这个老问题. 默认 SESSION_POSITION_DRAWDOWN_STOP_PCT=0.05, 0 关闭.
    pos_value = max(session.position_value, 0.0)
    if (tcfg.SESSION_POSITION_DRAWDOWN_STOP_PCT > 0
            and pos_value > 0
            and session.unrealized_pnl < 0):
        pos_loss_pct = -session.unrealized_pnl / pos_value
        if pos_loss_pct >= tcfg.SESSION_POSITION_DRAWDOWN_STOP_PCT:
            return True, (
                f"持仓 unrealized 亏损 {session.unrealized_pnl:+.2f} / "
                f"持仓市值{pos_value:.2f} = {pos_loss_pct*100:.2f}% "
                f">= {tcfg.SESSION_POSITION_DRAWDOWN_STOP_PCT*100:.1f}%"
            )

    # 趋势分极端
    risk = trend_risk_score if trend_risk_score is not None else calculate_trend_risk_score(ctx)
    if risk >= tcfg.TREND_RISK_SCORE_FORCE_EXIT:
        return True, f"趋势风险评分 {risk:.1f} >= {tcfg.TREND_RISK_SCORE_FORCE_EXIT:.0f}"

    # S6.2: ATR% 爆炸独立触发 (战术 override 把 grid_engine.should_exit 静音了,
    # trend_risk_score 里 ATR_expansion 只占 0-15 分权重, 单独的 ATR% 爆炸可能
    # 不足以推过 FORCE_EXIT 阈值, 所以这里独立兜底).
    if ctx.atr > 0 and ctx.current_price > 0:
        atr_pct_now = ctx.atr / ctx.current_price
        if (tcfg.SESSION_ATR_PCT_EXPLOSION_STOP > 0
                and atr_pct_now >= tcfg.SESSION_ATR_PCT_EXPLOSION_STOP):
            return True, (
                f"ATR% 爆炸 {atr_pct_now*100:.2f}% >= "
                f"{tcfg.SESSION_ATR_PCT_EXPLOSION_STOP*100:.1f}% (波动失控)"
            )

    # 绝对最大持仓时间
    if session.age_bars > tcfg.SESSION_ABSOLUTE_MAX_AGE_BARS:
        return True, (
            f"age={session.age_bars:.1f}bars > "
            f"SESSION_ABSOLUTE_MAX_AGE_BARS={tcfg.SESSION_ABSOLUTE_MAX_AGE_BARS}"
        )

    # S2: no_fill_timeout — age >= N AND filled_buy_levels 为空 → 早退
    # 救"入场后价格直接漂走 / grid 触不到 -1 档"的 session, 释放 age 预算回 SCANNING.
    if (tcfg.SESSION_NO_FILL_TIMEOUT_BARS > 0
            and session.age_bars >= tcfg.SESSION_NO_FILL_TIMEOUT_BARS
            and len(session.filled_buy_levels) == 0):
        return True, (
            f"no_fill_timeout: age={session.age_bars:.1f}bars >= "
            f"{tcfg.SESSION_NO_FILL_TIMEOUT_BARS} 且 0 成交"
        )

    return False, ""


def should_protect_profit(session: SessionStateView) -> tuple[bool, str, dict]:
    """盈利保护决策.

    返回 (action_needed, reason, details), 其中 details 可能包含:
      - "action": "exit" | "partial_exit"
      - "partial_exit_ratio": float  (仅 action == partial_exit 时)

    判定:
      1. 已触发 STRONG_PROFIT 阶段 + 当前 total_pnl 比 peak_pnl 回吐超过 trailing → exit
      2. 已触发 MIN_PROFIT 阶段 + 当前 total_pnl 比 peak_pnl 回吐超过 trailing → exit
      3. total_pnl 达到 STRONG_PROFIT_PCT 且 peak_pnl 还在 STRONG_PROFIT 区域 → partial_exit
    """
    if session.start_equity <= 0:
        return False, "", {}

    pnl_pct = session.total_pnl_pct
    peak_pct = session.peak_pnl_pct

    # Trailing giveback 判定 (peak 必须高于最小保护线)
    if peak_pct >= tcfg.SESSION_MIN_PROFIT_TO_PROTECT_PCT and session.peak_pnl > 0:
        giveback = session.peak_pnl - session.total_pnl
        if giveback > 0:
            giveback_ratio = giveback / session.peak_pnl
            if giveback_ratio >= tcfg.SESSION_TRAILING_GIVEBACK_RATIO:
                return True, (
                    f"盈利保护退出: peak=${session.peak_pnl:+.2f} 回吐 "
                    f"{giveback_ratio*100:.1f}% (>{tcfg.SESSION_TRAILING_GIVEBACK_RATIO*100:.0f}%) → "
                    f"当前${session.total_pnl:+.2f}"
                ), {"action": "exit", "reason_code": "trailing_giveback"}

    # 强盈利部分减仓
    if pnl_pct >= tcfg.SESSION_STRONG_PROFIT_PCT and session.position_value > 0:
        return True, (
            f"强盈利部分减仓 pnl_pct={pnl_pct*100:.2f}% "
            f">= {tcfg.SESSION_STRONG_PROFIT_PCT*100:.2f}%"
        ), {
            "action": "partial_exit",
            "partial_exit_ratio": tcfg.SESSION_STRONG_PROFIT_PARTIAL_EXIT_RATIO,
            "reason_code": "strong_profit_partial",
        }

    return False, "", {}


def should_allow_buy(
    session: SessionStateView,
    level_index: int,
    ctx: MarketContext,
    confidence: Optional[str] = None,
) -> tuple[bool, str]:
    """逐 BUY 信号过滤. 给 grid_bot 在下单前调用.

    检查:
      - DEFENSIVE 模式: 永远禁
      - level_index 必须 <= max_buy_levels (绝对值口径: -1 是最浅, -6 是最深)
      - session 当前仓位 <= max_position_value_pct * equity
      - SESSION_MAX_AGE: 进入 DEFENSIVE 不靠这里 (由 should_enter_defensive 触发),
        但临界时降低进攻性 (此处保持不拒, 由 caller 自行调用 should_enter_defensive)
    """
    if session.mode == tcfg.SESSION_MODE_DEFENSIVE:
        return False, "DEFENSIVE 模式禁止 BUY"
    if session.mode == tcfg.SESSION_MODE_EXITING:
        return False, "EXIT_PENDING 中禁止 BUY"
    if session.mode == tcfg.SESSION_MODE_COOLDOWN:
        return False, "COOLDOWN 中禁止 BUY"

    conf = confidence or calculate_confidence(ctx)
    pct_cap, max_levels = calculate_dynamic_position_cap(conf)

    # level_index 是负数, abs() 比较档数
    if abs(level_index) > max_levels:
        return False, (
            f"BUY 档位 {level_index} 超过 confidence={conf} 下的最大 {max_levels} 层"
        )

    # 仓位上限
    if session.start_equity > 0 and session.position_value > 0:
        equity = session.current_equity or session.start_equity
        cap = equity * pct_cap
        if session.position_value >= cap:
            return False, (
                f"session 仓位市值 ${session.position_value:,.0f} 已达 "
                f"{conf} 上限 ${cap:,.0f} ({pct_cap*100:.0f}%)"
            )

    # T3: 距 grid_center 太远不再加 BUY (不是 stop, 是"不再加深").
    # 防止价格一路下跌时无限加仓 — 当价格已经离中轴 >=N×ATR 时拒绝任何新 BUY,
    # 已有 BUY 不动. 配合 trend_risk_score 的 DEFENSIVE (65) 形成双层防线.
    # 默认 1.5×ATR, 设 0 关闭.
    if (tcfg.TACTICAL_MAX_BUY_DEPTH_ATR > 0
            and ctx.grid_center > 0 and ctx.atr > 0
            and ctx.current_price < ctx.grid_center):
        depth_atr = (ctx.grid_center - ctx.current_price) / ctx.atr
        if depth_atr >= tcfg.TACTICAL_MAX_BUY_DEPTH_ATR:
            return False, (
                f"距中轴 {depth_atr:.2f}×ATR >= {tcfg.TACTICAL_MAX_BUY_DEPTH_ATR:.1f}, "
                f"不再加深 (T3)"
            )

    return True, ""


def should_allow_sell(
    session: SessionStateView,
    level_index: int,
    ctx: MarketContext,
) -> tuple[bool, str]:
    """逐 SELL 信号过滤.

    OFFENSIVE / DEFENSIVE 都允许 SELL.
    DEFENSIVE 下还有反弹幅度过滤: 价格必须距离 grid_center 至少 N×ATR.
    """
    if session.mode == tcfg.SESSION_MODE_EXITING:
        # EXIT 由专门路径处理, 不走信号
        return False, "EXIT_PENDING 中, SELL 由专门路径处理"
    if session.mode == tcfg.SESSION_MODE_COOLDOWN:
        return False, "COOLDOWN 中无持仓信号"

    if session.mode == tcfg.SESSION_MODE_DEFENSIVE:
        if tcfg.DEFENSIVE_ALLOW_SELL_ONLY is False:
            return False, "DEFENSIVE 禁用所有交易 (DEFENSIVE_ALLOW_SELL_ONLY=False)"
        # 反弹幅度过滤
        if (ctx.grid_center > 0 and ctx.atr > 0
                and ctx.current_price < ctx.grid_center):
            shortfall_atr = (ctx.grid_center - ctx.current_price) / ctx.atr
            if shortfall_atr > tcfg.DEFENSIVE_MIN_REBOUND_ATR_TO_SELL:
                return False, (
                    f"DEFENSIVE: 价格${ctx.current_price:.2f} 比 center "
                    f"${ctx.grid_center:.2f} 低 {shortfall_atr:.2f}×ATR, "
                    f"未到反弹卖出位"
                )

    return True, ""


def should_disable_recenter(
    session: SessionStateView,
    soft_stop_triggered: bool = False,
) -> tuple[bool, str]:
    """战术模式下默认禁用 recenter, 避免 "续命" 失败 session."""
    if not tcfg.TACTICAL_GRID_ENABLED:
        return False, ""
    if (session.mode == tcfg.SESSION_MODE_DEFENSIVE
            and tcfg.TACTICAL_DISABLE_RECENTER_IN_DEFENSIVE):
        return True, "DEFENSIVE 下禁用 recenter"
    if soft_stop_triggered and tcfg.TACTICAL_DISABLE_RECENTER_AFTER_SOFT_STOP:
        return True, "软止损后禁用 recenter"
    if (session.mode == tcfg.SESSION_MODE_OFFENSIVE
            and not tcfg.TACTICAL_RECENTER_ALLOWED_IN_OFFENSIVE):
        # S1: OFFENSIVE 默认禁 recenter, 但放行 "首次零成交 + age>=N" 一次
        # 用途: 入场后价格漂走, 网格触不到 -1 档, 给一次追价机会
        allow_rescue = (
            tcfg.TACTICAL_OFFENSIVE_RESCUE_RECENTER_ENABLED
            and session.recenter_used_count == 0
            and len(session.filled_buy_levels) == 0
            and session.age_bars >= tcfg.TACTICAL_OFFENSIVE_RESCUE_MIN_AGE_BARS
        )
        if allow_rescue:
            return False, ""
        return True, "OFFENSIVE 默认禁用 recenter (TACTICAL_RECENTER_ALLOWED_IN_OFFENSIVE=False)"
    return False, ""
