# 战术化网格不可落地性证明 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 通过理论 + 实测严证伪 (UVXY 4h + VXX 4h 各 5y × $10k cap) 证明战术化"短线收割"网格在合规边界内不能落地, 同时修复 F5 (entry_filter datetime.now() 漏洞) + F4 (README §3.2/§5 漂移).

**Architecture:** 复用现有 `SimulatedExecutor` + `GridBot` + `tactical_config` 通过 env 变量覆盖的设计. 新增 `scripts/prove_tactical.py` 多进程 sweep 驱动 (10 单维 + 联合 + 成本) 跑出 `runtime/experiments/tactical_proof/` 数据, 写 `reports/tactical_proof_of_impossibility.md` 含 Part 1 机制论证 + Part 2-4 实测数据 + 联合判定. 严证伪通过分支: TURBO 默认 OFF + EXPERIMENTAL banner + 中性 regression test.

**Tech Stack:** Python ≥3.10, pandas, multiprocessing (复用 `scripts/tune.py` 的 worker 模型), Alpaca v2 stocks/bars (拉 VXX, 复用 `data/qqq.py` pipeline), SimulatedExecutor 真实化撮合, unittest.

**Spec:** `docs/superpowers/specs/2026-05-14-tactical-impossibility-proof-design.md`

---

## File Structure

**新建**:
- `data/vxx.py` — Alpaca 拉 VXX 4h 前 5 年的脚本 (复用 qqq.py pattern)
- `data/vxx_4h.csv` — VXX 4h 前 5 年 OHLCV, 列与 `uvxy_4h.csv` 对齐 (c,h,l,n,o,t,v,vw)
- `scripts/prove_tactical.py` — sweep 驱动: `--symbol`, `--csv`, `--interval`,
  `--capital`, `--workers`, `--part {single,joint,cost}`. 不重复 `scripts/tune.py` 的
  walk-forward / OOS 逻辑, 只跑单一窗口的 backtest 矩阵.
- `scripts/_proof_runner.py` — 单 worker 实际跑一次 backtest 的子进程入口 (env 变量
  注入 tactical 阈值, 调用 `backtest.BacktestRunner`, 提取 B1/B2/B3 指标, 返回 dict).
  独立文件以便多进程序列化.
- `runtime/experiments/tactical_proof/uvxy_4h/{single_dim,joint,cost}.csv`
- `runtime/experiments/tactical_proof/vxx_4h/{single_dim,joint,cost}.csv`
- `reports/tactical_proof_of_impossibility.md`

**修改**:
- `entry_filter.py` L86, L98, L117, L124, L136, L173 — `evaluation_time` 必填
- `backtest.py` (无, capital 改在 config.py)
- `config.py` L123 — `BACKTEST_DEFAULT_CAPITAL = 2000.0` → `10000.0`; 严证伪通过时 L99 `TURBO_ENABLED` 默认 `"0"`
- `tactical_config.py` — 严证伪通过时顶部加 EXPERIMENTAL banner
- `test.py` — 严证伪通过时新增 `TestTacticalActionsReachable`
- `README.md` — §3.2 (6 状态) + §5 (新数据 + 证明结论链接)
- `findings.md` — 加证明结论简版
- `progress.md` — 会话日志追加

**只读 (不改)**:
- `CLAUDE.md`
- `PROJECT_STATUS.md` (hook 自动维护)
- 其他战术化期间改过的 legacy 路径 (state_machine / risk_manager / WAITING 解耦 etc.)

---

## Task 1: F5 修复 — entry_filter.py 移除 datetime.now() fallback

**Files:**
- Modify: `entry_filter.py` L86 / L98 / L117 / L124 / L136 / L173
- Test: `test.py` (现有 248 用例, 不新增)

**前置**: grep 已确认所有调用方 (`grid_bot.py:540/624`) 都传 `evaluation_time`. test.py 用 MagicMock 不受签名影响.

- [ ] **Step 1: 看 entry_filter.py 当前 L80-180 上下文**

```bash
sed -n '80,180p' entry_filter.py
```

预期: 看到 L86 `evaluate(self, hourly_df, evaluation_time: Optional[datetime] = None)`,
L98/L124/L136 三处 `timestamp=evaluation_time or datetime.now()`,
L173 `_passes_blackouts(self, evaluation_time: Optional[datetime] = None)`.

- [ ] **Step 2: 改 5 处签名 + 删 fallback**

Edit `entry_filter.py`:
- L86: `evaluation_time: Optional[datetime] = None` → `evaluation_time: datetime`
- L98: `timestamp=evaluation_time or datetime.now(),` → `timestamp=evaluation_time,`
- L117: `evaluation_time: Optional[datetime] = None` → `evaluation_time: datetime`
- L124: `timestamp=evaluation_time or datetime.now(),` → `timestamp=evaluation_time,`
- L136: `timestamp=evaluation_time or datetime.now(),` → `timestamp=evaluation_time,`
- L173: `evaluation_time: Optional[datetime] = None` → `evaluation_time: datetime`

L258 `current_date=(evaluation_time.date() if evaluation_time else None)` — 因为
evaluation_time 现在必填, 简化为 `current_date=evaluation_time.date()`.

如果文件顶部 `from typing import Optional` 是只为 `evaluation_time` 引入的 (grep 后判断),
可以保留 (其他签名可能也用), 不必清理.

- [ ] **Step 3: grep 调用方确认全部传 evaluation_time**

```bash
grep -n "entry_filter\.evaluate\|\.entry_filter\.evaluate" grid_bot.py backtest.py main.py
```

预期: `grid_bot.py:540`, `grid_bot.py:624` 两处, 都已传 `evaluation_time=now`.
`backtest.py` / `main.py` 不直接调用 (走 GridBot).

- [ ] **Step 4: 运行 test.py**

```bash
python test.py 2>&1 | tail -5
```

预期: `Ran 248 tests in X.Xs` + `OK`.

如果出现新失败, 检查测试是否直接构造 `EntryFilter().evaluate(...)` 而未传
`evaluation_time` — 那种用例需要补传 (用 `datetime(2026,1,1)` 等固定值).

- [ ] **Step 5: Commit**

```bash
git add entry_filter.py
git commit -m "fix: entry_filter 移除 datetime.now() fallback, evaluation_time 必填

修复 CLAUDE.md §9 违规: 业务模块不应读 wall-clock. 当前所有调用方
(grid_bot.py:540/624) 都已传 evaluation_time, 删除 fallback 后回归测试全绿.
未来若新调用方漏传, IDE 会立即报类型错误而不是回测/实盘行为分叉."
```

---

## Task 2: BACKTEST_DEFAULT_CAPITAL 2000 → 10000

**Files:**
- Modify: `config.py` L123

- [ ] **Step 1: Edit config.py L123**

```python
# 原:
BACKTEST_DEFAULT_CAPITAL = 2000.0
# 改为:
BACKTEST_DEFAULT_CAPITAL = 10000.0
```

- [ ] **Step 2: 跑回归测试**

```bash
python test.py 2>&1 | tail -5
```

预期: 248/248 全绿. (test.py setUp 用固定 2000 直接注入 `config.TOTAL_CAPITAL`,
不依赖默认值.)

- [ ] **Step 3: 跑一次 UVXY 4h backtest 看新基线**

```bash
TURBO_ENABLED=0 python backtest.py --csv data/uvxy_4h.csv --interval 4h --capital 10000 2>&1 | grep -E "总收益|年化|交易次数|最大回撤|Sharpe比率" | head -8
```

预期: 拿到新的 TURBO=OFF UVXY 4h baseline (替代 spec 中 +80.16% 的 2k 数据).
记录数字, Task 9 写 report 时用.

- [ ] **Step 4: Commit**

```bash
git add config.py
git commit -m "config: BACKTEST_DEFAULT_CAPITAL 2000 -> 10000

按 tactical impossibility proof spec §3 Part 0 要求, 回测固定 10000.
实盘 main.py 仍从 IBKR NetLiquidation 动态注入, 不受影响."
```

---

## Task 3: README §3.2 4 状态 → 6 状态

**Files:**
- Modify: `README.md` §3.2

- [ ] **Step 1: 找到 §3.2 状态描述位置**

```bash
grep -n "四状态\|4 状态\|SCANNING.*WAITING.*ACTIVE.*EXIT" README.md
```

