"""
scripts/tune_tactical.py — 战术 Session Grid 参数调优

设计原则:
  - 分层调参: P0 (止损/年龄) → P1 (盈利保护/仓位) → P2 (V49 基础)
  - P3 / P4 锁定: 系统级 + 频率/调度参数不参与搜索, 仅展示
  - 多指标评分: 不只看 total return, 还看 drawdown / profit_factor /
    grid_close_win_rate / forced_exit_ratio / session_count
  - 不会自动写回 config.py — 输出 CSV/MD, 人工 review 后才生效
  - 默认禁止跑 all profile — 强制用户显式选择 layer

CLI:
  python scripts/tune_tactical.py --profile p0 --years 1            # smoke (≤1y)
  python scripts/tune_tactical.py --profile p0 --years 5 --top 5    # 5y 完整
  python scripts/tune_tactical.py --profile p1 --years 5 --workers 4
  python scripts/tune_tactical.py --profile p2 --years 5
  python scripts/tune_tactical.py --profile all --years 5 --top 5 --explicit-all

输出:
  reports/tuning/<symbol>_<interval>_<profile>_<timestamp>/
      grid_search.csv     全量参数 × 指标
      top.md              top-N 候选 + 评分解释
      locked_params.md    P3/P4 锁定参数清单 (回测当前快照)
"""
from __future__ import annotations

import argparse
import csv
import itertools
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from multiprocessing import get_context
from pathlib import Path
from typing import Optional

# 允许从仓库根运行
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd

import config
import tactical_config as tcfg
from backtest import BacktestRunner, load_market_data


# ════════════════════════════════════════════
#  P0 — "解决过早退出 / session 无法闭环" (战术止损 + 年龄 + 趋势)
# ════════════════════════════════════════════
GRID_P0 = {
    "SESSION_SOFT_STOP_PCT":            [0.015, 0.020, 0.025],
    "SESSION_HARD_STOP_PCT":            [0.030, 0.040, 0.050],
    "SESSION_MAX_AGE_BARS":             [24, 30, 40],
    "SESSION_ABSOLUTE_MAX_AGE_BARS":    [40, 60],
    "TREND_RISK_SCORE_DEFENSIVE":       [65, 70, 75],
    "SESSION_TRAILING_GIVEBACK_RATIO":  [0.50, 0.60],
}

# ════════════════════════════════════════════
#  P1 — 在 P0 top 附近, 优化盈利保护 + 仓位效率 (NARROW: ~72 combos)
# ════════════════════════════════════════════
GRID_P1 = {
    # 盈利保护 (最影响策略性格)
    "SESSION_MIN_PROFIT_TO_PROTECT_PCT":         [0.005, 0.010],
    "SESSION_STRONG_PROFIT_PCT":                 [0.012, 0.018, 0.025],
    "SESSION_STRONG_PROFIT_PARTIAL_EXIT_RATIO":  [0.25, 0.35, 0.50],
    # 仓位
    "SESSION_MAX_BUY_LEVELS_NORMAL":             [3, 4],
    "SESSION_MAX_POSITION_VALUE_PCT_NORMAL":     [0.45, 0.55],
    # Defensive 反弹卖出 ATR 阈值 (影响 DEFENSIVE 退出节奏)
    "DEFENSIVE_MIN_REBOUND_ATR_TO_SELL":         [0.3, 0.5],
}  # 2*3*3*2*2*2 = 72 combos

# ════════════════════════════════════════════
#  P2 — 轻量检查 V49 / 入场 / 网格 (LIGHT: ~32 combos)
# ════════════════════════════════════════════
GRID_P2 = {
    "ENTRY_MAX_ADX":                  [20, 25],
    "ENTRY_MAX_EMA_DEVIATION_ATR":    [1.0, 1.5],
    "GRID_SPACING_ATR_MULTIPLIER":    [0.50, 0.60],
    "EXIT_MAX_ADX":                   [22, 28],
    "EXIT_PRICE_DEVIATION_ATR":       [4.0, 5.0],
}  # 2*2*2*2*2 = 32 combos

# ════════════════════════════════════════════
#  P3 (LOCKED — 系统级 / 真实费用, 不参与搜索)
# ════════════════════════════════════════════
LOCKED_P3 = [
    "HARD_STOP_LOSS_PCT", "MAX_DAILY_LOSS_PCT", "MAX_POSITION_VALUE_PCT",
    "IBKR_COMMISSION_MIN", "IBKR_COMMISSION_PER_SHARE",
    "SEC_FEE_RATE", "TAF_FEE_PER_SHARE", "TAF_FEE_MIN", "TAF_FEE_MAX",
    "BT_MARKET_SLIP_BPS", "BT_LIMIT_SLIP_BPS",
    "BT_LIMIT_FILL_PROB_TOUCH", "BT_LIMIT_FILL_PROB_CROSS", "BT_GAP_FILL_PROB",
    "TRADING_START_HOUR", "TRADING_START_MINUTE",
    "TRADING_END_HOUR", "TRADING_END_MINUTE",
    "FRIDAY_CUTOFF_HOUR", "FRIDAY_CUTOFF_MINUTE",
    "STARTUP_PRICE_TIMEOUT_SEC", "PRICE_TIMEOUT_SEC", "PRICE_RETRY_COUNT",
    "IBKR_HIST_PREV_CLOSE_DAYS",
]

