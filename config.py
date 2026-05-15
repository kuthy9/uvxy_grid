"""
config.py — v4 配置 (UVXY V49 全量 tune + 跨 interval 对比)

V49 (2026-04-23, 4h 324-combo 全量 tune 结果):
  full (真实化回测):  +103.78%   MDD  8.88%  Sharpe +0.54  win 83.8%  trips 37
  vs V48:             +101.87%   MDD 10.36%  Sharpe +0.52  win 79.5%  trips 44
  vs V47 baseline:     +84.61%   MDD 20.65%  Sharpe +0.37  win 79.5%  trips 39

  4h 全量网格 324 组 + top-8 walk-forward (6 窗口) + OOS 三分法 + ±20% 稳定性.
  V49 在 walk-forward 上的平均 valid Sharpe = 1.03, 略优于 V48 (1.00).

  改动点 (vs V48):
    - GRID_SPACING_ATR_MULTIPLIER 0.40 → 0.50  (全量网格 + walk-forward 共同偏好 0.5)
    - (EXIT_MAX_ADX 保持 22.0)

跨 interval 对比 (所有都做了完整 tune, 见 runtime/experiments/):
  interval  chosen       5y return   Sharpe    DD   trips   WF_Sharpe  结论
  4h        V49          +103.78%    +0.54    8.88%   37    +1.03      ✓ 综合最优
  1d        looser       +500.39%    +0.46   16.48%    2    +0.25      小样本
  1h        (所有负)      -20.32%    -0.45   55.82%   91    -1.38      ✗
  15m       (所有负)      -21.83%    -1.03   28.18%   19    -1.17      ✗

默认使用 4h. 若想切换, 只改 `STRATEGY_INTERVAL = "1h"/"15m"/"1d"` 即可, 系统动态随动.
V47/V48 注释保留 (QQQ-tuned 参考) 便于回滚或换标的.
"""
import os

# ════════════════════════════════════════════
#  IBKR 连接 — 端口白名单 (唯一权威来源)
#
#  标准端口:
#    TWS 桌面版             Paper = 7497    Live = 7496
#    IB Gateway 原生        Paper = 4002    Live = 4001
#    gnzsnz/ib-gateway      Paper = 4004    Live = 4003
#      (docker 镜像通过 socat 把内部 4001/4002 转发到 4003/4004,
#       docker-compose 内容器间互连通常走 4003/4004)
#  main.py 的 Paper/Live 标签、连接前校验都走 KNOWN_IBKR_PORTS.
#
#  当前部署 (见 docker-compose.yml): IBKR_HOST=ib-gateway, IBKR_PORT=4004 (gnzsnz paper).
# ════════════════════════════════════════════
IBKR_HOST = os.getenv("IBKR_HOST", "127.0.0.1")
IBKR_PORT = int(os.getenv("IBKR_PORT", "4004"))
IBKR_CLIENT_ID = int(os.getenv("IBKR_CLIENT_ID", "1"))

# reqMarketDataType: 1=live, 2=frozen, 3=delayed(15min), 4=delayed-frozen.
# 默认 1 (实时); 无实时订阅账户会在 get_current_price 自动降级到 3 并打 WARN 日志.
MARKET_DATA_TYPE = int(os.getenv("MARKET_DATA_TYPE", "1"))

# 启动阶段 reqMktData 等待窗口 (秒). 原 3s 过短, 冷启动常返 None → 闪崩保护失效.
STARTUP_PRICE_TIMEOUT_SEC = float(os.getenv("STARTUP_PRICE_TIMEOUT_SEC", "15"))

# 实盘 get_current_price 单次轮询超时 (秒) 与重试次数.
# 单次轮询用 PRICE_TIMEOUT_SEC; 出现 Socket disconnect / 异常 / 超时无报价时,
# IBKRExecutor 会在外层 reconnect 后再 retry, 最多 PRICE_RETRY_COUNT 次.
PRICE_TIMEOUT_SEC = float(os.getenv("PRICE_TIMEOUT_SEC", "15"))
PRICE_RETRY_COUNT = int(os.getenv("PRICE_RETRY_COUNT", "2"))

