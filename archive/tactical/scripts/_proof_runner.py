"""scripts/_proof_runner.py — 单 worker 跑一次 backtest, 提取 B1/B2/B3 指标.

设计:
  - 入口函数 run_one_trial(trial: dict) -> dict 必须可 pickle
  - 通过 env 变量覆盖 tactical_config / config 阈值, 不直接 mutate module 状态
  - 子进程内 importlib.reload(config / tactical_config / backtest) 让 env 生效
  - 返回 dict 含: trial_id + 输入参数 + B1/B2/B3 指标

注意 import 顺序: env 必须在 reload 之前设置, 否则 config._env_* helper 读不到.
"""
from __future__ import annotations

import importlib
import logging
import os
import sys
from typing import Any

# 确保 repo 根目录在 sys.path (worker 子进程有时继承, 有时不继承)
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


def run_one_trial(trial: dict[str, Any]) -> dict[str, Any]:
    """单次 backtest. trial 必须含:
        - trial_id: str
        - symbol: str
        - csv_path: str
        - interval: str ("4h" / "1h" 等)
        - interval_hours: float (4.0 / 1.0 等, 用于 hours→bars 换算)
        - capital: float
        - env_overrides: dict[str, str]
    """
    # 1. 设 env (在 reload 之前)
    for k, v in trial["env_overrides"].items():
        os.environ[k] = str(v)

    # 2. 子进程内 reload (避免父进程 fork 携带 cached module 状态)
    import config as _config
    importlib.reload(_config)

    import tactical_config as _tcfg
    importlib.reload(_tcfg)

    # backtest 读 config module-level 值, 也需要 reload
    import backtest as _bt
    importlib.reload(_bt)

    # 3. 屏蔽 backtest 内部 logging (避免 worker 输出污染父进程)
    logging.basicConfig(level=logging.CRITICAL)
    for name in ("GridTrader", "GridTrader.Bot", "GridTrader.Session",
                 "GridTrader.Risk", "GridTrader.Executor", "GridTrader.Factory",
                 "backtest", "grid_bot", "risk_manager", "session_manager"):
        logging.getLogger(name).setLevel(logging.CRITICAL)

    # 4. 加载数据
    df = _bt.load_market_data(
        symbol=trial["symbol"],
        interval=trial["interval"],
        days=99999,       # CSV 模式下 days 被忽略
        csv_path=trial["csv_path"],
    )

    # 5. 注入 runtime config (与 backtest.py main() 保持一致的模式)
    #    config.TOTAL_CAPITAL 是运行时注入值, reload 后仍是 None; 必须在 run() 前赋值.
    original_capital = _config.TOTAL_CAPITAL
    original_symbol = _config.SYMBOL
    original_interval = _config.STRATEGY_INTERVAL
    try:
        _config.TOTAL_CAPITAL = trial["capital"]
        _config.SYMBOL = trial["symbol"]
        _config.STRATEGY_INTERVAL = trial["interval"]

        runner = _bt.BacktestRunner(
            df=df,
            symbol=trial["symbol"],
            capital=trial["capital"],
            interval=trial["interval"],
            verbose=False,
        )
        stats, _events = runner.run()
    finally:
        _config.TOTAL_CAPITAL = original_capital
        _config.SYMBOL = original_symbol
        _config.STRATEGY_INTERVAL = original_interval

    # 6. 提取 B1/B2/B3 指标 (字段名已通过 backtest.py grep 验证)
    defensive_count = stats.defensive_mode_count
    forced_count = stats.forced_exit_count
    profit_protect_count = stats.profit_protect_exit_count
    b1_total = defensive_count + forced_count + profit_protect_count

    avg_session_hours = stats.avg_session_duration_hours
    interval_hours = trial["interval_hours"]
    avg_session_bars = avg_session_hours / interval_hours if interval_hours > 0 else 0.0

    result = dict(trial)
    result.update({
        "b1_trigger_total": int(b1_total),
        "b1_defensive": int(defensive_count),
        "b1_forced": int(forced_count),
        "b1_profit_protect": int(profit_protect_count),
        "b2_avg_session_bars": float(avg_session_bars),
        "b2_avg_session_hours": float(avg_session_hours),
        "b3_total_return_pct": float(stats.total_return_pct),
        "session_count": int(stats.session_count),
        # getattr 兜底: 字段存在直接读, 不存在给 0.0 (不 crash)
        "max_drawdown_pct": float(getattr(stats, "max_drawdown_pct", 0.0)),
        "sharpe": float(getattr(stats, "sharpe_ratio", 0.0)),
    })
    return result