# ════════════════════════════════════════════
#  P4 (LOCKED — 频率 / 调度 / API 压力, 不参与收益优化)
# ════════════════════════════════════════════
LOCKED_P4 = [
    "ACTIVE_CHECK_INTERVAL_SEC", "WAITING_ENTRY_CHECK_INTERVAL_SEC",
    "ENTRY_MAX_WAIT_BARS", "ENTRY_TIMEOUT_EPSILON_BARS",
    "ENTRY_EXECUTION_MAX_FAILURES",
]


CAPITAL = 10000.0


# ════════════════════════════════════════════
#  Exit reason 分类: 把人类可读的中文 reason 解析成机器分类
#
#  各 reason 写入位置 (用于关键字推断):
#    硬止损 / hard_stop    → rules.should_force_exit "硬止损 session_pnl=..."
#    软止损 / soft_stop    → rules.should_enter_defensive "软止损 session_pnl=..."
#    trend_risk           → rules.* "趋势风险评分 X >= Y"
#    adx_high             → grid_engine.should_exit "ADX=X > Y (趋势确立)"
#    price_deviation      → grid_engine.should_exit "价格$X偏离中轴 N×ATR"
#    atr_explosion        → grid_engine.should_exit "ATR%=X% > Y% (波动爆炸)"
#    age / timeout        → rules.should_force_exit "age=Xbars > SESSION_ABSOLUTE_MAX_AGE_BARS"
#    profit_protect       → rules.should_protect_profit "盈利保护退出: peak=$X 回吐 Y%"
#    partial_profit       → rules.should_protect_profit "强盈利部分减仓 pnl_pct=X%"
# ════════════════════════════════════════════

def classify_exit_reason(text: str) -> str:
    r = (text or "").lower()
    if not r:
        return "unknown"
    # 顺序重要: 优先精确关键字
    # S2: no_fill_timeout 需要排在 age 之前 — 它的文本也含 "age=N"
    if "no_fill_timeout" in r or "0 成交" in text:
        return "no_fill_timeout"
    if "硬止损" in text or "hard_stop" in r:
        return "hard_stop"
    if "盈利保护" in text or "giveback" in r or "trailing" in r:
        return "profit_protect"
    if "强盈利" in text or "partial_profit" in r:
        return "partial_profit"
    if "趋势风险" in text or "trend_risk" in r:
        return "trend_risk"
    if "波动爆炸" in text or "atr%" in r:
        return "atr_explosion"
    if "偏离中轴" in text or "deviation" in r:
        return "price_deviation"
    if ("adx=" in r or "adx >" in r) and "趋势确立" in text:
        return "adx_high"
    if "age=" in r or "absolute_max" in r or "超时" in text or "max_age_bars" in r:
        return "age"
    if "软止损" in text or "soft_stop" in r:
        return "soft_stop"
    if "exit_complete" in r or "force_exit" in r:
        return "force_exit_generic"
    return "other"