预期: L66 附近, 文字 "四状态 FSM (`SCANNING → WAITING_ENTRY → ACTIVE_GRID → EXIT_PENDING`)"

- [ ] **Step 2: Edit README.md**

```
# 原:
四状态 FSM (`SCANNING → WAITING_ENTRY → ACTIVE_GRID → EXIT_PENDING`) + SQLite / JSON 持久化。
# 改为:
六状态 FSM (`SCANNING → WAITING_ENTRY → OFFENSIVE_GRID → DEFENSIVE_GRID → EXIT_PENDING → COOLDOWN`)
+ SQLite / JSON 持久化。旧 `ACTIVE_GRID` 在 DB 恢复时被翻译为 `OFFENSIVE_GRID`.
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: README §3.2 状态机描述更新为 6 状态

战术化扩展引入 OFFENSIVE_GRID / DEFENSIVE_GRID / COOLDOWN, ACTIVE_GRID
作为 legacy DB 行翻译保留. CLAUDE.md §1 描述同样过时但按用户指令不动."
```

---

## Task 4: 拉 VXX 4h 前 5 年数据 (data/vxx.py + vxx_4h.csv)

**Files:**
- Create: `data/vxx.py`
- Create: `data/vxx_4h.csv` (运行 vxx.py 生成)

- [ ] **Step 1: 写 data/vxx.py (复用 qqq.py pattern)**

```python
"""data/vxx.py — 拉 VXX 4h 前 5 年 Alpaca 数据.

复用 data/qqq.py 的 pattern. VXX (iPath Series B S&P 500 VIX Short-Term Futures ETN)
是战术化"理论最优"对照标的 (1× VIX 期货, 高波动 + 强 mean-revert, 流动性 ~$1B/d).

输出列与 data/uvxy_4h.csv 对齐: c,h,l,n,o,t,v,vw
"""
import os
from datetime import datetime, timedelta

import pandas as pd
import requests

# 凭证按 CLAUDE.md §10 保留, 与 qqq.py 同源.
api_key = "PKQGZMEOZCJCMUWKH73FYVN4V3"
api_secret = "CQPYmKq49PaNpgLnrN5weWb3pPLMN35SEp7UpzGC43LR"

url = "https://data.alpaca.markets/v2/stocks/bars"

headers = {
    "APCA-API-KEY-ID": api_key,
    "APCA-API-SECRET-KEY": api_secret,
}

# 前 5 年: 今天往前 5 年到今天
end_dt = datetime(2026, 5, 15)
start_dt = end_dt - timedelta(days=365 * 5)

params = {
    "symbols": "VXX",
    "timeframe": "4Hour",
    "start": start_dt.strftime("%Y-%m-%d"),
    "end": end_dt.strftime("%Y-%m-%d"),
    "feed": "iex",
    "limit": 10000,
}

all_bars = []
page_token = None

while True:
    if page_token:
        params["page_token"] = page_token
    r = requests.get(url, headers=headers, params=params)
    r.raise_for_status()
    data = r.json()
    bars = data.get("bars", {}).get("VXX", [])
    all_bars.extend(bars)
    page_token = data.get("next_page_token")
    if not page_token:
        break

df = pd.DataFrame(all_bars)
out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vxx_4h.csv")
df.to_csv(out_path, index=False)

print(df.head())
print(df.tail())
print(f"总行数: {len(df)}")
print(f"窗口: {df['t'].min()} → {df['t'].max()}")
```

- [ ] **Step 2: 跑 data/vxx.py**

```bash
cd /Users/krisjiang/Desktop/grid && python data/vxx.py
```

预期输出: 类似 `总行数: ~3000-4500` (4h × 252d × 6.5h/4h × 5y ≈ 2050;
若 Alpaca free tier 限制 IEX 数据起始时间 ~2016, 应能拉满 5 年).
**若拉不满 5 年**: 记录实际窗口起止时间, Part 1C 表格中注明.

- [ ] **Step 3: 验证 vxx_4h.csv 列对齐 uvxy_4h.csv**

```bash
head -2 data/vxx_4h.csv
head -2 data/uvxy_4h.csv
```

预期: 两文件 header 一致 (`c,h,l,n,o,t,v,vw`), 数据格式一致 (第一列是 close, 时间在第 6 列 `t`).

如果列不对齐 (例如 Alpaca 加了新字段), 在 vxx.py 末尾加 `df = df[["c","h","l","n","o","t","v","vw"]]` 显式投影.

- [ ] **Step 4: 跑一次 VXX 4h backtest 看新基线**

```bash
TURBO_ENABLED=0 python backtest.py --csv data/vxx_4h.csv --interval 4h --capital 10000 2>&1 | grep -E "总收益|年化|交易次数|最大回撤|Sharpe比率" | head -8
```

预期: 拿到 VXX 4h TURBO=OFF baseline. **记录数字, 这是 Part 4 判定 B3 阈值的对照.**

如果 0 笔交易: 说明 UVXY-tuned 入场参数 (`ENTRY_MAX_ADX=20`, `ATR_PCT ∈ [0.020, 0.045]`)
在 VXX 上不通过 — VXX 1× 比 UVXY 1.5× ATR% 低. 这种情况下 spec §5 中"VXX 上跑 sweep"
变得不可行, 需要先在 prove_tactical.py 里加 VXX-specific 入场参数覆盖 (env var 注入).
**遇到此情况停下来报告用户**, 不要擅自改 config.py 入场默认值.

- [ ] **Step 5: Commit**

```bash
git add data/vxx.py data/vxx_4h.csv
git commit -m "data: 加 VXX 4h 前 5 年 Alpaca 数据 + 拉数脚本

战术化不可落地性证明 spec §3 Part 0 要求. VXX (1× VIX 短期期货) 与 UVXY (1.5×)
同源但振幅更小, 是战术化'理论最优'对照标的. 与 uvxy_4h.csv 列对齐, 直接喂
DataProvider/BacktestRunner."
```

---

## Task 5: 单 worker proof runner (scripts/_proof_runner.py)

**Files:**
- Create: `scripts/_proof_runner.py`
- Create: `tests/proof/test_proof_runner.py` (轻量单测, 1 个用例)

设计取向: worker 入口必须可 pickle (`multiprocessing.Pool` 要求 top-level 函数 + 简单参数). 把 backtest 调用 + 指标提取封装在这里, 让 `prove_tactical.py` 只负责生成参数矩阵 + 并行调度 + CSV 输出.

- [ ] **Step 1: 写 scripts/_proof_runner.py**