# reqHistoricalData 取前收盘时的回溯窗口 (自然日). 5D 足以覆盖周末 + 一个节假日.
IBKR_HIST_PREV_CLOSE_DAYS = int(os.getenv("IBKR_HIST_PREV_CLOSE_DAYS", "5"))

# (port) -> ("paper" | "live", 描述)
KNOWN_IBKR_PORTS: dict[int, tuple[str, str]] = {
    7497: ("paper", "TWS Paper"),
    7496: ("live",  "TWS Live"),
    4002: ("paper", "IB Gateway Paper"),
    4001: ("live",  "IB Gateway Live"),
    4004: ("paper", "gnzsnz/ib-gateway Paper (socat tunnel)"),
    4003: ("live",  "gnzsnz/ib-gateway Live (socat tunnel)"),
}


def ibkr_port_label(port: int) -> tuple[str, str]:
    """返回 (mode, label). 未知端口返回 ('unknown', ...)."""
    if port in KNOWN_IBKR_PORTS:
        mode, label = KNOWN_IBKR_PORTS[port]
        return mode, label
    return "unknown", f"Unknown port {port}"

# ════════════════════════════════════════════
#  引擎模式开关 ("涡轮增压" — 战术 Session 网格)
#
#  TURBO_ENABLED = True  → 启用激进战术 Session 网格. 包含:
#                          - 6 状态 FSM (SCANNING/WAITING/OFFENSIVE/DEFENSIVE/EXIT/COOLDOWN)
#                          - SessionManager (战役级风控: SOFT/HARD/AGE/PROFIT_PROTECT)
#                          - tactical_rules (信号过滤 / DEFENSIVE 切换 / FORCE_EXIT)
#                          - 入场 ADX 斜率 + recent-range 过滤
#                          - 距中轴 max BUY depth
#                          - rescue recenter / no_fill_timeout
#  TURBO_ENABLED = False → 退化为原始 3-state 网格 (SCANNING/ACTIVE_GRID/EXIT_PENDING):
#                          - 全部战术 gate 走 legacy 分支 (grid_bot 8 处 if 都退化)
#                          - 底仓用 BASE_POSITION_RATIO (默认 0.40, 不是 TACTICAL 的 0.05)
#                          - grid_engine.should_exit 直接触发 EXIT_PENDING (无 override)
#                          - 入场只看 ADX/ATR/BB 三个条件 (无斜率 / range / depth 过滤)
#
#  这是用户面对的"单一开关". tactical_config.TACTICAL_GRID_ENABLED 默认绑这个值,
#  但保留 env 覆盖 (TACTICAL_GRID_ENABLED=0 仍能精细关闭仅战术层).
# ════════════════════════════════════════════
import os as _os
# 默认 OFF — 2026-05-15 严证伪通过, 详见 reports/tactical_proof_of_impossibility.md
# 战术化代码全部保留 (EXPERIMENTAL); 实盘 / 回测默认走 V49 legacy 路径.
TURBO_ENABLED: bool = _os.getenv("TURBO_ENABLED", "0").strip().lower() in (
    "1", "true", "yes", "y", "on"
)


# ════════════════════════════════════════════
#  标的
# ════════════════════════════════════════════
SYMBOL = "UVXY"           # QQQ-tuned 默认: "QQQ"
EXCHANGE = "SMART"
CURRENCY = "USD"

# ════════════════════════════════════════════
#  资金
#
#  TOTAL_CAPITAL 由运行时动态注入, 绝不在 config 中硬编码:
#    - 实盘 (main.py): 从 IBKR NetLiquidation 拉取 × (1 - CAPITAL_RESERVE_RATIO).
#                      读取失败 → sys.exit(3), 拒绝带任意资金假设启动.
#    - 回测 (backtest.py): 通过 --capital 参数显式注入 (默认 BACKTEST_DEFAULT_CAPITAL).
#    - 测试 (test.py): conftest/setUp 注入 (固定 2000 用于对比阈值).
#  业务代码读 config.TOTAL_CAPITAL 时, 必须在注入之后.
# ════════════════════════════════════════════
from typing import Optional as _Optional
TOTAL_CAPITAL: _Optional[float] = None   # runtime 注入
BACKTEST_DEFAULT_CAPITAL = 10000.0       # 仅作为 --capital argparse default