def compute_per_session_diagnostics(db_path: str) -> dict:
    """从 grid_sessions + trades 表聚合每 session 维度的诊断指标.

    返回 dict (字段全部从真实 DB 聚合, 不伪造):
      - exit_reason_breakdown: dict[reason → count]
      - exit_reason_top: list[(reason, count, pct)]  按数量排序
      - buy_fills_per_session: avg per closed session
      - sell_fills_per_session: avg per closed session
      - grid_close_count: trades 表中 GRID_SELL 数量 (作为网格平仓数)
      - grid_pnl_total: GRID_SELL + EXIT_GRID + EMERGENCY_GRID 的 pnl 之和
      - base_exit_loss: (sum sell_value - sum buy_value) for BASE_BUY + EXIT_BASE
      - avg_realized_pnl_session: realized_pnl 列 / count
      - avg_unrealized_pnl_session: unrealized_pnl 列 / count
      - sessions_with_grid_sell: 有 GRID_SELL 成交的 session 数
    """
    out = {
        "exit_reason_breakdown": {},
        "exit_reason_top": [],
        "buy_fills_per_session": 0.0,
        "sell_fills_per_session": 0.0,
        "grid_close_count": 0,
        "grid_pnl_total": 0.0,
        "base_exit_loss": 0.0,
        "avg_realized_pnl_session": 0.0,
        "avg_unrealized_pnl_session": 0.0,
        "sessions_with_grid_sell": 0,
    }
    try:
        with sqlite3.connect(db_path) as conn:
            sessions = conn.execute(
                """SELECT session_id, exit_reason, total_pnl, realized_pnl,
                          unrealized_pnl FROM grid_sessions WHERE status='closed'"""
            ).fetchall()
            trades = conn.execute(
                """SELECT session_id, action, order_type, quantity, price, pnl
                   FROM trades"""
            ).fetchall()
    except sqlite3.OperationalError:
        return out

    if not sessions:
        return out

    n = len(sessions)

    # exit_reason 分类
    reason_counts: dict[str, int] = {}
    realized_sum = 0.0
    unrealized_sum = 0.0
    for sid, exit_reason, total_pnl, realized, unrealized in sessions:
        cat = classify_exit_reason(exit_reason or "")
        reason_counts[cat] = reason_counts.get(cat, 0) + 1
        realized_sum += float(realized or 0.0)
        unrealized_sum += float(unrealized or 0.0)
    out["exit_reason_breakdown"] = reason_counts
    out["exit_reason_top"] = sorted(
        [(k, v, v / n * 100.0) for k, v in reason_counts.items()],
        key=lambda x: -x[1],
    )
    out["avg_realized_pnl_session"] = realized_sum / n
    out["avg_unrealized_pnl_session"] = unrealized_sum / n

    # trades 维度聚合
    session_ids = {s[0] for s in sessions}
    buy_per_sid: dict[str, int] = {}
    sell_per_sid: dict[str, int] = {}
    grid_sell_count = 0
    grid_pnl = 0.0
    base_buy_value = 0.0
    base_exit_value = 0.0
    sessions_with_grid_sell: set = set()

    for sid, action, order_type, qty, price, pnl in trades:
        ot = (order_type or "").upper()
        # 只统计能归到 session 的 trades (BASE_BUY 入场前 session 未起步, 排除)
        if sid in session_ids:
            if action == "BUY":
                buy_per_sid[sid] = buy_per_sid.get(sid, 0) + 1
            elif action == "SELL":
                sell_per_sid[sid] = sell_per_sid.get(sid, 0) + 1
                if "GRID" in ot and "EXIT" not in ot and "EMERGENCY" not in ot:
                    sessions_with_grid_sell.add(sid)
        # 网格平仓 (GRID_SELL = FIFO 配对) + EXIT/EMERGENCY 路径的 GRID
        if ot.startswith("GRID_SELL"):
            grid_sell_count += 1
            grid_pnl += float(pnl or 0.0)
        elif ot in ("EXIT_GRID", "EMERGENCY_GRID"):
            grid_pnl += float(pnl or 0.0)
        # 底仓相关: BASE_BUY 是建仓, EXIT_BASE / EMERGENCY_BASE 是清仓
        if ot == "BASE_BUY":
            base_buy_value += float(qty or 0.0) * float(price or 0.0)
        elif ot in ("EXIT_BASE", "EMERGENCY_BASE"):
            base_exit_value += float(qty or 0.0) * float(price or 0.0)

    out["buy_fills_per_session"] = sum(buy_per_sid.values()) / n
    out["sell_fills_per_session"] = sum(sell_per_sid.values()) / n
    out["grid_close_count"] = grid_sell_count
    out["grid_pnl_total"] = grid_pnl
    # base_exit_loss = 卖回时的现金 - 买入时的现金成本 (正=底仓盈利, 负=亏损)
    # 注意: 这是名义金额差, 不扣佣金 (那部分小, 不影响主因分析)
    out["base_exit_loss"] = base_exit_value - base_buy_value
    out["sessions_with_grid_sell"] = len(sessions_with_grid_sell)

    return out


# ════════════════════════════════════════════
#  评分: 多指标加权
# ════════════════════════════════════════════

def score_row(row: dict) -> float:
    """综合评分.

    用户目标:
      1. 不要靠极少数交易撑起结果 → session_count >= 20 才有正分
      2. 不要单边大亏 → drawdown 罚分
      3. 盈亏比 > 胜率 → 看 profit_factor 而非纯 win_rate
      4. forced_exit_ratio 过高 → 罚分 (说明参数让 session 频繁被强制退出)

    评分组成 (-∞ ~ +∞):
      + total_return / 5y           主收益
      + sharpe × 0.5                夏普
      + profit_factor × 1.5         盈亏比直接奖励 (> 1.0 + ; < 1.0 -)
      - drawdown × 1.0              回撤直接扣
      - forced_exit_ratio_pct × 0.01 频繁强退罚分
      + session_count >= 20 才有完整分; < 20 整体折半
    """
    ann = float(row.get("annualized_return_pct", 0.0)) / 100.0
    dd = float(row.get("max_drawdown_pct", 0.0)) / 100.0
    sharpe = float(row.get("sharpe", 0.0))
    pf = float(row.get("profit_factor", 0.0))
    forced_ratio = float(row.get("forced_exit_ratio", 0.0)) / 100.0
    n_session = int(row.get("session_count", 0))

    raw = (ann
           + sharpe * 0.5
           + (pf - 1.0) * 1.5
           - dd * 1.0
           - forced_ratio * 1.0)
    # session_count 不足 20 直接折半 (鼓励样本量)
    if n_session < 20:
        raw *= 0.5
    return raw