```python
"""scripts/_proof_runner.py — 单 worker 跑一次 backtest, 提取 B1/B2/B3 指标.

设计:
  - 入口函数 run_one_trial(trial: dict) -> dict 必须可 pickle
  - 通过 env 变量覆盖 tactical_config / config 阈值, 不直接 mutate module 状态
    (避免 worker 间状态污染; multiprocessing fork 后子进程独立读 env)
  - 子进程内 import config / tactical_config 时, env 已设置, 阈值通过 _env_* helper
    自动读到
  - 返回 dict 含: trial_id, 参数, B1 触发计数, B2 平均 session 寿命, B3 总收益率,
    sessions 数, defensive/forced/profit_protect 各自计数

import 注意: 必须延迟到 worker 内做 (multiprocessing fork 后), 否则父进程 import
的 config/tactical_config 会被 fork 携带, env 变更无效.
"""
from __future__ import annotations

import logging
import os
import tempfile
from typing import Any


def run_one_trial(trial: dict[str, Any]) -> dict[str, Any]:
    """单次 backtest. trial 必须含:
        - trial_id: str
        - csv_path: str
        - interval: str
        - capital: float
        - env_overrides: dict[str, str]  (env 变量名 → 值, 全部 stringified)
    """
    # 1. 设 env (在 import config / tactical_config 之前)
    for k, v in trial["env_overrides"].items():
        os.environ[k] = str(v)

    # 2. 子进程内 import (避免父进程 cache)
    import importlib
    import config as _config
    importlib.reload(_config)
    import tactical_config as _tcfg
    importlib.reload(_tcfg)

    # backtest 模块也需要重 import, 它读 config 的 module-level 值
    import backtest as _bt
    importlib.reload(_bt)

    # 3. 屏蔽 backtest 内部 logging (worker 太吵)
    logging.basicConfig(level=logging.CRITICAL)
    for name in ("GridTrader", "GridTrader.Bot", "GridTrader.Session",
                 "GridTrader.Risk", "GridTrader.Executor"):
        logging.getLogger(name).setLevel(logging.CRITICAL)

    # 4. 跑 backtest
    df = _bt.load_market_data(
        symbol=trial.get("symbol", "PROOF"),
        interval=trial["interval"],
        days=99999,  # CSV 模式下 days 被忽略
        csv=trial["csv_path"],
    )
    # 用 temp DB 避免 worker 冲突
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "trades.db")
        os.environ["DB_FILE"] = db_path
        runner = _bt.BacktestRunner(
            df=df, symbol=trial.get("symbol", "PROOF"),
            capital=trial["capital"],
            interval=trial["interval"],
            verbose=False,
        )
        stats, _events = runner.run()

    # 5. 提取 B1/B2/B3 指标
    sm = runner.bot.session_manager
    # session-level 计数: sm 自己没存 defensive/forced/pp 总计, 从 trade_logger
    # 或 stats 拿. BacktestRunner.print_stats 里有这部分汇总; 直接读 stats 对象.
    defensive_count = getattr(stats, "defensive_session_count", 0)
    forced_exit_count = getattr(stats, "force_exit_count", 0)
    profit_protect_count = getattr(stats, "profit_protect_exit_count", 0)
    b1_total = defensive_count + forced_exit_count + profit_protect_count

    avg_session_bars = getattr(stats, "avg_session_age_bars", 0.0)
    total_return_pct = getattr(stats, "total_return_pct", 0.0)
    session_count = getattr(stats, "session_count", 0)

    result = dict(trial)
    result.update({
        "b1_trigger_total": b1_total,
        "b1_defensive": defensive_count,
        "b1_forced": forced_exit_count,
        "b1_profit_protect": profit_protect_count,
        "b2_avg_session_bars": avg_session_bars,
        "b3_total_return_pct": total_return_pct,
        "session_count": session_count,
    })
    return result
```

**注意**: spec 中 stats 字段名 (`defensive_session_count` / `force_exit_count` 等) 是
**待确认**. backtest.py L380-450 的 stats 装配应该有这些字段 (从 BacktestRunner.print_stats
出现的"Defensive sessions: 0" "Forced exit: 0" 推断). 在 Step 2 单测时验证, 如果字段名
不一致, 修正 `getattr` 默认值或在 BacktestRunner 里补字段.

- [ ] **Step 2: 看 backtest.py 中 stats 实际有的字段**

```bash
grep -n "stats\." backtest.py | grep -E "defensive|force|profit_protect|session" | head -20
```

确认: stats 对象上"Defensive sessions / Forced exit / Profit-protect exit" 这些
print_stats 输出对应的字段名. 若实际字段名不同 (例如 `defensive_count`), 在
`_proof_runner.py` 改 `getattr` 的 key. 若 stats 根本没存这些字段 (只是 print_stats
当场算的), **需要在 BacktestRunner._build_stats 里补存**:
- 跑过的 session_manager.engine_exit_signal_count: backtest 不直接拿, 需要从 db 查
- 简单处理: 在 BacktestRunner.run 收尾时, `runner._stats.b1_defensive = sum(s.mode == 'defensive' for s in closed_sessions)` 等
- 这块在 Step 3 单测验证如果发现缺字段, 在 backtest.py 加 5 行就行, 不写新文件.

- [ ] **Step 3: 写 tests/proof/test_proof_runner.py**

```python
"""轻量 smoke test: run_one_trial 在 UVXY 4h 上能跑通, 返回字段齐全."""
import os
import sys
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO_ROOT)

from scripts._proof_runner import run_one_trial


class TestProofRunner(unittest.TestCase):
    def test_smoke_uvxy_4h_default(self):
        trial = {
            "trial_id": "smoke_001",
            "symbol": "UVXY",
            "csv_path": os.path.join(REPO_ROOT, "data", "uvxy_4h.csv"),
            "interval": "4h",
            "capital": 10000.0,
            "env_overrides": {"TURBO_ENABLED": "1"},
        }
        result = run_one_trial(trial)
        # 字段齐全
        for k in ("b1_trigger_total", "b1_defensive", "b1_forced",
                  "b1_profit_protect", "b2_avg_session_bars",
                  "b3_total_return_pct", "session_count"):
            self.assertIn(k, result)
        # 数值合理性 (5y UVXY 默认 TURBO=ON 上轮实测: ~30 sessions, return +70%, b1=0)
        self.assertGreater(result["session_count"], 5)
        self.assertGreater(result["b3_total_return_pct"], -100.0)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 4: 跑 smoke test**

```bash
mkdir -p tests/proof && touch tests/proof/__init__.py
python -m unittest tests.proof.test_proof_runner -v
```

预期: PASS. 第一次跑可能慢 (5y UVXY 4h backtest 一遍 ~30-60s).

如果失败 (字段名不对 / KeyError): 根据 Step 2 grep 结果调整 `_proof_runner.py` 的
getattr key 或在 backtest.py BacktestRunner 里补字段, 再跑直到 PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/_proof_runner.py tests/proof/__init__.py tests/proof/test_proof_runner.py
# 如果 Step 2 修改了 backtest.py 加字段:
# git add backtest.py
git commit -m "feat(proof): 加 _proof_runner 单 worker 入口 + smoke test

封装一次 backtest + B1/B2/B3 指标提取. 通过 env 变量注入 tactical_config /
config 阈值, 子进程内 importlib.reload 让阈值生效. 为 scripts/prove_tactical.py
并行 sweep 做底座."
```

---

## Task 6: prove_tactical.py 主驱动 + 单维 sweep

**Files:**
- Create: `scripts/prove_tactical.py`

- [ ] **Step 1: 写 scripts/prove_tactical.py 骨架 (argparse + symbol/csv 解析 + dispatch)**