BASE_POSITION_RATIO = 0.40       # 底仓占比 (V47 = QQQ 默认值)
GRID_CAPITAL_RATIO = 0.50        # 网格资金占比 (V47 = QQQ 默认值)


def require_total_capital() -> float:
    """业务代码需读真实数值时用此 helper, 未初始化会明确报错而非 NoneType."""
    if TOTAL_CAPITAL is None:
        raise RuntimeError(
            "config.TOTAL_CAPITAL 未初始化 — "
            "实盘应从 IBKR NetLiquidation 拉取; 回测从 --capital 注入; 测试 setUp 注入."
        )
    return float(TOTAL_CAPITAL)

# ════════════════════════════════════════════
#  策略周期 (唯一入口 — 改这里整个系统随动)
#  支持: "15m" | "1h" | "4h" | "1d"
# ════════════════════════════════════════════
STRATEGY_INTERVAL = "4h"

# 周期字符串 → 分钟数 (权威映射)
_INTERVAL_MINUTES: dict[str, int] = {
    "15m": 15,
    "1h":  60,
    "4h":  240,
    "1d":  24 * 60,
}


def strategy_interval_minutes() -> int:
    iv = STRATEGY_INTERVAL.lower()
    if iv not in _INTERVAL_MINUTES:
        raise ValueError(
            f"不支持的策略周期 {STRATEGY_INTERVAL!r}, 必须是 {list(_INTERVAL_MINUTES)}"
        )
    return _INTERVAL_MINUTES[iv]


def strategy_interval_hours() -> float:
    """策略周期小时数 (可为小数: 15m → 0.25). bar-based 阈值计算用."""
    return strategy_interval_minutes() / 60.0


def strategy_interval_seconds() -> int:
    """策略周期秒数 — SCANNING / WAITING_ENTRY 主循环 sleep 基于此"""
    return strategy_interval_minutes() * 60


def round_quantity(q: float) -> float:
    """
    按 USE_FRACTIONAL 规整下单数量 — 所有下单点必须走这里, 保证一致性。
      - True: 保留 4 位小数 (IBKR 支持碎股)
      - False: 向下取整到整数股 (避免 IBKR 拒单)
    """
    if q is None:
        return 0.0
    if not USE_FRACTIONAL:
        return float(int(q))
    return round(float(q), 4)