def meets_filters(row: dict) -> bool:
    """硬过滤: 太离谱直接淘汰 (避免 'top' 里出现 1 笔交易暴利)"""
    if int(row.get("session_count", 0)) < 5:
        return False
    if float(row.get("max_drawdown_pct", 100)) >= 50.0:
        return False
    return True


# ════════════════════════════════════════════
#  Worker
# ════════════════════════════════════════════

_WORKER_DF = None
_WORKER_INTERVAL = None
_WORKER_SYMBOL = None


def _worker_init(csv_path: str, interval: str, symbol: str):
    global _WORKER_DF, _WORKER_INTERVAL, _WORKER_SYMBOL
    _WORKER_DF = load_market_data(symbol, interval, 9999, csv_path=csv_path)
    _WORKER_INTERVAL = interval
    _WORKER_SYMBOL = symbol


def _apply_params(params: dict) -> None:
    """把 params 推入 config / tactical_config. 自动识别归属."""
    config_attrs = set(dir(config))
    tcfg_attrs = set(dir(tcfg))
    for k, v in params.items():
        if k in tcfg_attrs:
            setattr(tcfg, k, v)
        elif k in config_attrs:
            setattr(config, k, v)
        else:
            raise KeyError(f"参数 {k} 不在 config 也不在 tactical_config")


def _worker_run(args: tuple) -> Optional[dict]:
    params, year_window = args
    df = _WORKER_DF
    if year_window is not None:
        start, end = year_window
        sub = df.loc[start:end].copy()
        if len(sub) < 100:
            return None
        df_used = sub
    else:
        df_used = df

    _apply_params(params)
    config.STRATEGY_INTERVAL = _WORKER_INTERVAL
    config.BT_REALISTIC_FILLS = True
    # 必须显式注入 TOTAL_CAPITAL — BacktestRunner / business code 会 require_total_capital()
    config.TOTAL_CAPITAL = CAPITAL

    try:
        runner = BacktestRunner(df=df_used, symbol=_WORKER_SYMBOL,
                                capital=CAPITAL, interval=_WORKER_INTERVAL,
                                verbose=False)
        stats, _ = runner.run()
        # 在 runner 销毁前查 tempdb (tempdir 跟着 runner 生命周期)
        diag = compute_per_session_diagnostics(runner.db_path)
    except Exception as e:
        return {"__error__": str(e), **params}

    out = {
        "total_return_pct":     float(stats.total_return_pct),
        "annualized_return_pct": float(stats.annualized_return_pct),
        "max_drawdown_pct":     float(stats.max_drawdown_pct),
        "sharpe":               float(stats.sharpe_ratio),
        "session_count":        int(stats.session_count),
        "average_session_pnl":  float(stats.average_session_pnl),
        "max_session_loss":     float(stats.max_session_loss),
        "session_win_rate":     float(stats.session_win_rate_pct),
        "grid_close_win_rate":  float(stats.grid_close_win_rate_pct),
        "profit_factor":        float(stats.profit_factor),
        "forced_exit_count":    int(stats.forced_exit_count),
        "forced_exit_ratio":    float(stats.forced_exit_ratio),
        "defensive_mode_ratio": float(stats.defensive_mode_ratio),
        "profit_protect_exit":  int(stats.profit_protect_exit_count),
        "timeout_exit":         int(stats.timeout_exit_count),
        "avg_trades_per_session": float(stats.average_trades_per_session),
        "total_grid_sessions":  int(stats.total_grid_sessions),
        "total_trades":         int(stats.total_trades),
        # 诊断 (compute_per_session_diagnostics)
        "diag_buy_fills_per_session":  float(diag["buy_fills_per_session"]),
        "diag_sell_fills_per_session": float(diag["sell_fills_per_session"]),
        "diag_grid_close_count":       int(diag["grid_close_count"]),
        "diag_grid_pnl_total":         float(diag["grid_pnl_total"]),
        "diag_base_exit_loss":         float(diag["base_exit_loss"]),
        "diag_avg_realized_session":   float(diag["avg_realized_pnl_session"]),
        "diag_avg_unrealized_session": float(diag["avg_unrealized_pnl_session"]),
        "diag_sessions_with_grid_sell": int(diag["sessions_with_grid_sell"]),
        # exit reason 分类: 序列化成 JSON 便于 CSV 存 + 之后 MD 解析
        "exit_reason_breakdown": json.dumps(diag["exit_reason_breakdown"]),
        **params,
    }
    out["score"] = score_row(out)
    return out


# ════════════════════════════════════════════
#  Grid generation + output
# ════════════════════════════════════════════

PROFILE_GRIDS = {
    "p0": GRID_P0,
    "p1": GRID_P1,
    "p2": GRID_P2,
}


def expand_grid(grid: dict) -> list[dict]:
    keys = list(grid.keys())
    values = [grid[k] for k in keys]
    return [dict(zip(keys, combo)) for combo in itertools.product(*values)]