```python
"""scripts/prove_tactical.py — 战术化不可落地性证明 sweep 驱动.

Usage:
    python scripts/prove_tactical.py --csv data/uvxy_4h.csv --symbol UVXY --interval 4h \\
        --capital 10000 --part single --workers 7
    python scripts/prove_tactical.py --csv data/vxx_4h.csv --symbol VXX --interval 4h \\
        --capital 10000 --part joint --workers 7
    python scripts/prove_tactical.py --csv data/uvxy_4h.csv --symbol UVXY --interval 4h \\
        --capital 10000 --part cost --workers 7

输出: runtime/experiments/tactical_proof/{symbol}_{interval}/{single_dim,joint,cost}.csv

Spec: docs/superpowers/specs/2026-05-14-tactical-impossibility-proof-design.md
"""
from __future__ import annotations

import argparse
import csv
import itertools
import multiprocessing as mp
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts._proof_runner import run_one_trial


# ════════════════════════════════════════
#  单维 sweep 参数表
# ════════════════════════════════════════
SINGLE_DIM_KNOBS: dict[str, list] = {
    "SESSION_HARD_STOP_PCT": [0.02, 0.05, 0.10, 0.15, 0.20, 1.0],
    "SESSION_SOFT_STOP_PCT": [0.01, 0.03, 0.05, 0.10, 0.20, 1.0],
    "SESSION_MAX_AGE_BARS": [6, 12, 20, 30, 60, 99999],
    "TREND_RISK_SCORE_DEFENSIVE": [40.0, 50.0, 60.0, 70.0, 80.0, 999.0],
    "TREND_RISK_SCORE_FORCE_EXIT": [60.0, 70.0, 80.0, 90.0, 999.0],
    "SESSION_MIN_PROFIT_TO_PROTECT_PCT": [0.01, 0.02, 0.03, 0.05, 1.0],
    "SESSION_TRAILING_GIVEBACK_RATIO": [0.30, 0.50, 0.70, 1.0],
    "TACTICAL_OVERRIDE_GRID_ENGINE_EXIT": [0, 1],  # bool 用 0/1
    "TACTICAL_MAX_BUY_DEPTH_ATR": [0.0, 1.0, 1.5, 2.0, 3.0],
    "SESSION_NO_FILL_TIMEOUT_BARS": [0, 12, 24, 48, 96],
}

# 联合 sweep fallback 轴 (Part 2 之后用数据驱动选, fallback 见 spec)
JOINT_AXES_FALLBACK: list[tuple[str, list]] = [
    ("SESSION_MAX_AGE_BARS", [12, 20, 30, 60, 99999]),
    ("TACTICAL_OVERRIDE_GRID_ENGINE_EXIT", [0, 1]),
    ("TREND_RISK_SCORE_DEFENSIVE", [50.0, 60.0, 70.0, 999.0]),
    ("SESSION_HARD_STOP_PCT", [0.05, 0.10, 0.20]),
]

# 成本敏感性 (slip_bps, comm_per_share)
COST_GRID: list[tuple[float, float]] = [
    (5.0, 0.0035),    # 当前默认
    (7.5, 0.00525),   # +50%
    (10.0, 0.007),    # +100%
    (3.0, 0.00175),   # -40%
]


def _base_env(args) -> dict[str, str]:
    """基础 env: TURBO=1 (战术 ON) + 其他保持默认."""
    return {"TURBO_ENABLED": "1"}


def _make_single_dim_trials(args) -> list[dict]:
    trials = []
    for knob, values in SINGLE_DIM_KNOBS.items():
        for v in values:
            env = _base_env(args)
            env[knob] = str(v)
            trials.append({
                "trial_id": f"single_{knob}_{v}",
                "symbol": args.symbol,
                "csv_path": args.csv,
                "interval": args.interval,
                "capital": args.capital,
                "env_overrides": env,
                "knob": knob,
                "value": v,
            })
    return trials


def _make_joint_trials(args, axes: list) -> list[dict]:
    trials = []
    for combo in itertools.product(*[v for _, v in axes]):
        env = _base_env(args)
        label_parts = []
        for (knob, _), val in zip(axes, combo):
            env[knob] = str(val)
            label_parts.append(f"{knob}={val}")
        trials.append({
            "trial_id": "joint_" + "_".join(label_parts),
            "symbol": args.symbol,
            "csv_path": args.csv,
            "interval": args.interval,
            "capital": args.capital,
            "env_overrides": env,
            **{f"axis_{i}_{knob}": val for i, ((knob, _), val) in enumerate(zip(axes, combo))},
        })
    return trials


def _make_cost_trials(args) -> list[dict]:
    """成本敏感性: 战术默认 (全部 999/1.0/0/0) + 改 slip/comm."""
    trials = []
    for slip, comm in COST_GRID:
        env = _base_env(args)
        env["BT_MARKET_SLIP_BPS"] = str(slip)
        env["IBKR_COMMISSION_PER_SHARE"] = str(comm)
        trials.append({
            "trial_id": f"cost_slip={slip}_comm={comm}",
            "symbol": args.symbol,
            "csv_path": args.csv,
            "interval": args.interval,
            "capital": args.capital,
            "env_overrides": env,
            "slip_bps": slip,
            "comm_per_share": comm,
        })
    # 再跑一组 TURBO=0 同样 cost grid 作对照
    for slip, comm in COST_GRID:
        env = {"TURBO_ENABLED": "0"}
        env["BT_MARKET_SLIP_BPS"] = str(slip)
        env["IBKR_COMMISSION_PER_SHARE"] = str(comm)
        trials.append({
            "trial_id": f"cost_OFF_slip={slip}_comm={comm}",
            "symbol": args.symbol,
            "csv_path": args.csv,
            "interval": args.interval,
            "capital": args.capital,
            "env_overrides": env,
            "slip_bps": slip,
            "comm_per_share": comm,
        })
    return trials


def _output_dir(args) -> Path:
    d = REPO_ROOT / "runtime" / "experiments" / "tactical_proof" / \
        f"{args.symbol.lower()}_{args.interval}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_csv(out_path: Path, rows: list[dict]) -> None:
    if not rows:
        print(f"[warn] 无结果, 跳过 {out_path}")
        return
    # 合并所有 key 作为表头
    keys = []
    for r in rows:
        for k in r.keys():
            if k not in keys:
                keys.append(k)
    # env_overrides 是 dict, 展开成 JSON str
    import json
    for r in rows:
        if "env_overrides" in r and isinstance(r["env_overrides"], dict):
            r["env_overrides"] = json.dumps(r["env_overrides"], sort_keys=True)
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"[ok] 写入 {out_path} ({len(rows)} rows)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--interval", default="4h")
    parser.add_argument("--capital", type=float, default=10000.0)
    parser.add_argument("--part", required=True, choices=["single", "joint", "cost"])
    parser.add_argument("--workers", type=int, default=7)
    args = parser.parse_args()

    if args.part == "single":
        trials = _make_single_dim_trials(args)
        out_path = _output_dir(args) / "single_dim.csv"
    elif args.part == "joint":
        # 数据驱动选轴: 读 single_dim.csv, 按 spec §3 Part 3 算法选 top-4
        # 若 single_dim.csv 不存在或全军覆没, fallback
        axes = _select_joint_axes_from_single_dim(args) or JOINT_AXES_FALLBACK
        print(f"[info] 联合 sweep 选择的轴: {[k for k, _ in axes]}")
        trials = _make_joint_trials(args, axes)
        out_path = _output_dir(args) / "joint.csv"
    else:  # cost
        trials = _make_cost_trials(args)
        out_path = _output_dir(args) / "cost.csv"

    print(f"[info] 启动 {len(trials)} 个 trial, workers={args.workers}")

    with mp.Pool(processes=args.workers) as pool:
        results = []
        for i, r in enumerate(pool.imap_unordered(run_one_trial, trials)):
            results.append(r)
            print(f"  [{i+1}/{len(trials)}] {r['trial_id']}: "
                  f"ret={r['b3_total_return_pct']:+.2f}% "
                  f"sessions={r['session_count']} "
                  f"b1={r['b1_trigger_total']} "
                  f"avg_age={r['b2_avg_session_bars']:.1f}bars")

    _write_csv(out_path, results)


def _select_joint_axes_from_single_dim(args) -> list[tuple[str, list]] | None:
    """Spec §3 Part 3 算法: 读 single_dim.csv, 算每个 knob 的 score, 取 top-4.

    score = (#B 满足条数) + min(1.0, b3_return / b3_baseline).
    b3_baseline = 同标的 TURBO=OFF 跑出的回报 (本函数内动态跑一次拿到).
    """
    single_csv = _output_dir(args) / "single_dim.csv"
    if not single_csv.exists():
        return None

    # 拿 baseline (TURBO=OFF, 默认 cost)
    print("[info] 跑 TURBO=OFF baseline 用于 joint axes 选择...")
    baseline = run_one_trial({
        "trial_id": "baseline_off",
        "symbol": args.symbol,
        "csv_path": args.csv,
        "interval": args.interval,
        "capital": args.capital,
        "env_overrides": {"TURBO_ENABLED": "0"},
    })
    base_ret = baseline["b3_total_return_pct"]
    print(f"[info] TURBO=OFF baseline ret = {base_ret:+.2f}%")

    import json
    rows = list(csv.DictReader(open(single_csv)))
    # 每个 knob 的最佳 score
    knob_scores: dict[str, float] = {}
    for row in rows:
        knob = row.get("knob")
        if not knob:
            continue
        try:
            b1 = int(row["b1_trigger_total"])
            b2 = float(row["b2_avg_session_bars"])
            b3 = float(row["b3_total_return_pct"])
        except (KeyError, ValueError):
            continue
        n_b = (1 if b1 > 0 else 0) + (1 if b2 <= 20 else 0) + (1 if b3 >= base_ret else 0)
        normalized_b3 = min(1.0, max(0.0, b3 / max(base_ret, 1e-9)))
        score = n_b + normalized_b3
        knob_scores[knob] = max(knob_scores.get(knob, -1.0), score)

    # 排序取 top-4
    ranked = sorted(knob_scores.items(), key=lambda kv: -kv[1])[:4]
    # 过滤"全军覆没"(score < 0.1) → 走 fallback
    if not ranked or ranked[0][1] < 0.1:
        return None
    selected_knobs = [k for k, _ in ranked]
    # 从 SINGLE_DIM_KNOBS 取该轴的取值表 (但取值数限制 3-5)
    axes = []
    for knob in selected_knobs:
        vals = SINGLE_DIM_KNOBS[knob]
        # 限制 ≤ 5 个值, 优先保留默认 + 极端
        if len(vals) > 5:
            vals = vals[:5]
        axes.append((knob, vals))
    return axes


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 跑 single dim sweep on UVXY 4h (smoke)**

```bash
python scripts/prove_tactical.py --csv data/uvxy_4h.csv --symbol UVXY --interval 4h \
    --capital 10000 --part single --workers 7
