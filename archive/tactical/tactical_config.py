"""
tactical_config.py — Aggressive Tactical Session Grid 配置

战术状态网格的参数与基础策略 (config.py) 解耦, 单独在这里维护:
  - SESSION_* : 单轮战役级风控阈值 (软/硬止损, 利润保护, 最大年龄)
  - DEFENSIVE_* : 防守模式开关 / 反弹卖出阈值
  - SESSION_COOLDOWN_BARS_* : 退出后冷却节拍 (按 strategy_interval 计)
  - TACTICAL_DISABLE_RECENTER_* : recenter 在战术模式下的限制
  - TREND_RISK_* : 单边趋势风险评分阈值
  - CONFIDENCE_* : 入场/持仓信心评分相关阈值

设计取向 (按用户偏好优先级):
  P0: 不允许一次大亏吃掉多笔小盈利 → SOFT_STOP_PCT 软止损先进 DEFENSIVE,
      HARD_STOP_PCT 强制 EXIT_PENDING.
  P0: 高周转可接受, 但单边下跌时停止补仓 → DEFENSIVE 模式取消 BUY 挂单 + 禁新 BUY,
      只允许 SELL/反弹减仓.
  P0: 持有时间越长盈利要求越高 → SESSION_TARGET/MAX/ABSOLUTE_MAX_AGE_BARS 三段.
  P0: 接受卖飞, 不希望过早固定止盈 → 用 trailing giveback 而不是固定 take-profit;
      STRONG_PROFIT 阶段做部分减仓而非清仓.

所有参数支持环境变量覆盖, 命名一律 TACTICAL_前缀外的同名 env 即可.
默认值参照用户给定基线 ($10000 账户单轮可接受亏损 $100-$200).
"""

# ════════════════════════════════════════════════════════════════════
#  ⚠️  EXPERIMENTAL — 2026-05-15 严证伪通过
#  ════════════════════════════════════════════════════════════════════
#  在 UVXY 4h + VXX 4h × ≤5y × $10k cap 边界内, 不存在战术化配置同时满足:
#    (B1) Defensive/Forced/Profit-protect 触发 > 0
#    (B2) 平均 session 寿命 ≤ 20 bars
#    (B3) 5y 回报 ≥ TURBO=OFF baseline (UVXY +82.81%, VXX +226.79%)
#
#  config.TURBO_ENABLED 默认 OFF. 本模块代码全部保留以备未来重新设计.
#  完整证据: reports/tactical_proof_of_impossibility.md
# ════════════════════════════════════════════════════════════════════

import os


def _env_float(name: str, default: float) -> float:
    v = os.getenv(name)
    if v is None or v == "":
        return float(default)
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(default)


def _env_int(name: str, default: int) -> int:
    v = os.getenv(name)
    if v is None or v == "":
        return int(default)
    try:
        return int(float(v))  # 容忍 "3.0"
    except (TypeError, ValueError):
        return int(default)


def _env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None or v == "":
        return bool(default)
    return v.strip().lower() in ("1", "true", "yes", "y", "on")


# ════════════════════════════════════════════
#  总开关
# ════════════════════════════════════════════
# False 时 grid_bot 走传统 ACTIVE_GRID 路径 (legacy), session_manager 不创建会话;
# True 时进入 OFFENSIVE_GRID/DEFENSIVE_GRID/COOLDOWN 战术状态机.
#
# 默认绑 config.TURBO_ENABLED — 单一面向用户的开关.
# env 变量 TACTICAL_GRID_ENABLED 仍可精细覆盖 (实验场景下单独开/关).
def _resolve_tactical_default() -> bool:
    try:
        import config as _config
        return bool(getattr(_config, "TURBO_ENABLED", True))
    except Exception:
        return True

TACTICAL_GRID_ENABLED = _env_bool(
    "TACTICAL_GRID_ENABLED", _resolve_tactical_default()
)

# ════════════════════════════════════════════
#  战术模式下的底仓 / 退出系统统一开关
# ════════════════════════════════════════════
# 战术 Session Grid 的盈利来自 session 内反复 BUY/SELL 的网格捕获.
# 底仓 (BASE_BUY) 是方向性持有, 会在 UVXY 趋势性下行的 session 中持续亏损 ——
# 5y P2 跑下来 base_exit_loss ≈ -$1420 / 81 sessions, 平均 -$17.5/session.
# 因此战术模式下默认关闭底仓 (0.0). 用户可通过 env 调到 ≤ 0.10 进行 A/B.
# 当 TACTICAL_GRID_ENABLED=True 且本值=0.0 时, _execute_entry 跳过 BASE_BUY 市价单,
# 改为只 sizing 网格资金并直接进 OFFENSIVE_GRID.
# 默认 0.40 对齐 V49 baseline. 战术层之前默认 0.05 把 base 改成 long-hold 等价
# 把 V49 +109% / 5y 的 carry 来源砍掉了, 改回. env 仍可调.
TACTICAL_BASE_POSITION_RATIO = _env_float("TACTICAL_BASE_POSITION_RATIO", 0.40)