def truncate_df_to_years(csv_path: str, years: int, interval: str,
                         symbol: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    """返回 [start, end] 时间窗口, 取数据最后 years 年."""
    df = load_market_data(symbol, interval, 9999, csv_path=csv_path)
    end = df.index[-1]
    start = end - pd.Timedelta(days=int(years * 365.25))
    if start < df.index[0]:
        start = df.index[0]
    return start, end


def write_csv(rows: list[dict], path: Path) -> None:
    if not rows:
        path.write_text("")
        return
    keys = sorted(rows[0].keys())
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in keys})


def render_locked_md(out_path: Path) -> None:
    """渲染 P3/P4 锁定参数清单 (展示当前快照, 不优化)."""
    lines = [
        "# Locked Parameters Snapshot",
        "",
        "P3 / P4 参数在调参时**不参与搜索**, 仅作为系统约束展示.",
        "调整这些值的影响**不是策略优化**, 而是改变系统假设或运行成本.",
        "",
        "## P3 — 系统级 / 真实费用",
        "",
        "| 参数 | 当前值 | 说明 |",
        "|---|---:|---|",
    ]
    for k in LOCKED_P3:
        v = getattr(config, k, None)
        lines.append(f"| {k} | {v} | locked |")
    lines += [
        "",
        "## P4 — 频率 / 调度 / API 压力",
        "",
        "| 参数 | 当前值 | 说明 |",
        "|---|---:|---|",
    ]
    for k in LOCKED_P4:
        v = getattr(config, k, None)
        lines.append(f"| {k} | {v} | locked |")
    out_path.write_text("\n".join(lines), encoding="utf-8")