```

预期: ~54 个 trial 跑完, 屏幕滚动每个 trial 一行进度. 总耗时 ~10-20 分钟
(每个 backtest ~30-60s, 7 worker 并行).

输出文件: `runtime/experiments/tactical_proof/uvxy_4h/single_dim.csv` 含 ~54 行.

如果出现 worker 死掉 / pickle 错误: 看 `_proof_runner.py` 是否依赖了 module-level
状态. 多进程 fork 时, 父进程已 import 的 module 会被 fork 携带, env 变更对已 import
的 module 无效 — 这就是为什么 _proof_runner.py 内做 `importlib.reload`. 若仍失败,
改用 `spawn` start method:
```python
mp.set_start_method("spawn", force=True)
```

- [ ] **Step 3: 跑 single dim sweep on VXX 4h**

```bash
python scripts/prove_tactical.py --csv data/vxx_4h.csv --symbol VXX --interval 4h \
    --capital 10000 --part single --workers 7
```

预期: 同上, 输出 `runtime/experiments/tactical_proof/vxx_4h/single_dim.csv`.

**如果 VXX 上很多 trial 0 笔交易** (入场过滤拒绝): 这是 Task 4 Step 4 警告过的
情况. 在 prove_tactical.py 加 `_base_env` 里给 VXX 注入合理 entry 阈值
(例如 `ENTRY_MIN_ATR_PCT=0.010`), 重跑. 不擅自改 config.py 默认.

- [ ] **Step 4: Commit**

```bash
git add scripts/prove_tactical.py
git add runtime/experiments/tactical_proof/  # CSV 落盘
git commit -m "feat(proof): prove_tactical.py 单维 sweep + UVXY/VXX single_dim 数据

10 个 tactical 旋钮 × 6 点 = 54 trial × 2 标的 = 108 backtest. 多进程 7 worker
并行, 通过 env override + importlib.reload 在子进程内生效阈值.
为 joint sweep 数据驱动选轴 (spec §3 Part 3) 准备底座."
```

---

## Task 7: 联合 sweep + 成本敏感性 sweep

**Files:**
- Modify: 无 (复用 scripts/prove_tactical.py)
- Generate: `runtime/experiments/tactical_proof/{uvxy_4h,vxx_4h}/{joint,cost}.csv`

- [ ] **Step 1: 跑 joint sweep UVXY**

```bash
python scripts/prove_tactical.py --csv data/uvxy_4h.csv --symbol UVXY --interval 4h \
    --capital 10000 --part joint --workers 7
```

预期: prove_tactical.py `_select_joint_axes_from_single_dim` 自动从 single_dim.csv
选 4 个 top-score 轴; 若全军覆没 fallback 到预设. 总 trial 数 ≈ 100-150, 跑 ~20-40 min.
输出: `uvxy_4h/joint.csv`.

- [ ] **Step 2: 跑 joint sweep VXX**

```bash
python scripts/prove_tactical.py --csv data/vxx_4h.csv --symbol VXX --interval 4h \
    --capital 10000 --part joint --workers 7
```

预期: 同上, 输出 `vxx_4h/joint.csv`.

- [ ] **Step 3: 跑 cost sweep UVXY + VXX**

```bash
python scripts/prove_tactical.py --csv data/uvxy_4h.csv --symbol UVXY --interval 4h \
    --capital 10000 --part cost --workers 4
python scripts/prove_tactical.py --csv data/vxx_4h.csv --symbol VXX --interval 4h \
    --capital 10000 --part cost --workers 4
```

每个 8 trial (4 cost × {ON, OFF}), 跑 ~3-5 分钟. 输出: 两份 `cost.csv`.

- [ ] **Step 4: 用 Python 对 4 份 CSV 各做一行总结**

```bash
python -c "
import csv
import os
for sym in ('uvxy_4h', 'vxx_4h'):
    print(f'\n=== {sym} ===')
    for part in ('single_dim', 'joint', 'cost'):
        p = f'runtime/experiments/tactical_proof/{sym}/{part}.csv'
        if not os.path.exists(p):
            print(f'  {part}: 文件缺失')
            continue
        rows = list(csv.DictReader(open(p)))
        b1_count = sum(1 for r in rows if int(r.get('b1_trigger_total', 0)) > 0)
        b2_count = sum(1 for r in rows if float(r.get('b2_avg_session_bars', 999)) <= 20)
        max_ret = max((float(r.get('b3_total_return_pct', -999)) for r in rows), default=0)
        print(f'  {part}: {len(rows)} trials, b1>0: {b1_count}, b2<=20: {b2_count}, max_ret: {max_ret:+.2f}%')
"
```

- [ ] **Step 5: Commit**

```bash
git add runtime/experiments/tactical_proof/
git commit -m "feat(proof): 联合 sweep + 成本敏感性 sweep 完成 (UVXY + VXX)

joint sweep (~120 trial × 2 标的) 用 single_dim 数据驱动选轴 (top-4 by score).
cost sweep 验证 spec §3 Part 1B(iv) 成本敏感性必要条件.
全部数据落 runtime/experiments/tactical_proof/."
```

---

## Task 8: 联合判定 + 写 reports/tactical_proof_of_impossibility.md

**Files:**
- Create: `reports/tactical_proof_of_impossibility.md`
- Read-only: 4 份 sweep CSV

- [ ] **Step 1: 用 Python 跑联合判定**

```bash
python -c "
import csv, os, json
def load(sym, part):
    p = f'runtime/experiments/tactical_proof/{sym}/{part}.csv'
    return list(csv.DictReader(open(p))) if os.path.exists(p) else []

def b_ok(row, baseline_ret):
    b1 = int(row.get('b1_trigger_total', 0)) > 0
    b2 = float(row.get('b2_avg_session_bars', 999)) <= 20
    b3 = float(row.get('b3_total_return_pct', -999)) >= baseline_ret
    return b1, b2, b3

# baseline (从 cost.csv 的 cost_OFF_slip=5.0_comm=0.0035 拿; 它是 TURBO=OFF 默认 cost)
for sym in ('uvxy_4h', 'vxx_4h'):
    baseline_row = next((r for r in load(sym, 'cost') if 'OFF_slip=5.0' in r['trial_id']), None)
    if not baseline_row:
        print(f'{sym}: 找不到 OFF baseline'); continue
    base_ret = float(baseline_row['b3_total_return_pct'])
    print(f'\n=== {sym}: TURBO=OFF baseline = {base_ret:+.2f}% ===')
    for part in ('single_dim', 'joint', 'cost'):
        rows = load(sym, part)
        # 找满足 B 三条的点
        satisfying = []
        for r in rows:
            b1, b2, b3 = b_ok(r, base_ret)
            if b1 and b2 and b3:
                satisfying.append(r)
        # 找'最接近'的点 (满足 B 条数 + 距离)
        near = []
        for r in rows:
            b1, b2, b3 = b_ok(r, base_ret)
            n = sum([b1, b2, b3])
            if n == 2:
                near.append((n, r))
        near.sort(key=lambda x: -float(x[1].get('b3_total_return_pct', -999)))
        print(f'  {part}: B 全满足 = {len(satisfying)} / {len(rows)}; B 满足 2 条 = {len(near)}')
        if satisfying:
            print(f'    反例 trial: {satisfying[0][\"trial_id\"]} ret={satisfying[0][\"b3_total_return_pct\"]}%')
        if near and not satisfying:
            top = near[0][1]
            print(f'    最接近: {top[\"trial_id\"]} ret={top[\"b3_total_return_pct\"]}% '
                  f'b1={top[\"b1_trigger_total\"]} avg_age={top[\"b2_avg_session_bars\"]}')
"
```

记录输出. 这是 Part 4 判定的核心数据.

- [ ] **Step 2: 写 reports/tactical_proof_of_impossibility.md (Part 1 机制 ~1200 字)**

```markdown
# 战术化网格不可落地性证明