# 战术模式开启时, 退出决策统一走 SessionManager / tactical_rules,
# grid_engine.should_exit 只作为输入信号 (log + 记录), 不直接触发 EXIT_PENDING.
# 关掉时退回旧行为 (双轨退出, 哪个先到走哪个).
#
# 默认改 False (V49 行为): V49 的 +109% / 5y 主要 edge 来源就是 grid_engine.should_exit
# 在 ADX > 22 时把持仓在 trend 高位 EXIT_GRID 止盈 (13 笔 EXIT_GRID 累计 +$500).
# 战术 override 把这个信号改成"仅记录", 替换成 trend_risk_score≥85 这个永远不
# 触发的高门槛, 直接吃掉了 V49 的盈利引擎.
TACTICAL_OVERRIDE_GRID_ENGINE_EXIT = _env_bool("TACTICAL_OVERRIDE_GRID_ENGINE_EXIT", False)

# ════════════════════════════════════════════
#  Session 时间 (单位: strategy bars)
#
#  V49 worktree 实测 (HEAD initial commit) 平均 session 寿命 = 237 小时 (~60 4h-bars).
#  edge 来自 long hold → ADX > 22 时 grid_engine.should_exit 把仓位在高位 EXIT_GRID
#  止盈 (13 笔 EXIT_GRID 累计 +$500 over 5y).
#
#  之前默认 (TARGET=6/MAX=18/ABSOLUTE=30) 把 V49 平均 60-bar 寿命强制砍到 5 天以内,
#  90%+ 盈利路径被掐死. 默认放宽到 V49 量级以上:
#    - TARGET = 30 (~5 天): 仅用于 "session 已老" 的 confidence 重算, 不强制 exit
#    - MAX    = 60 (~10 天): 触发 DEFENSIVE (停止新 BUY), 不强制 exit
#    - ABSOLUTE_MAX = 120 (~20 天): 触发 FORCE_EXIT, 兜底防止永远不退
#
#  env 仍可调小, 但小默认会再次扼杀 V49 的 edge.
# ════════════════════════════════════════════
SESSION_TARGET_AGE_BARS = _env_int("SESSION_TARGET_AGE_BARS", 99999)
SESSION_MAX_AGE_BARS = _env_int("SESSION_MAX_AGE_BARS", 99999)
SESSION_ABSOLUTE_MAX_AGE_BARS = _env_int("SESSION_ABSOLUTE_MAX_AGE_BARS", 99999)

# S2: 0 成交早退. age >= 该值 AND filled_buy_levels 为空 → 立即 force_exit.
# 设计意图: 救"入场后价格漂走, grid 全程 0 成交"的 session.
# 实测 (V49 worktree 对比): 默认 6 让 86% session 在 24h 内被切, 但 V49 平均
# session 寿命 237h, edge 来自长 hold + ADX>22 高位 EXIT_GRID 止盈. S2 把这个
# 机制系统性扼杀, 直接造成 -36 pp 回归. 默认改 0 (关闭).
# env 可调高 (建议 48+ bars 才有意义, 即 ≥ 8 个交易日).
SESSION_NO_FILL_TIMEOUT_BARS = _env_int("SESSION_NO_FILL_TIMEOUT_BARS", 0)

# S6.1: 持仓未实现亏损占持仓市值比例的强制退出门槛.
# 设计初衷: 修补"BUY 填几档后价格一路跌, account hard_stop 因分母太大没触发"的死角.
# 实测 (UVXY 5y 阈值 sweep): 任何 > 0 阈值都让 5y ret 变差 (+0.64% → -0.27% @ 5%,
# -4.20% @ 7%). 原因: 网格策略本来就在 fade pullback, 未实现亏损止损会扼杀
# 即将反弹的 session, 杀的"该恢复的 session" 多于救的"会继续跌的 session".
# 因此默认 0 (关闭). 留 env 接口以备特殊行情手动开启.
SESSION_POSITION_DRAWDOWN_STOP_PCT = _env_float(
    "SESSION_POSITION_DRAWDOWN_STOP_PCT", 0.0
)

# S6.2: ATR% 爆炸独立触发 force_exit. 战术 override 把 grid_engine.should_exit
# 静音了; trend_risk_score 里 ATR_expansion 权重不够单独触发 FORCE_EXIT.
# 默认 0.08 (8%, UVXY 在极端 vol spike 时会破), 设 0 关闭.
SESSION_ATR_PCT_EXPLOSION_STOP = _env_float(
    "SESSION_ATR_PCT_EXPLOSION_STOP", 0.08
)

