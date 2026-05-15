# 战术化扩展筛选 + 多标的回测验证 Plan

> **Mode**: inline executing-plans
> **Date**: 2026-05-15
> **Predecessor**: `docs/superpowers/plans/2026-05-15-tactical-impossibility-proof.md`

**Goal**: 把 Part 1B 筛选候选从 15 扩到 50 (含杠杆 ETF / 高波动单股 / option income ETF / VIX 系列), 在合格标的上拉 Alpaca 5y 数据 + 跑 TURBO=ON/OFF baseline 对比, 在 Top-3 score 标的上跑完整 sweep, 看战术化能否在新标的上找到反例.

**Architecture**: 复用 `scripts/screen_symbols.py` (Part 1B 规则已就位) + `scripts/_proof_runner.py` + `scripts/prove_tactical.py`. 新增 `data/multi_pull.py` 一次性拉多标的 Alpaca 数据 + `scripts/run_baseline_grid.py` 批量跑 baseline. 不改业务代码.

**Tech Stack**: Python ≥3.10, yfinance (screen), Alpaca v2 stocks/bars (5y data), SimulatedExecutor, multiprocessing.

---

## File Structure

**新建**:
- `data/multi_pull.py` — 批量 Alpaca 拉数, 接受 symbol list, 输出 `data/{sym_lower}_4h.csv`
- `scripts/run_baseline_grid.py` — 批量 backtest, 接受 symbol list, 输出 `runtime/experiments/tactical_extended/baseline.csv`
- `reports/tactical_extended_screening.md` — 完整 report
- 自动产生: `data/{sym}_4h.csv` × N, `runtime/experiments/tactical_extended/baseline.csv`, `runtime/experiments/tactical_proof/{top3_sym}_4h/{single_dim,joint,cost}.csv`

**修改**:
- `scripts/screen_symbols.py` — CANDIDATES 从 15 扩到 50
- `findings.md` — 加 F8 段
- `progress.md` — 加会话 3

---

## Task 1: 扩展 CANDIDATES 到 50

修改 `scripts/screen_symbols.py` CANDIDATES (原 15 → 50):

```python
CANDIDATES = [
    # 现有 15 (V1 baseline)
    "UVXY", "VXX", "SVXY", "TQQQ", "SQQQ", "SOXL", "SOXS",
    "SPXL", "SPXS", "TLT", "GLD", "USO", "UNG", "QQQ", "SPY",
    # VIX 补 (1)
    "VIXY",
    # 杠杆 ETF (10)
    "UPRO", "SPXU", "TNA", "TZA", "FAS", "FAZ", "DRN", "DRV", "NUGT", "DUST",
    # 高波动单股 (15)
    "TSLA", "NVDA", "AMD", "PLTR", "COIN", "MSTR", "MARA", "RIOT",
    "RIVN", "LCID", "AFRM", "SOFI", "DKNG", "HOOD", "GME",
    # Option income ETF (8)
    "JEPI", "JEPQ", "QYLD", "XYLD", "RYLD", "TLTW", "HEQT", "SVOL",
    # 商品/矿业 (1)
    "GDX",
]
```

---

## Task 2: 跑 extended screen

```bash
python scripts/screen_symbols.py 2>&1 | tee /tmp/screen_v2.log | tail -100
```

预期: ~10 min (yfinance 50 个标的). 记录 Part 1B 全过列表 (`PASSERS`).

---

## Task 3: 拉 PASSERS 的 Alpaca 5y 数据

新建 `data/multi_pull.py`, 复用 `data/vxx.py` pattern, 一次拉多个 symbol:

```python
"""data/multi_pull.py — 批量 Alpaca 4h × 5y 拉数."""
import os, sys, time
from datetime import datetime, timedelta
import pandas as pd, requests

# 凭证按 CLAUDE.md §10 保留
api_key = "PKQGZMEOZCJCMUWKH73FYVN4V3"
api_secret = "CQPYmKq49PaNpgLnrN5weWb3pPLMN35SEp7UpzGC43LR"

SYMBOLS = sys.argv[1:] if len(sys.argv) > 1 else []
if not SYMBOLS:
    print("usage: python data/multi_pull.py SYM1 SYM2 ...")
    sys.exit(1)

end_dt = datetime(2026, 5, 15)
start_dt = end_dt - timedelta(days=365 * 5)
url = "https://data.alpaca.markets/v2/stocks/bars"
headers = {"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": api_secret}

for sym in SYMBOLS:
    params = {"symbols": sym, "timeframe": "4Hour",
              "start": start_dt.strftime("%Y-%m-%d"),
              "end": end_dt.strftime("%Y-%m-%d"),
              "feed": "iex", "limit": 10000}
    all_bars, page_token = [], None
    while True:
        if page_token:
            params["page_token"] = page_token
        r = requests.get(url, headers=headers, params=params, timeout=30)
        if r.status_code != 200:
            print(f"  {sym}: HTTP {r.status_code}, skip")
            break
        d = r.json()
        bars = d.get("bars", {}).get(sym, [])
        all_bars.extend(bars)
        page_token = d.get("next_page_token")
        if not page_token:
            break
    if not all_bars:
        print(f"  {sym}: 0 bars, skip")
        continue
    df = pd.DataFrame(all_bars)
    expected = ["c", "h", "l", "n", "o", "t", "v", "vw"]
    df = df[[c for c in expected if c in df.columns]]
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       f"{sym.lower()}_4h.csv")
    df.to_csv(out, index=False)
    print(f"  {sym}: {len(df)} bars → {out}")
    time.sleep(0.3)  # rate limit
```