> **日期**: 2026-05-15
> **Spec**: `docs/superpowers/specs/2026-05-14-tactical-impossibility-proof-design.md`
> **数据**: `runtime/experiments/tactical_proof/{uvxy_4h, vxx_4h}/*.csv`

## TL;DR

[Step 1 结果直接填: "严证伪通过/失败. UVXY 4h: B 全满足 X 点 / N trial; VXX 4h: B 全满足 Y 点 / M trial. 战术化在合规边界 (UVXY/VXX × 4h × 5y × $10k cap) 内 (不能 / 能) 落地."]

## Part 1 — 机制 (理论)

### 1A. UVXY 专属冲突

V49 worktree 实证 (`README.md` §5 + `findings.md` F2):
- 平均 session 寿命 ~60 bars (~240 小时)
- 13 笔 EXIT_GRID 累计 +$500 (~46% 来自 ADX>22 高位 trend 末端退出)
- 5y +109.91% 主要 edge: 长 hold + grid_engine.should_exit 在 trend 末端 EXIT

战术化 4 个动作每个都与该 edge 结构性冲突:

| 战术动作 | 触发条件 | 与 V49 edge 的冲突 |
|---|---|---|
| ENTER_DEFENSIVE | trend_risk ≥ DEFENSIVE 阈值 / SOFT_STOP / age > MAX_AGE | 关闭 BUY → 杀掉 fade pullback 路径 (V49 利润来源之一) |
| FORCE_EXIT | trend_risk ≥ FORCE_EXIT / HARD_STOP / age > ABSOLUTE | 卖在 trend 中段而非高位 → 错过 grid_engine ADX>22 EXIT 的 +$500 |
| PROFIT_PROTECT_EXIT | peak_pnl 回吐 ≥ TRAILING_GIVEBACK_RATIO | 永远早卖, V49 long-hold 反弹收益归零 |
| PARTIAL_PROFIT_EXIT | total_pnl ≥ STRONG_PROFIT_PCT | 减仓后反弹空间砍半 |

### 1B. 战术化概念 (Symbol-agnostic) 必要条件

战术化要在任何标的上"短线收割"赢, 需同时满足:

- **(i) 短周期 ranging window 密度**: 标的存在 ≥ 数十段/年的可识别 ≤20 bars ranging 段
- **(ii) 等待型 edge**: ranging 间趋势期足够长 + 可识别, 让 DEFENSIVE/FORCE_EXIT 真的省下亏损
- **(iii) 成本占比基线低**: cost/毛利 占比起点必须低, 因为短线 → trades/session 线性放大
- **(iv) 成本敏感性低**: ±50% spread/commission 时, 战术化回报衰减 ≤ baseline 衰减; 否则是 fragile alpha

### 1C. UVXY / VXX 在 (i)-(iv) 上的属性

| 维度 | UVXY 4h | VXX 4h |
|---|---|---|
| ATR% 中位数 | [Step 3 填] | [Step 3 填] |
| ADX 长期均值 | [Step 3 填] | [Step 3 填] |
| ≤20bar ranging 段数 / 年 | [Step 3 填] | [Step 3 填] |
| 趋势期典型长度 (bar) | [Step 3 填] | [Step 3 填] |
| TURBO=OFF baseline 5y return | [Task 6 baseline 填] | [Task 6 baseline 填] |
| TURBO=OFF cost/毛利 % | [Step 3 填] | [Step 3 填] |
| 杠杆 → 长 hold edge 强度 | 强 (V49) | 弱 (1× 振幅小) |

## Part 2 — 单维 sweep 数据

[贴 single_dim.csv 摘要, 每个旋钮一行: knob, best_value, best_ret, b1 trigger, avg_session_bars]

## Part 3 — 联合 sweep + 成本敏感性

### 3A. 联合 sweep

[贴 joint.csv 摘要 + Pareto 前沿描述]

### 3B. 成本敏感性

| 配置 | slip=3 | slip=5 | slip=7.5 | slip=10 | 衰减率 |
|---|---|---|---|---|---|
| TURBO=ON | | | | | |
| TURBO=OFF | | | | | |

[填 cost.csv 数据]

## Part 4 — 联合判定

### UVXY 4h
- B 全满足点数: [填]
- 最接近点: [填]
- 结论: [严证伪通过/失败]

### VXX 4h
- B 全满足点数: [填]
- 最接近点: [填]
- 结论: [严证伪通过/失败]

### 联合
[按 spec §3 Part 4 的 4 种情形之一]

## Part 5 — 后续行动

[严证伪通过分支: Task 9 改 TURBO=False + EXPERIMENTAL banner + 4 个 regression test]
[严证伪失败分支: 反例参数交给用户决策, 不擅自合入默认]
```

- [ ] **Step 3: 填充 Part 1C 表格 (用脚本算 UVXY/VXX 的市场结构指标)**

```bash
python -c "
import pandas as pd
from indicators import compute_all_indicators

for sym, csv_path in [('UVXY', 'data/uvxy_4h.csv'), ('VXX', 'data/vxx_4h.csv')]:
    df = pd.read_csv(csv_path)
    # 列名: c h l n o t v vw → 转 OHLC 大写
    df = df.rename(columns={'o': 'Open', 'h': 'High', 'l': 'Low', 'c': 'Close', 'v': 'Volume'})
    df = compute_all_indicators(df, ema_period=20, atr_period=14, adx_period=14, bb_period=20)
    print(f'\n=== {sym} ===')
    print(f'  ATR% 中位数: {df[\"ATR_PCT\"].median()*100:.2f}%')
    print(f'  ADX 中位数:  {df[\"ADX\"].median():.2f}')
    print(f'  ADX > 22 占比: {(df[\"ADX\"] > 22).mean()*100:.1f}%')
    print(f'  样本数: {len(df)} bars (~{len(df)/(252*1.625):.1f} 年, 假设 1 天 1.625 个 4h bar)')
"
```

把数字填入 Part 1C 表格.

- [ ] **Step 4: 填充 Part 2/3/4 (从 Step 1 输出 + CSV 直接抓数据)**

手动看 4 份 CSV + Step 1 输出, 填入 report 的 Part 2-4. 必须包含:
- Part 2 每个旋钮的"该轴下最佳 trial" 一行
- Part 3A Pareto 前沿前 5 个 trial 列表
- Part 3B cost 表格 8 个数填
- Part 4 联合判定明确写"严证伪通过/失败"

- [ ] **Step 5: Commit**

```bash
git add reports/tactical_proof_of_impossibility.md
git commit -m "report: 战术化不可落地性证明完整报告