# ════════════════════════════════════════════
#  入场筛选 (V47: 严过滤, 只在 UVXY 中等波动+无趋势时入场)
# ════════════════════════════════════════════
ENTRY_MAX_ADX = 20.0                   # QQQ-tuned: 40.0
ENTRY_MIN_ADX = 5.0
ENTRY_MIN_ATR_PCT = 0.020              # QQQ-tuned: 0.005
ENTRY_MAX_ATR_PCT = 0.045              # QQQ-tuned: 0.025  (~UVXY p50)
ENTRY_MAX_EMA_DEVIATION_ATR = 1.0      # QQQ-tuned: 1.5
ENTRY_MAX_BB_WIDTH_PCT = 0.20          # QQQ-tuned: 0.10
# S3: 入场加"实际震荡幅度"过滤 — 过去 N 根 bar 的 (max High - min Low) / ATR.
# 设 0 关闭. 之前默认 2.0×ATR 实测在 base=0.40 长 session 框架下并无显著贡献,
# 反而拒掉了一些 V49 能成功的 entry. 默认改 0, 留作 step2C 增强候选验证.
ENTRY_RECENT_RANGE_LOOKBACK_BARS = int(
    os.getenv("ENTRY_RECENT_RANGE_LOOKBACK_BARS", "20")
)
ENTRY_MIN_RECENT_RANGE_ATR = float(
    os.getenv("ENTRY_MIN_RECENT_RANGE_ATR", "0.0")
)
# T1: ADX 斜率上行 → 拒绝入场. 防止入场在 "ranging 即将 break-out" 的临界点.
# ENTRY_MAX_ADX_SLOPE: 最大允许的 ADX 单 bar 斜率 (正=ADX 上升).
#   < 0 拒绝任何上升 (严格)
#   = 0 拒绝明显上升
#   > 0 允许小幅上升
#   <= -100 关闭该 check
# 默认改 -1000 (关闭) — 同 S3 理由, 待 step2C 验证.
ENTRY_ADX_SLOPE_LOOKBACK_BARS = int(
    os.getenv("ENTRY_ADX_SLOPE_LOOKBACK_BARS", "3")
)
ENTRY_MAX_ADX_SLOPE = float(
    # 默认 -0.5 (sweep 最优): 拒绝 ADX 斜率 ≥ -0.5/bar 的入场.
    # 5y 实测: 关闭 +44.08%, =0 +47.30%, =-0.3 +52.36%, =-0.5 +53.83%,
    #          =-0.7 +49.50%, =-1.0 +0.28% (过严, 没 session 触发).
    # 入场更挑剔 → 6 个 session 减少, 但 DD 从 16.33% 降到 13.69%, win 69.44%.
    # 这是当前唯一被实测验证的"战术增强". 关闭设 -1000.
    os.getenv("ENTRY_MAX_ADX_SLOPE", "-0.5")
)
ENTRY_MIN_DAILY_VOLUME_USD = 1.0e6     # 日均成交额下限 (USD)
ENTRY_BLACKOUT_DAYS_BEFORE_EARNINGS = 7
ENTRY_BLACKOUT_DAYS_AFTER_EARNINGS = 1
ENTRY_PRICE_BAND_ATR = float(os.getenv("ENTRY_PRICE_BAND_ATR", "1.25"))
# WAITING_ENTRY 等价格回到 EMA±1×ATR 入场带的最大 bar 数. 4h 周期下 1 bar=4h.
# 设计取向: 进入 WAITING_ENTRY 后最多再给 1 根 bar 看 timing 是否进 band;
# 没进就立刻回 SCANNING 重新评估, 不在旧 entry 上下文里挂久. 实践效果:
#   - T0 进 WAITING_ENTRY → T0+4h 评估一次 (此时 elapsed≈1.0, 不触发超时, 看 timing)
#   - T0+8h 再评估时 elapsed=2.0 > 1.0 → check_entry_timeout 触发回 SCANNING
# 即"最多挂一个 4h bar"窗口, 短到与 SCANNING 几乎等价但保留一次额外 timing 机会.
# 实盘观察后若发现 timing 命中比例过低可调大. 可通过环境变量覆盖.
ENTRY_MAX_WAIT_BARS = float(os.getenv("ENTRY_MAX_WAIT_BARS", "12"))  # 2026-05-15: V49 default 12 恢复, walk-forward 验证过

# 浮点边界误差吸收带 (单位: bar). check_entry_timeout 对比 elapsed_bars 时使用,
# 防止主循环调度漂移让 elapsed 略大于 ENTRY_MAX_WAIT_BARS 而提早超时,
# 在 timing 还没机会被重新评估前就把 WAITING_ENTRY 消耗掉.
ENTRY_TIMEOUT_EPSILON_BARS = float(os.getenv("ENTRY_TIMEOUT_EPSILON_BARS", "1e-6"))

# WAITING_ENTRY 下 _execute_entry 返回 False 的连续次数阈值;
# 触发后自动回 SCANNING, 防止取价/下单/成交异常把状态长期卡在 waiting_entry,
# 不需要人工改 SQLite. 默认 2: 容忍单次瞬时抖动 (如 IBKR 心跳掉线), 连续两次才退场.
ENTRY_EXECUTION_MAX_FAILURES = int(os.getenv("ENTRY_EXECUTION_MAX_FAILURES", "2"))