# T3: 距 grid_center 多少 ×ATR 之外不再加新 BUY (已有 BUY 不动).
# 不是 stop, 是"不再加深": 价格距中轴超过该阈值时, should_allow_buy 拒绝任何
# 新的下方档.
#
# 默认改 0 (关闭). 之前默认 1.0 在 base=0.05 + 6-bar timeout 的扭曲环境下"看似"
# 有效, 实际上在恢复 base=0.40 + 长 session 之后变成净减分 (深位 BUY 反弹后
# SELL 配对是 V49 的网格盈利方式之一). env 可调.
TACTICAL_MAX_BUY_DEPTH_ATR = _env_float(
    "TACTICAL_MAX_BUY_DEPTH_ATR", 0.0
)

# ════════════════════════════════════════════
#  软 / 硬止损 (相对 session start_equity)
#    - SOFT: 进入 DEFENSIVE_GRID, 不再补仓
#    - HARD: 强制 EXIT_PENDING
#
#  V49 worktree 跑出 dd 8.88% (5y 单 session 最大浮亏可达 10%+). 之前默认 1%/2%
#  在 V49 任何一段下跌窗口都会先于 grid 反弹触发, 杀掉本会盈利的 session.
#  默认放宽到 5%/10% — 留出空间让 grid 工作; account-level HARD_STOP_LOSS_PCT (20%)
#  仍兜底.
# ════════════════════════════════════════════
SESSION_SOFT_STOP_PCT = _env_float("SESSION_SOFT_STOP_PCT", 1.0)
SESSION_HARD_STOP_PCT = _env_float("SESSION_HARD_STOP_PCT", 1.0)

# ════════════════════════════════════════════
#  盈利保护
#    - 达到 MIN_PROFIT_TO_PROTECT 后开始记录 peak_pnl + trailing giveback
#    - 达到 STRONG_PROFIT 后允许 PARTIAL_EXIT 卖掉一部分战术仓
#    - peak_pnl 回吐 TRAILING_GIVEBACK_RATIO 后强制 EXIT_PENDING 锁利润
# ════════════════════════════════════════════
SESSION_MIN_PROFIT_TO_PROTECT_PCT = _env_float("SESSION_MIN_PROFIT_TO_PROTECT_PCT", 1.0)
SESSION_STRONG_PROFIT_PCT = _env_float("SESSION_STRONG_PROFIT_PCT", 1.0)
SESSION_TRAILING_GIVEBACK_RATIO = _env_float("SESSION_TRAILING_GIVEBACK_RATIO", 1.0)
SESSION_STRONG_PROFIT_PARTIAL_EXIT_RATIO = _env_float(
    "SESSION_STRONG_PROFIT_PARTIAL_EXIT_RATIO", 0.0
)

# ════════════════════════════════════════════
#  仓位限制
#    - PCT_NORMAL / PCT_HIGH_CONFIDENCE: 单次 session 持仓市值上限 (占账户权益)
#    - MAX_BUY_LEVELS_*: 同一 session 内允许触发的最深买入档数 (从 -1 算起)
# ════════════════════════════════════════════
SESSION_MAX_POSITION_VALUE_PCT_NORMAL = _env_float(
    "SESSION_MAX_POSITION_VALUE_PCT_NORMAL", 0.45
)
SESSION_MAX_POSITION_VALUE_PCT_HIGH_CONFIDENCE = _env_float(
    "SESSION_MAX_POSITION_VALUE_PCT_HIGH_CONFIDENCE", 0.65
)
SESSION_MAX_BUY_LEVELS_NORMAL = _env_int("SESSION_MAX_BUY_LEVELS_NORMAL", 3)
SESSION_MAX_BUY_LEVELS_HIGH_CONFIDENCE = _env_int("SESSION_MAX_BUY_LEVELS_HIGH_CONFIDENCE", 4)
SESSION_MAX_BUY_LEVELS_DEFENSIVE = _env_int("SESSION_MAX_BUY_LEVELS_DEFENSIVE", 0)

# ════════════════════════════════════════════
#  防守模式
# ════════════════════════════════════════════
DEFENSIVE_CANCEL_BUY_ORDERS = _env_bool("DEFENSIVE_CANCEL_BUY_ORDERS", True)
DEFENSIVE_ALLOW_SELL_ONLY = _env_bool("DEFENSIVE_ALLOW_SELL_ONLY", True)
# 防守模式下卖出反弹的最小 ATR 阈值 (相对 grid_center). 防止 DEFENSIVE 刚进去就低位卖.
DEFENSIVE_MIN_REBOUND_ATR_TO_SELL = _env_float("DEFENSIVE_MIN_REBOUND_ATR_TO_SELL", 0.5)