Part 1 (机制) + Part 2-3 (sweep 数据) + Part 4 (联合判定). UVXY 4h + VXX 4h
× 5y × \$10k cap, 共 ~400 trial. 结论: [严证伪通过 / 反例参数集]."
```

---

## Task 9: (条件) 严证伪通过 — TURBO=False + banner + regression test

**前置**: Task 8 Part 4 判定为"严证伪通过 (两个标的全 sweep 空间都无 B 全满足点)".

**Files (仅在严证伪通过时执行)**:
- Modify: `config.py` L99 TURBO_ENABLED 默认 `"0"`
- Modify: `tactical_config.py` 顶部加 banner
- Modify: `test.py` 新增 `TestTacticalActionsReachable`

- [ ] **Step 1: 判断分支**

阅读 `reports/tactical_proof_of_impossibility.md` Part 4 联合结论. 如果写"严证伪通过":
继续 Step 2. 如果"严证伪失败 (找到反例)": 跳过本 task, 直接 Task 10.

- [ ] **Step 2: Edit config.py L99 TURBO_ENABLED 默认 False**

```python
# 原:
TURBO_ENABLED: bool = _os.getenv("TURBO_ENABLED", "1").strip().lower() in (
    "1", "true", "yes", "y", "on"
)
# 改为:
# 默认 OFF — 2026-05-15 严证伪通过, 详见 reports/tactical_proof_of_impossibility.md
# 战术化代码全部保留 (EXPERIMENTAL); 实盘 / 回测默认走 V49 legacy 路径.
TURBO_ENABLED: bool = _os.getenv("TURBO_ENABLED", "0").strip().lower() in (
    "1", "true", "yes", "y", "on"
)
```

- [ ] **Step 3: tactical_config.py 顶部加 banner**

在 `import os` 之后 (即文件 docstring 之后, 第一段代码之前) 插入:

```python
# ════════════════════════════════════════════════════════════════════
#  ⚠️  EXPERIMENTAL — 2026-05-15 严证伪通过
#  ════════════════════════════════════════════════════════════════════
#  在 UVXY 4h + VXX 4h × 5y × $10k cap 边界内, 不存在战术化配置同时满足:
#    (B1) Defensive/Forced/Profit-protect 触发 > 0
#    (B2) 平均 session 寿命 ≤ 20 bars
#    (B3) 5y 回报 ≥ TURBO=OFF baseline
#
#  config.TURBO_ENABLED 默认 OFF. 本模块代码全部保留以备未来重新设计.
#  完整证据: reports/tactical_proof_of_impossibility.md
# ════════════════════════════════════════════════════════════════════
```

- [ ] **Step 4: test.py 加 TestTacticalActionsReachable**

在 test.py 末尾 (`if __name__ == "__main__":` 之前) 加:

```python
class TestTacticalActionsReachable(unittest.TestCase):
    """战术化 4 个 action 在受控合成 ctx 上必须可达 (中性 regression).

    目的: 防止战术化分支无声退化为 dead code. 不断言 'ON vs OFF' 优劣;
    断言每个 action 在合适的合成 MarketContext + 临时阈值下能被触发,
    即代码路径活着. 未来重启 / 重设计战术化时, 这套测试仍有意义.

    spec: docs/superpowers/specs/2026-05-14-tactical-impossibility-proof-design.md §4.4
    """

    def setUp(self):
        from datetime import datetime
        from interfaces import HistoricalClock
        from trade_logger import TradeDatabase
        from session_manager import SessionManager
        import tactical_config as tcfg

        self.tcfg = tcfg
        # 临时 DB
        import tempfile
        self.tmpdir = tempfile.TemporaryDirectory()
        db_path = f"{self.tmpdir.name}/t.db"
        self.db = TradeDatabase(db_path=db_path)
        self.clock = HistoricalClock(start=datetime(2026, 1, 1, 10, 0))
        self.sm = SessionManager(db=self.db, clock=self.clock)
        self.sm.start_session(
            symbol="TEST", start_equity=10000.0, start_cash=4000.0,
            start_position=200.0, start_price=30.0,
            confidence=tcfg.CONFIDENCE_NORMAL,
        )

    def tearDown(self):
        self.tmpdir.cleanup()

    def _patch(self, attr, value):
        """临时改 tactical_config 阈值, tearDown 恢复."""
        orig = getattr(self.tcfg, attr)
        setattr(self.tcfg, attr, value)
        self.addCleanup(setattr, self.tcfg, attr, orig)

    def test_defensive_triggers_on_strong_downtrend(self):
        from session_manager import ACTION_ENTER_DEFENSIVE
        from tactical_rules import MarketContext
        self._patch("TREND_RISK_SCORE_DEFENSIVE", 50.0)
        self._patch("TREND_RISK_ADX_THRESHOLD", 22.0)
        ctx = MarketContext(
            current_price=27.0, ema=30.0, atr=1.0, atr_pct=0.04,
            adx=40.0, grid_center=30.0,
            ema_slope=-0.02, consecutive_down_bars=5,
            price_below_ema_bars=5, atr_expansion=1.0,
        )
        ev = self.sm.evaluate_session(ctx)
        self.assertEqual(ev.action, ACTION_ENTER_DEFENSIVE,
                         f"reason={ev.reason} score={ev.trend_risk_score}")

    def test_force_exit_triggers_on_hard_stop(self):
        from session_manager import ACTION_FORCE_EXIT
        from tactical_rules import MarketContext
        self._patch("SESSION_HARD_STOP_PCT", 0.05)
        self.sm.update_session(
            current_equity=9000.0, current_cash=3500.0,  # 亏 10%
            current_position=200.0, current_price=27.5,
            realized_pnl_delta=-1000.0,
        )
        ctx = MarketContext(current_price=27.5, ema=30.0, atr=1.0,
                            atr_pct=0.04, adx=15.0, grid_center=30.0)
        ev = self.sm.evaluate_session(ctx)
        self.assertEqual(ev.action, ACTION_FORCE_EXIT,
                         f"reason={ev.reason}")

    def test_profit_protect_exit_on_trailing_giveback(self):
        from session_manager import ACTION_PROFIT_PROTECT_EXIT
        from tactical_rules import MarketContext
        self._patch("SESSION_MIN_PROFIT_TO_PROTECT_PCT", 0.01)
        self._patch("SESSION_TRAILING_GIVEBACK_RATIO", 0.5)
        # 先冲 peak +$300 (3%), 再回吐到 +$120
        self.sm.update_session(
            current_equity=10300.0, current_cash=4000.0,
            current_position=200.0, current_price=31.5,
            realized_pnl_delta=300.0,
        )
        self.sm.update_session(
            current_equity=10120.0, current_cash=4000.0,
            current_position=200.0, current_price=30.6,
            realized_pnl_delta=-180.0,
        )
        ctx = MarketContext(current_price=30.6, ema=30.0, atr=1.0,
                            atr_pct=0.03, adx=15.0, grid_center=30.0)
        ev = self.sm.evaluate_session(ctx)
        self.assertEqual(ev.action, ACTION_PROFIT_PROTECT_EXIT,
                         f"reason={ev.reason}")

    def test_partial_profit_exit_on_strong_profit(self):
        from session_manager import ACTION_PARTIAL_PROFIT_EXIT
        from tactical_rules import MarketContext
        self._patch("SESSION_STRONG_PROFIT_PCT", 0.03)
        self._patch("SESSION_STRONG_PROFIT_PARTIAL_EXIT_RATIO", 0.5)
        self._patch("SESSION_MIN_PROFIT_TO_PROTECT_PCT", 0.999)  # 屏蔽 trailing
        self._patch("SESSION_TRAILING_GIVEBACK_RATIO", 0.999)
        self.sm.update_session(
            current_equity=10500.0, current_cash=4000.0,
            current_position=200.0, current_price=32.5,
            realized_pnl_delta=500.0,
        )
        ctx = MarketContext(current_price=32.5, ema=30.0, atr=1.0,
                            atr_pct=0.03, adx=15.0, grid_center=30.0)
        ev = self.sm.evaluate_session(ctx)
        self.assertEqual(ev.action, ACTION_PARTIAL_PROFIT_EXIT,
                         f"reason={ev.reason}")
```

- [ ] **Step 5: 跑 test.py 全套**

```bash
python test.py 2>&1 | tail -10
```

预期: `Ran 252 tests in X.Xs` + `OK` (248 + 4 新).

如果新测试失败: 阅读失败的 reason / assertion 错误, 调整合成 MarketContext 让阈值通过. **不应**通过改 SessionManager 或 tactical_rules 让测试通过 — 这两者是被测对象.

- [ ] **Step 6: 跑一次 UVXY 4h backtest 看 TURBO 默认 OFF 后行为**

```bash
python backtest.py --csv data/uvxy_4h.csv --interval 4h --capital 10000 2>&1 | grep -E "总收益|TURBO|战术" | head -10
```

预期: 默认走 TURBO=OFF 路径 (战术 session 总数=0 或不打印战术段).

- [ ] **Step 7: Commit**

```bash
git add config.py tactical_config.py test.py
git commit -m "config: TURBO_ENABLED 默认 OFF + EXPERIMENTAL banner + 中性 regression test