# ════════════════════════════════════════════
#  网格 (V47: 跟得上 UVXY 高频换向)
# ════════════════════════════════════════════
GRID_LEVELS = 6
GRID_SPACING_ATR_MULTIPLIER = float(   # V49 walk-forward 最优 0.5
    os.getenv("GRID_SPACING_ATR_MULTIPLIER", "0.5")
)
# 注: T2 实验 0.3 实测让 ret 变差 (gpnl 由 +$20 → -$412): spacing 收窄
# 同样让 BUY 间距收窄, 浅回调就触发 BUY, 反弹不够触发 SELL, 反而扩大累积损失.
# 真正的 SELL 触发率改善要靠 T3 (距中轴 1.0×ATR 不再加 BUY), 不靠改 spacing.
GRID_MIN_SPACING_PCT = 0.012           # QQQ-tuned: 0.008
GRID_MAX_SPACING_PCT = 0.06            # QQQ-tuned: 0.04
GRID_CENTER_EMA_PERIOD = 20
GRID_RECENTER_THRESHOLD_ATR = 1.0      # QQQ-tuned: 1.5  (更敏感, 更频繁重置)
GRID_RECENTER_MIN_BARS = 4

# ════════════════════════════════════════════
#  退出条件 (V47: 略宽于入场, 给现持仓喘息)
# ════════════════════════════════════════════
EXIT_MAX_ADX = 22.0                    # V47: 25.0 | QQQ-tuned: 40.0 (稳定性扫描: 更早切 ADX 退出显著降低 DD)
EXIT_MAX_ATR_PCT = 0.07                # QQQ-tuned: 0.04
EXIT_PRICE_DEVIATION_ATR = 4.0         # QQQ-tuned: 6.0

# ════════════════════════════════════════════
#  订单
# ════════════════════════════════════════════
ORDER_TIF = "GTC"
USE_FRACTIONAL = False
MIN_ORDER_VALUE = 50.0

# ════════════════════════════════════════════
#  手续费 + 交易成本模型 (IBKR Tiered US 散户)
#  回测/实盘共用口径; 回测按此模拟真实扣费.
# ════════════════════════════════════════════
IBKR_COMMISSION_MIN = 0.35            # 每单最低 (USD)
IBKR_COMMISSION_PER_SHARE = 0.0035    # 每股费率 (USD), Tiered 散户起步档

# 监管/清算费 (卖出主要承担, 2025 SEC/FINRA 公布费率)
SEC_FEE_RATE = 27.8e-6                # 卖出名义金额 × 此费率 (SEC Section 31)
TAF_FEE_PER_SHARE = 0.000166          # FINRA TAF, 卖出按股收取
TAF_FEE_MIN = 0.01                    # TAF 最低 (每笔)
TAF_FEE_MAX = 8.30                    # TAF 每笔最高

# ════════════════════════════════════════════
#  回测真实化 (slippage / fill probability)
#  这些参数只影响 SimulatedExecutor, 实盘走 IBKR 真实撮合, 不受影响.
# ════════════════════════════════════════════
BT_REALISTIC_FILLS = True             # 关掉后退化成旧的理想化撮合, 方便前后对比
BT_MARKET_SLIP_BPS = 5.0              # 市价单滑点 (bps, 即 0.01% 单位). UVXY ETF spread 中位数 ~10bps → 取 half-spread
BT_LIMIT_SLIP_BPS = 1.0               # 限价单触价成交时的微小滑点 (bps). 同方向价改善 0 → 按 limit 成交, 偶有半分劣化
BT_LIMIT_FILL_PROB_TOUCH = 0.60       # 价格仅"触及"limit (seg_end == limit) 时的成交概率
BT_LIMIT_FILL_PROB_CROSS = 1.0        # 价格明显"穿越"limit 时保持全额成交
BT_GAP_FILL_PROB = 0.95               # 开盘跳空穿越 limit 的成交概率 (gap 处通常能成, 稍低是模拟排队)
BT_RANDOM_SEED = 20260423             # 回测随机种子, 保证 fill 概率可复现

# ════════════════════════════════════════════
#  统计口径
# ════════════════════════════════════════════
RISK_FREE_RATE_ANNUAL = 0.04          # 年化无风险利率, 用于 Sharpe 分子
TRADING_DAYS_PER_YEAR = 252           # 美股标准
TRADING_HOURS_PER_DAY = 6.5           # 9:30-16:00 ET