def render_top_md(top_rows: list[dict], out_path: Path,
                   profile: str, symbol: str, interval: str, years: float,
                   total_combos: int) -> None:
    lines = [
        f"# Tactical Tune — Profile **{profile}**",
        "",
        f"- Symbol: `{symbol}` | Interval: `{interval}` | Years: `{years}`",
        f"- Total combinations explored: **{total_combos}**",
        f"- Rows passing filters (sessions ≥ 5, dd < 50%): **{len(top_rows)}**",
        f"- Generated at: {datetime.now().isoformat()}",
        "",
        "## Scoring",
        "",
        "score = annual_ret + 0.5×sharpe + 1.5×(profit_factor−1) − drawdown − forced_exit_ratio",
        "  - session_count < 20 折半 (鼓励样本量)",
        "",
        "## Top Results",
        "",
    ]
    if not top_rows:
        lines.append("**没有满足 hard filters 的参数组合.**")
        out_path.write_text("\n".join(lines), encoding="utf-8")
        return

    keys_first = ["score", "total_return_pct", "max_drawdown_pct", "sharpe",
                  "profit_factor", "session_count", "session_win_rate",
                  "grid_close_win_rate", "forced_exit_ratio", "defensive_mode_ratio"]
    param_keys = sorted(k for k in top_rows[0].keys()
                         if k not in keys_first and k != "annualized_return_pct"
                         and not k.startswith("__"))

    lines.append("| Rank | " + " | ".join(keys_first + param_keys) + " |")
    lines.append("|" + "---|" * (1 + len(keys_first) + len(param_keys)))
    for i, r in enumerate(top_rows, 1):
        row_vals = [f"{i}"]
        for k in keys_first:
            v = r.get(k, "")
            if isinstance(v, float):
                row_vals.append(f"{v:.3f}")
            else:
                row_vals.append(str(v))
        for k in param_keys:
            v = r.get(k, "")
            row_vals.append(str(v))
        lines.append("| " + " | ".join(row_vals) + " |")

    # ── 每 top 的详细诊断 ──
    lines += [
        "",
        "## Per-top 诊断",
        "",
        "解读:",
        "- **exit_reason 分布**: session 退出原因占比. hard_stop / trend_risk / age 是主要 forced_exit 来源.",
        "- **buy/sell fills/session**: 每 session 平均成交笔数. 太低 (<2) 说明 session 还没充分跑.",
        "- **grid_close_count**: 网格平仓数 (GRID_SELL 配对). 0 说明 sell trigger 从未命中.",
        "- **base_exit_loss vs grid_pnl**: 底仓退出的现金差 vs 网格交易盈亏. 哪一方主导亏损?",
        "- **avg_realized vs avg_unrealized**: session 已实现 vs 浮盈差距, 揭示 'EXIT 时机' 问题.",
        "",
    ]
    for i, r in enumerate(top_rows, 1):
        try:
            breakdown = json.loads(r.get("exit_reason_breakdown", "{}") or "{}")
        except Exception:
            breakdown = {}
        total_n = sum(breakdown.values()) or 1
        breakdown_str = ", ".join(
            f"{k}={v} ({v/total_n*100:.0f}%)"
            for k, v in sorted(breakdown.items(), key=lambda x: -x[1])
        )
        params_str = ", ".join(f"{k}={r[k]}" for k in param_keys)
        lines.append(f"### Top {i}")
        lines.append(f"- Params: `{params_str}`")
        lines.append(f"- score={r['score']:.3f} | total_return={r['total_return_pct']:.2f}% | "
                      f"dd={r['max_drawdown_pct']:.2f}% | sharpe={r['sharpe']:.3f}")
        lines.append(f"- sessions={r['session_count']} | session_win_rate={r['session_win_rate']:.1f}% | "
                      f"profit_factor={r['profit_factor']:.2f}")
        lines.append(f"- **exit_reason**: {breakdown_str or 'n/a'}")
        lines.append(f"- forced_exit_ratio={r['forced_exit_ratio']:.1f}% | "
                      f"defensive_mode_ratio={r['defensive_mode_ratio']:.1f}% | "
                      f"profit_protect_exit={r['profit_protect_exit']} | "
                      f"timeout_exit={r['timeout_exit']}")
        lines.append(f"- **buy_fills/session**={r['diag_buy_fills_per_session']:.2f} | "
                      f"**sell_fills/session**={r['diag_sell_fills_per_session']:.2f}")
        lines.append(f"- **grid_close_count**={r['diag_grid_close_count']} "
                      f"(sessions with grid_sell={r['diag_sessions_with_grid_sell']}) | "
                      f"grid_close_win_rate={r['grid_close_win_rate']:.1f}%")
        lines.append(f"- grid_pnl_total=${r['diag_grid_pnl_total']:+.2f} | "
                      f"base_exit_loss=${r['diag_base_exit_loss']:+.2f}")
        lines.append(f"- avg_realized/session=${r['diag_avg_realized_session']:+.2f} | "
                      f"avg_unrealized/session=${r['diag_avg_unrealized_session']:+.2f}")
        lines.append("")

    # ── 整体诊断 / 调参建议 ──
    # 找 dominant exit reason 跨 top-N
    all_breakdown: dict[str, int] = {}
    for r in top_rows:
        try:
            bd = json.loads(r.get("exit_reason_breakdown", "{}") or "{}")
        except Exception:
            bd = {}
        for k, v in bd.items():
            all_breakdown[k] = all_breakdown.get(k, 0) + v
    total_all = sum(all_breakdown.values()) or 1
    dominant = sorted(all_breakdown.items(), key=lambda x: -x[1])

    lines += ["", "## 跨 top 整体诊断", ""]
    lines.append("整个 top 集合的 exit_reason 主导分布:")
    for k, v in dominant[:5]:
        lines.append(f"- `{k}`: {v} ({v/total_all*100:.1f}%)")

    # 自动建议
    suggestions = []
    if all_breakdown.get("hard_stop", 0) / max(1, total_all) > 0.25:
        suggestions.append(
            "🚨 hard_stop 占比 > 25%: 当前 SESSION_HARD_STOP_PCT 可能仍偏紧, 下一轮 P0 应继续放宽."
        )
    if all_breakdown.get("trend_risk", 0) / max(1, total_all) > 0.20:
        suggestions.append(
            "⚠️ trend_risk 主导: 考虑把 TREND_RISK_SCORE_DEFENSIVE / FORCE_EXIT 阈值上调, "
            "或者扩 P0 grid 包含 TREND_RISK_* 评分各组件权重 (tactical_rules._RISK_* 常数)."
        )
    if all_breakdown.get("price_deviation", 0) / max(1, total_all) > 0.20:
        suggestions.append(
            "⚠️ price_deviation 主导: 网格 EXIT_PRICE_DEVIATION_ATR 太严, P2 应包含该参数."
        )
    if all_breakdown.get("age", 0) / max(1, total_all) > 0.15:
        suggestions.append(
            "ℹ️ age 退出占比偏高: 考虑加大 SESSION_ABSOLUTE_MAX_AGE_BARS."
        )
    avg_grid_close = sum(r["diag_grid_close_count"] for r in top_rows) / max(1, len(top_rows))
    if avg_grid_close < 1.0:
        suggestions.append(
            "🚨 grid_close_count ≈ 0: sell trigger 从未命中 — 检查 GRID_SPACING_ATR_MULTIPLIER / "
            "spacing_pct (网格间距过大) 或者 EXIT 在 grid_sell 之前就把 session 杀了."
        )
    avg_realized = sum(r["diag_avg_realized_session"] for r in top_rows) / max(1, len(top_rows))
    avg_unrealized = sum(r["diag_avg_unrealized_session"] for r in top_rows) / max(1, len(top_rows))
    if avg_unrealized < 0 and abs(avg_unrealized) > abs(avg_realized) * 2:
        suggestions.append(
            "ℹ️ avg_unrealized << avg_realized: session EXIT 时仍带较大浮亏, "
            "可能 SOFT/HARD 触发节奏太早, 没等到反弹."
        )

    if suggestions:
        lines += ["", "### 自动建议", ""]
        for s in suggestions:
            lines.append(f"- {s}")

    lines += [
        "",
        "## 下一步建议 (固定流程)",
        "",
        "1. 不要直接把 top-1 参数写回 config.py — 先 walk-forward 验证 (--walk-forward)",
        "2. P0 跑完后, 在 top-1 附近跑 P1 (--anchor-params JSON of top-1 P0 params)",
        "3. P0+P1 top 3 邻域跑 P2 light",
        "4. 检查每行 forced_exit_ratio — 过高 (>50%) 说明参数仍偏紧",
        "5. profit_factor < 1.0 即使 total_return 为正, 也说明仰仗少数大盈利, 不稳健",
    ]
    out_path.write_text("\n".join(lines), encoding="utf-8")