跑:
```bash
python data/multi_pull.py SYM1 SYM2 ... SYMN
```

预期: ~30 min (取决于 PASSERS 数量).

---

## Task 4: 批量 baseline backtest

新建 `scripts/run_baseline_grid.py`:

```python
"""scripts/run_baseline_grid.py — 批量跑 TURBO=ON vs OFF baseline."""
import os, sys, csv
from pathlib import Path
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from scripts._proof_runner import run_one_trial

SYMBOLS = sys.argv[1:]
INTERVAL = "4h"
CAPITAL = 10000.0
INTERVAL_HOURS = 4.0

out_dir = REPO / "runtime/experiments/tactical_extended"
out_dir.mkdir(parents=True, exist_ok=True)

results = []
for sym in SYMBOLS:
    csv_path = REPO / f"data/{sym.lower()}_4h.csv"
    if not csv_path.exists():
        print(f"  {sym}: csv 缺失, skip"); continue
    for turbo in (1, 0):
        trial = dict(
            trial_id=f"baseline_{sym}_TURBO{turbo}",
            symbol=sym, csv_path=str(csv_path),
            interval=INTERVAL, interval_hours=INTERVAL_HOURS,
            capital=CAPITAL,
            env_overrides={"TURBO_ENABLED": str(turbo)},
        )
        try:
            r = run_one_trial(trial)
            print(f"  {sym} TURBO={turbo}: ret={r['b3_total_return_pct']:+.2f}% "
                  f"sessions={r['session_count']} b1={r['b1_trigger_total']}")
            results.append(r)
        except Exception as e:
            print(f"  {sym} TURBO={turbo}: ERROR {type(e).__name__}: {e}")

# 输出 CSV
if results:
    keys = list(results[0].keys())
    with open(out_dir / "baseline.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        for r in results:
            if "env_overrides" in r and isinstance(r["env_overrides"], dict):
                import json; r["env_overrides"] = json.dumps(r["env_overrides"], sort_keys=True)
        w.writerows(results)
    print(f"\n落盘 {out_dir / 'baseline.csv'} ({len(results)} rows)")
```

跑:
```bash
python scripts/run_baseline_grid.py SYM1 SYM2 ...
```

预期: ~15-20 min (N × 2 × ~30s).

---

## Task 5: Top-3 完整 sweep

根据 baseline.csv + screen 输出, 选 score 最高的 Top-3 PASSERS (排除已知失败的 UVXY + VXX). 对每个 sym 跑:

```bash
python scripts/prove_tactical.py --csv data/{sym}_4h.csv --symbol {sym} \
    --interval 4h --capital 10000 --part single --workers 7
python scripts/prove_tactical.py --csv data/{sym}_4h.csv --symbol {sym} \
    --interval 4h --capital 10000 --part joint --workers 7
python scripts/prove_tactical.py --csv data/{sym}_4h.csv --symbol {sym} \
    --interval 4h --capital 10000 --part cost --workers 4
```

3 个 × 3 part. 预期 ~45-60 min total.

---

## Task 6: 写 report

`reports/tactical_extended_screening.md`. 含:
- 50 候选 screen 结果摘要 (Part 1B 通过比例)
- PASSERS 列表 + baseline.csv 对比表
- Top-3 sweep 结果 (是否找到反例 / 距 B 三条多远)
- 联合判定 (严证伪结论是否站住 / 反例参数集)

---

## Task 7: docs + commit

- `findings.md` 加 F8 段 (扩展筛选结论)
- `progress.md` 加会话 3 日志
- commit 全部新文件 + 修改

---

## Verification

每个 Task 完成后:
- Task 1: `grep -c '","' scripts/screen_symbols.py` (粗略数候选行数)
- Task 2: screen 输出 Part 1B 表
- Task 3: `ls data/*.csv | wc -l` (确认 N+15 个 csv)
- Task 4: `wc -l runtime/experiments/tactical_extended/baseline.csv` (~2N+1 行)
- Task 5: `ls runtime/experiments/tactical_proof/*/joint.csv | wc -l` (前 2 个 + Top-3 = 5)
- Task 6: report 行数 ≥ 100
- Task 7: `git log --oneline -3` 看新 commit

## Risks

- Alpaca free tier IEX 数据: 部分新上市单股 (RIVN 2021-11, LCID 2021-07, HOOD 2021-07 等) 拿不满 5y; 接受较短窗口 + report 标注
- yfinance rate limit: screen 拉 50 个时可能 429; 失败标的 skip
- 杠杆 ETF / option income ETF 较新, 数据可能不全
- Top-3 选择规则: 按 screen score (现有公式) 排序, 不重新设计

## 边界 (诚实声明)

- 不证明"战术化在跑了的 50 个标的之外也不行"
- single option contract 不在范围 (无 spot 价格连续性)
- 数据窗口因标的而异 (新股 < 5y)