# ════════════════════════════════════════════
#  风控 (V47: 放宽硬止损, 容忍 UVXY 高波动)
# ════════════════════════════════════════════
HARD_STOP_LOSS_PCT = 0.20              # QQQ-tuned: 0.15
# 单日已实现亏损上限 (按 TOTAL_CAPITAL 比例). 5% 与 V49 baseline ($2000 → $100) 数值一致,
# 但跨资金规模都保持同一语义, 不再随 NetLiquidation 变化漂移.
MAX_DAILY_LOSS_PCT = 0.05
MAX_POSITION_VALUE_PCT = 0.95

# ════════════════════════════════════════════
#  交易时段 (ET)
# ════════════════════════════════════════════
TRADING_START_HOUR = 10
TRADING_START_MINUTE = 30
TRADING_END_HOUR = 15
TRADING_END_MINUTE = 30
FRIDAY_CUTOFF_HOUR = 15
FRIDAY_CUTOFF_MINUTE = 0

# ════════════════════════════════════════════
#  财报 (UVXY 是 ETF, 默认空)
# ════════════════════════════════════════════
EARNINGS_FREEZE_DAYS_BEFORE = 3
EARNINGS_FREEZE_DAYS_AFTER = 1
EARNINGS_DATES = []

# ════════════════════════════════════════════
#  主循环频率
#    - SCANNING:      跟随 STRATEGY_INTERVAL (4h 周期下每 4h 扫描一次)
#    - WAITING_ENTRY: 与 STRATEGY_INTERVAL 解耦, 默认 300s (5min) — 这样进入
#                     WAITING_ENTRY 后能在 5 分钟级别反复评估 timing,
#                     而不是干等 4h. 本身不会延长 ENTRY_MAX_WAIT_BARS 的总等待
#                     时间 (那个仍按 strategy bar 计), 只决定 "评估频率".
#    - ACTIVE_GRID / EXIT_PENDING: 与策略周期无关, 用 ACTIVE_CHECK_INTERVAL_SEC.
# ════════════════════════════════════════════
ACTIVE_CHECK_INTERVAL_SEC = 60    # ACTIVE_GRID / EXIT_PENDING 下订单检查节拍, 与策略周期无关

# WAITING_ENTRY 评估节拍 (秒). 默认 300s = 5min. 可通过环境变量覆盖以便实盘调节,
# 不需要每次改代码. 设得太大 → 错过入场 band 内的窗口; 设得太小 → IBKR 取价频繁
# 但单次取价代价低, 5min 是经验上够用的值.
WAITING_ENTRY_CHECK_INTERVAL_SEC = int(os.getenv("WAITING_ENTRY_CHECK_INTERVAL_SEC", "300"))


def scanning_interval_sec() -> int:
    return strategy_interval_seconds()


def waiting_interval_sec() -> int:
    """WAITING_ENTRY 主循环 sleep 周期.

    刻意与 strategy_interval_seconds() 解耦 — 历史 bug: 两者绑定在一起后,
    STRATEGY_INTERVAL=4h 会让 WAITING_ENTRY 也每 4h 才检查一次, 入场信号
    出现后系统先睡 4h 才尝试 timing, 等再醒来 timing 早已不在 band 内.
    现在固定走 WAITING_ENTRY_CHECK_INTERVAL_SEC, 与策略周期独立.
    """
    return WAITING_ENTRY_CHECK_INTERVAL_SEC

# ════════════════════════════════════════════
#  数据
# ════════════════════════════════════════════
DATA_CACHE_DIR = os.getenv("DATA_CACHE_DIR", "./runtime/data_cache")
HISTORY_LOOKBACK_DAYS = 180

# ════════════════════════════════════════════
#  日志 & 报告
# ════════════════════════════════════════════
LOG_FILE = os.getenv("LOG_FILE", "./runtime/grid_trader.log")
DB_FILE = os.getenv("DB_FILE", "./runtime/trades.db")
REPORT_DIR = os.getenv("REPORT_DIR", "./runtime/reports")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# 周报: 每周一 16:30 ET 生成 (原来每日)
WEEKLY_REPORT_HOUR = 16
WEEKLY_REPORT_MINUTE = 30

# 兼容旧代码
DAILY_REPORT_HOUR = WEEKLY_REPORT_HOUR
DAILY_REPORT_MINUTE = WEEKLY_REPORT_MINUTE