严证伪通过 (reports/tactical_proof_of_impossibility.md). 战术化代码全部保留,
默认 OFF. test.py 新增 TestTacticalActionsReachable (4 个用例) 锁定战术化
4 个 action 代码可达, 防止未来无声退化为 dead code; 不锁定 ON/OFF 优劣.
"
```

---

## Task 10: README §5 + findings.md + progress.md 收尾

**Files:**
- Modify: `README.md` §5
- Modify: `findings.md`
- Modify: `progress.md`

- [ ] **Step 1: 拿当前 HEAD UVXY 4h 在 $10k 下的新基线数据**

```bash
TURBO_ENABLED=0 python backtest.py --csv data/uvxy_4h.csv --interval 4h --capital 10000 2>&1 | grep -E "总收益|年化|交易次数|最大回撤|Sharpe比率"
TURBO_ENABLED=1 python backtest.py --csv data/uvxy_4h.csv --interval 4h --capital 10000 2>&1 | grep -E "总收益|年化|交易次数|最大回撤|Sharpe比率"
```

记录数字.

(V49 worktree 跑 +109.91% 是 $2k 数据, $10k 下结果可能不同; 但 V49 worktree 重测
**不在本轮范围** — 维持 README §5 现有 V49 数据作为"历史参考", 仅更新 TURBO ON/OFF 行.)

- [ ] **Step 2: Edit README.md §5**

把 §5 "TURBO=ON, 当前 file defaults" 和 "TURBO=OFF, legacy fallback" 两行替换成
$10k 新数据. 在末尾加一行链接证明报告:

```markdown
- **战术化不可落地性证明** (2026-05-15): 严证伪通过 / 反例参数集 [按实际结论选一]
  — `reports/tactical_proof_of_impossibility.md`
```

- [ ] **Step 3: Edit findings.md 加证明结论段**

在 findings.md 末尾追加:

```markdown
---

## F7. 战术化不可落地性证明 (2026-05-15)

**严证伪结论**: [通过 / 失败]. UVXY 4h + VXX 4h × 5y × $10k cap × 总 ~400 trial,
[B 全满足 0 点 / 找到 N 个反例]. 详见 `reports/tactical_proof_of_impossibility.md`.

**主要数据**:
- UVXY 4h TURBO=OFF baseline: [填]%
- UVXY 4h 战术 sweep 最优: [填]% (距 baseline [+/-X] pp, B 满足 [N/3] 条)
- VXX 4h TURBO=OFF baseline: [填]%
- VXX 4h 战术 sweep 最优: [填]% (距 baseline [+/-X] pp, B 满足 [N/3] 条)
- 成本敏感性: TURBO=ON 在 spread+50% 时衰减 [X]pp, TURBO=OFF 衰减 [Y]pp; [是 / 否]验证 1B(iv) fragile

**后续动作**: [Task 9 已执行 → TURBO=OFF default + banner + 4 regression test /
反例参数集 = ..., 等用户决策合并入默认]
```

- [ ] **Step 4: Edit progress.md 追加会话日志**

```markdown
---

## 2026-05-15 会话 2 — 严证伪 implementation 完成

### 已完成
- F5: entry_filter.py 移除 datetime.now() fallback (Task 1)
- backtest.py 默认资金 $10k (Task 2)
- F4a: README §3.2 6 状态 (Task 3)
- data/vxx.py + vxx_4h.csv ([N] bars / [Y] 年) (Task 4)
- scripts/_proof_runner.py + smoke test (Task 5)
- scripts/prove_tactical.py + single_dim sweep × 2 标的 (Task 6)
- joint sweep + cost sensitivity × 2 标的 (Task 7)
- reports/tactical_proof_of_impossibility.md (Task 8)
- [if 严证伪通过] TURBO_ENABLED 默认 OFF + EXPERIMENTAL banner + 4 regression test (Task 9)
- F4b: README §5 + findings.md F7 + progress.md (Task 10, 本提交)

### 结论
[一段话总结严证伪结论 + 下一步建议]
```

- [ ] **Step 5: 跑一次完整 test.py 确认全绿**

```bash
python test.py 2>&1 | tail -3
```

预期: 全绿. 如果有失败, **不要 commit**, 回头查.

- [ ] **Step 6: Commit**

```bash
git add README.md findings.md progress.md
git commit -m "docs: README §5 + findings F7 + progress 终态

战术化不可落地性证明完整闭环. README §5 用 \$10k 新基线 + 链接 proof report;
findings.md F7 总结证明结论 + 关键数据; progress.md 加会话 2 日志.
test.py 全绿确认."
```

- [ ] **Step 7: 总结报告给用户**

输出文本:
```
✅ 战术化不可落地性证明 implementation 完成.

变更:
- F5 (entry_filter datetime.now() fallback): 已修
- F4a (README §3.2 4→6 状态): 已修
- F4b (README §5 + findings F7 + progress): 已更新
- BACKTEST_DEFAULT_CAPITAL 2000 → 10000
- data/vxx.py + vxx_4h.csv: 拉了 [N] bars / [Y] 年
- scripts/prove_tactical.py + scripts/_proof_runner.py: 多进程 sweep 驱动
- runtime/experiments/tactical_proof/: ~400 trial 全数据
- reports/tactical_proof_of_impossibility.md: 完整证明报告

证明结论:
[一行严证伪结论]

仅在严证伪通过时已做:
- config.TURBO_ENABLED 默认 OFF
- tactical_config.py EXPERIMENTAL banner
- test.py 新增 TestTacticalActionsReachable (4 用例)

未做 (诚实声明):
- CLAUDE.md 没动 (按用户要求)
- 战术化期间 legacy 路径 -29pp 回归未 git-bisect (与本命题正交)
- 反例参数集 (如有) 未擅自合入默认, 等用户决策
```

---

## Self-Review

**Spec coverage check** (对照 spec §2-9 各 section 是否都有 task 覆盖):

| Spec section | 覆盖 task | OK? |
|---|---|---|
| §2 命题 | Task 8 Part 4 判定 | ✓ |
| §3 Part 0 (数据 + capital) | Task 2, Task 4 | ✓ |
| §3 Part 1 (机制) | Task 8 Step 2-3 (Part 1A/1B/1C) | ✓ |
| §3 Part 2 (单维 sweep) | Task 6 | ✓ |
| §3 Part 2 子节 (成本敏感性) | Task 7 Step 3 | ✓ |
| §3 Part 3 (联合 sweep + 数据驱动选轴) | Task 6 (`_select_joint_axes_from_single_dim`) + Task 7 Step 1-2 | ✓ |
| §3 Part 4 (联合判定) | Task 8 Step 1 + Step 4 | ✓ |
| §3 Part 5 (失败处理条件分支) | Task 9 (条件) | ✓ |
| §4.1 F5 | Task 1 | ✓ |
| §4.2 F4a | Task 3 | ✓ |
| §4.3 F4b | Task 10 Step 2 | ✓ |
| §4.4 中性 regression test | Task 9 Step 4 | ✓ |
| §5 Deliverables | Task 4-10 全覆盖 | ✓ |
| §6 不在范围内 (诚实声明) | Task 10 Step 7 报告 | ✓ |
| §7 风险与缓解 | 内联在 Task 4 Step 4 (Alpaca 数据不全) / Task 5 Step 4 (字段缺失) / Task 6 Step 2 (pickle 错误) / Task 6 Step 3 (VXX 入场拒绝) | ✓ |
| §8 验收清单 10 项 | Task 1 (F5) / Task 2 (capital) / Task 4 (vxx CSV) / Task 5+6+7 (脚本+CSV) / Task 8 (report) / Task 9 (TURBO+banner+test) / Task 10 (README+findings) | ✓ |

**Placeholder scan**: 检查"TBD" / "TODO" / "implement later" / "fill in details" / "add appropriate error handling" — 无, 全部 step 都有具体代码 / 命令 / 预期输出.

**Type consistency check**:
- `SessionEvaluation.action` 在 Task 9 Step 4 用 4 个常量 (`ACTION_ENTER_DEFENSIVE`, `ACTION_FORCE_EXIT`, `ACTION_PROFIT_PROTECT_EXIT`, `ACTION_PARTIAL_PROFIT_EXIT`) — 与 `session_manager.py` 实际定义对齐 ✓
- `MarketContext` 字段在 Task 9 Step 4 引用 — 与 `tactical_rules.py:80-99` 定义对齐 ✓
- `run_one_trial(trial: dict)` 在 Task 5 / Task 6 一致 ✓
- `BACKTEST_DEFAULT_CAPITAL` 在 Task 2 / Task 6 一致 ✓
- stats 字段名 (`defensive_session_count` etc.) **未完全验证**, Task 5 Step 2 显式留了 grep 验证步骤, 如果实际字段名不同会在那一步修正 ✓ (可控)

**Scope check**: 单次 implementation, 10 task, 跨 ~4-5 小时. 不需要拆分.