# ════════════════════════════════════════════
#  Walk-forward (top N only)
# ════════════════════════════════════════════

def walk_forward_windows(df: pd.DataFrame,
                          train_years: float = 1.0,
                          valid_months: float = 3.0,
                          step_months: float = 3.0) -> list[tuple]:
    """返回 [(train_start, train_end, valid_start, valid_end), ...]

    默认 1y train / 3m valid / 3m step — 在 5y 数据上能切出 ~13 个 valid 窗口,
    比之前 2y/6m/6m 的 6 窗口更能识别"单窗口主导回报"的情况.
    """
    out = []
    first = df.index[0]
    last = df.index[-1]
    train_delta = pd.Timedelta(days=int(train_years * 365.25))
    valid_delta = pd.Timedelta(days=int(valid_months * 30.5))
    step_delta = pd.Timedelta(days=int(step_months * 30.5))

    cur = first + train_delta
    while cur + valid_delta <= last:
        train_start = cur - train_delta
        train_end = cur
        valid_start = cur
        valid_end = cur + valid_delta
        out.append((train_start, train_end, valid_start, valid_end))
        cur = cur + step_delta
    return out


# ════════════════════════════════════════════
#  Main
# ════════════════════════════════════════════

def main(argv=None):
    parser = argparse.ArgumentParser(description="Tactical Session Grid 调参")
    parser.add_argument("--symbol", default=config.SYMBOL)
    parser.add_argument("--interval", default=config.STRATEGY_INTERVAL)
    parser.add_argument("--csv", default=None,
                        help="数据 CSV 路径. 默认 data/<symbol>_<interval>.csv")
    parser.add_argument("--years", type=float, default=1.0,
                        help="回测年限 (取数据末尾 N 年). 默认 1 (smoke)")
    parser.add_argument("--profile", choices=["p0", "p1", "p2", "all"], default="p0")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--top", type=int, default=5)
    parser.add_argument("--walk-forward", action="store_true",
                        help="对 top-N 跑 walk-forward 验证 (默认 train 1y / valid 3m / step 3m)")
    parser.add_argument("--wf-train-years", type=float, default=1.0,
                        help="walk-forward train 窗口大小 (年), 默认 1.0")
    parser.add_argument("--wf-valid-months", type=float, default=3.0,
                        help="walk-forward valid 窗口大小 (月), 默认 3.0")
    parser.add_argument("--wf-step-months", type=float, default=3.0,
                        help="walk-forward 滚动步长 (月), 默认 3.0")
    parser.add_argument("--explicit-all", action="store_true",
                        help="确认你真要跑 all profile (默认拒绝 all 防误用)")
    parser.add_argument("--anchor-params", default="",
                        help='JSON 字符串: P0 → P1 → P2 串联时, 把上一阶段 top 参数锚定. '
                             '例: \'{"SESSION_SOFT_STOP_PCT":0.025}\'')
    parser.add_argument("--anchor-json-file", default="",
                        help="从 JSON 文件读取 anchor (替代 --anchor-params)")
    args = parser.parse_args(argv)

    if args.profile == "all" and not args.explicit_all:
        print("拒绝默认跑 all profile. 加 --explicit-all 才能继续.\n"
              "建议先按 p0 → p1 → p2 顺序跑.", file=sys.stderr)
        return 2

    # 解析 anchor (与 sweep grid 合并; sweep key 优先)
    anchor_params: dict = {}
    if args.anchor_params:
        anchor_params = json.loads(args.anchor_params)
    elif args.anchor_json_file:
        with open(args.anchor_json_file) as f:
            anchor_params = json.load(f)
    if anchor_params:
        print(f"Anchor params: {anchor_params}")

    csv_path = args.csv or f"data/{args.symbol.lower()}_{args.interval}.csv"
    if not os.path.exists(csv_path):
        print(f"❌ CSV 不存在: {csv_path}", file=sys.stderr)
        return 2

    # 时间窗口截断到末尾 N 年
    start, end = truncate_df_to_years(csv_path, args.years, args.interval, args.symbol)
    print(f"时间窗口: {start.date()} ~ {end.date()} (~{args.years}y)")

    # 决定要扫的 profile (顺序)
    if args.profile == "all":
        profiles = ["p0", "p1", "p2"]
    else:
        profiles = [args.profile]

    # 输出目录
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_root = ROOT / "reports" / "tuning" / (
        f"{args.symbol}_{args.interval}_{args.profile}_{ts}"
    )
    out_root.mkdir(parents=True, exist_ok=True)
    render_locked_md(out_root / "locked_params.md")
    print(f"输出目录: {out_root}")

    all_results: dict[str, list[dict]] = {}
    for prof in profiles:
        grid = PROFILE_GRIDS[prof]
        combos = expand_grid(grid)
        # 合并 anchor (不覆盖 sweep key)
        if anchor_params:
            merged_combos = []
            for c in combos:
                m = dict(anchor_params)
                m.update(c)  # sweep key 优先
                merged_combos.append(m)
            combos = merged_combos
        print(f"\n▶ {prof}: {len(combos)} combos × 1 window"
              + (f" (with anchor {len(anchor_params)} keys)" if anchor_params else ""))

        tasks = [(p, (start, end)) for p in combos]
        ctx = get_context("fork")
        with ctx.Pool(args.workers, initializer=_worker_init,
                       initargs=(csv_path, args.interval, args.symbol)) as pool:
            results = []
            t0 = time.time()
            last_print = t0
            for i, r in enumerate(pool.imap_unordered(_worker_run, tasks), 1):
                if r is not None and "__error__" not in r:
                    results.append(r)
                if time.time() - last_print >= 5 or i == len(tasks):
                    eta = (time.time() - t0) * (len(tasks) - i) / max(1, i)
                    print(f"    [{prof}] {i}/{len(tasks)}  "
                          f"elapsed={time.time()-t0:.0f}s eta={eta:.0f}s")
                    last_print = time.time()

        all_results[prof] = results
        write_csv(results, out_root / f"{prof}_grid.csv")

        # 过滤 + 排序
        filtered = [r for r in results if meets_filters(r)]
        filtered.sort(key=lambda r: r["score"], reverse=True)
        top = filtered[: args.top]
        render_top_md(top, out_root / f"{prof}_top.md",
                       profile=prof, symbol=args.symbol, interval=args.interval,
                       years=args.years, total_combos=len(combos))

        if top:
            print(f"  top-{args.top}:")
            for i, r in enumerate(top, 1):
                params_str = ", ".join(
                    f"{k}={r[k]}" for k in r if k in grid
                )
                print(f"    {i}. score={r['score']:.3f} "
                      f"ret={r['total_return_pct']:.1f}% "
                      f"dd={r['max_drawdown_pct']:.1f}% "
                      f"pf={r['profit_factor']:.2f} "
                      f"sessions={r['session_count']}  | {params_str}")
        else:
            print(f"  ⚠️ {prof}: 没有候选通过 hard filters")

    # walk-forward (仅对 top-N)
    if args.walk_forward:
        print("\n▶ Walk-forward (top-N only)")
        df_full = load_market_data(args.symbol, args.interval, 9999,
                                    csv_path=csv_path)
        windows = walk_forward_windows(
            df_full,
            train_years=args.wf_train_years,
            valid_months=args.wf_valid_months,
            step_months=args.wf_step_months,
        )
        print(f"   windows: {len(windows)} "
              f"(train={args.wf_train_years}y / valid={args.wf_valid_months}m "
              f"/ step={args.wf_step_months}m)")
        for prof in profiles:
            filtered = sorted(
                (r for r in all_results[prof] if meets_filters(r)),
                key=lambda r: r["score"], reverse=True,
            )[: args.top]
            wf_rows = []
            for rank, r in enumerate(filtered, 1):
                grid = PROFILE_GRIDS[prof]
                # 关键修复: walk-forward params 必须包含 anchor + grid 的合并集
                # 否则 worker 用模块当前默认值, 验证结果与 5y 全样本结果不一致
                p = dict(anchor_params) if anchor_params else {}
                for k in grid:
                    p[k] = r[k]
                # 也带上其它 P0/P1 keys (跨 profile 串联时, 某 grid 有的 key 现在不在当前 grid)
                for k in list(GRID_P0.keys()) + list(GRID_P1.keys()) + list(GRID_P2.keys()):
                    if k in r and k not in p:
                        p[k] = r[k]
                for (ts_, te_, vs_, ve_) in windows:
                    tasks = [(p, (vs_, ve_))]
                    ctx = get_context("fork")
                    with ctx.Pool(1, initializer=_worker_init,
                                   initargs=(csv_path, args.interval, args.symbol)) as pool:
                        out_list = list(pool.imap_unordered(_worker_run, tasks))
                    if out_list and out_list[0]:
                        wf_rows.append({
                            **out_list[0],
                            "wf_rank": rank,
                            "wf_train_start": str(ts_.date()),
                            "wf_train_end": str(te_.date()),
                            "wf_valid_start": str(vs_.date()),
                            "wf_valid_end": str(ve_.date()),
                        })
            write_csv(wf_rows, out_root / f"{prof}_walkforward.csv")
            print(f"   {prof} walkforward: {len(wf_rows)} rows")

    print(f"\n✓ 输出: {out_root}")
    print("⚠️  请人工 review top.md 后再决定是否更新参数;"
           " 本脚本不会自动写回 config.py.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