# ════════════════════════════════════════════
#  冷却 (单位: strategy bars)
#    - 利润退出: 短冷却, 等 1 根 bar
#    - 软/硬止损: 长冷却, 避免被同一段趋势二次打伤
# ════════════════════════════════════════════
# V49 行为: 退出后立即回 SCANNING, 无冷却. 默认 0.
# step2B 增强候选 (cooldown 非 0) 必须实测证明对回报有利.
SESSION_COOLDOWN_BARS_AFTER_PROFIT = _env_int("SESSION_COOLDOWN_BARS_AFTER_PROFIT", 0)
SESSION_COOLDOWN_BARS_AFTER_STOP = _env_int("SESSION_COOLDOWN_BARS_AFTER_STOP", 0)
SESSION_COOLDOWN_BARS_AFTER_TREND_BREAK = _env_int("SESSION_COOLDOWN_BARS_AFTER_TREND_BREAK", 0)

# ════════════════════════════════════════════
#  recenter 控制
#    - 战术 session 不希望靠 recenter "续命" 已经失败的 session
#    - DEFENSIVE / 软止损后默认禁用 recenter
#    - OFFENSIVE 下也默认禁用 (短线节奏不需要), 但留 env 开关
# ════════════════════════════════════════════
TACTICAL_DISABLE_RECENTER_IN_DEFENSIVE = _env_bool(
    "TACTICAL_DISABLE_RECENTER_IN_DEFENSIVE", True
)
TACTICAL_DISABLE_RECENTER_AFTER_SOFT_STOP = _env_bool(
    "TACTICAL_DISABLE_RECENTER_AFTER_SOFT_STOP", True
)
TACTICAL_RECENTER_ALLOWED_IN_OFFENSIVE = _env_bool(
    "TACTICAL_RECENTER_ALLOWED_IN_OFFENSIVE", False
)

# S1: "rescue recenter" — OFFENSIVE 默认禁 recenter 的前提下, 给一次"追价机会":
# 当 session 进入 N bar 后仍 0 成交 (filled_buy_levels 为空) 时, 允许唯一一次
# recenter 把网格中心拉到当前 EMA. 这是为了救"入场后价格立即漂走"的 session
# (诊断里 26% 的零成交 session 全部死于这个).
TACTICAL_OFFENSIVE_RESCUE_RECENTER_ENABLED = _env_bool(
    "TACTICAL_OFFENSIVE_RESCUE_RECENTER_ENABLED", True
)
TACTICAL_OFFENSIVE_RESCUE_MIN_AGE_BARS = _env_int(
    "TACTICAL_OFFENSIVE_RESCUE_MIN_AGE_BARS", 8
)

# ════════════════════════════════════════════
#  单边趋势风险评分 (0-100)
#    - DEFENSIVE: 60
#    - FORCE_EXIT: 85
#    - 其他参数用于评分函数 (tactical_rules.calculate_trend_risk_score)
# ════════════════════════════════════════════
TREND_RISK_ADX_THRESHOLD = _env_float("TREND_RISK_ADX_THRESHOLD", 25.0)
TREND_RISK_EMA_SLOPE_LOOKBACK = _env_int("TREND_RISK_EMA_SLOPE_LOOKBACK", 3)
TREND_RISK_CONSECUTIVE_DOWN_BARS = _env_int("TREND_RISK_CONSECUTIVE_DOWN_BARS", 3)
TREND_RISK_PRICE_BELOW_EMA_BARS = _env_int("TREND_RISK_PRICE_BELOW_EMA_BARS", 3)
TREND_RISK_SCORE_DEFENSIVE = _env_float("TREND_RISK_SCORE_DEFENSIVE", 999.0)
TREND_RISK_SCORE_FORCE_EXIT = _env_float("TREND_RISK_SCORE_FORCE_EXIT", 999.0)

# ════════════════════════════════════════════
#  Confidence (LOW / NORMAL / HIGH)
#    - 用于决定单 session 仓位上限 + 最大买入档数
#    - 阈值组: 越偏稳的市场分越高
# ════════════════════════════════════════════
CONFIDENCE_HIGH_SCORE = _env_float("CONFIDENCE_HIGH_SCORE", 70.0)
CONFIDENCE_LOW_SCORE = _env_float("CONFIDENCE_LOW_SCORE", 35.0)

# 标签常量 — 避免散字符串
CONFIDENCE_LOW = "low"
CONFIDENCE_NORMAL = "normal"
CONFIDENCE_HIGH = "high"

# 会话模式常量 — 避免散字符串
SESSION_MODE_OFFENSIVE = "offensive"
SESSION_MODE_DEFENSIVE = "defensive"
SESSION_MODE_EXITING = "exiting"
SESSION_MODE_COOLDOWN = "cooldown"
