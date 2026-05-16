# Audit + Active Tuning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Phase A 审计 (Q1 无硬编码幻觉 + Q2 当前 MDD + Q4 refactor 仍在), Phase B 用扩展 grid + 新 score formula tune VXX 4h 寻找更活跃 (≥1.5× trades) 参数, 通过 Gate 5 项则应用。

**Architecture:** Phase A 仅审计 (grep + fingerprint + 4 cell backtest + multi 复现 + test.py 全绿验证) → `reports/audit_2026-05-16.md`. Phase B 修 `scripts/tune.py` 加 `EXTENDED_GRID` (2000 combos) + `--active-score` (公式含 trades 因子) + 捕获 `total_trades` 字段, 跑 VXX 4h, 应用 Gate 5 项 (ann/trades/MDD/WF/cross-sym).

**Tech Stack:** Python 3.10+, pandas, 现有 `scripts/tune.py` / `backtest.py` / `run_multi_backtest.py`, 不引入新依赖。

**Spec:** `docs/superpowers/specs/2026-05-16-audit-and-active-tuning-design.md`

---

## File Structure

**新建**:
- `reports/audit_2026-05-16.md` (Phase A 单页报告)
- `reports/active_tuning_2026-05-16.md` (Phase B 完整报告)
- `runtime/experiments/4h_active/` (tune.py extended 输出目录)

**修改 (Phase B)**:
- `scripts/tune.py`: 加 `EXTENDED_GRID` 常量 + `--extended` CLI flag + `--active-score` CLI flag + `_worker_run` 捕获 `total_trades` + 新 `active_score()` 函数

**条件修改 (Phase B Gate PASS 时)**:
- `config.py` (替换 5 个 tuned param 默认值)
- `README.md` (§5 winner update)
- `CHANGELOG.md` (追加 entry)

---

## Task 1: Phase A — Q1/Q2/Q4 审计执行

**Files:**
- 无 code change (仅读 + 记录)
- 临时 fingerprint 记录文件: `/tmp/phase_a_audit_data.txt`

### Step 1: Q1 — Grep 业务代码硬编码收益数

Run:
```bash
grep -rnE "26\.78|226\.79|153\.93|82\.81|268\.41|201\.69|30169|25393" \
    --include="*.py" /Users/krisjiang/Desktop/grid/ \
    | grep -vE "archive/|/docs/|/reports/|/tests/|__pycache__|/data/" \
    | tee /tmp/q1_grep.txt
```

**预期**: 0 hits 在业务路径 (`grid_bot.py`, `risk_manager.py`, `backtest.py`, `simulated_executor.py`, `state_machine.py`, `entry_filter.py`, `bot_factory.py`, `orchestrator.py`, `main.py`)。

允许 hits: `test.py`, `scripts/*.py` (注释或 baseline 参考), `data/*.py` (puller 脚本日期默认)。

如果业务路径有 hit, 记入 `/tmp/q1_grep.txt` + 在 Step 2 后修复。

### Step 2: Q1 — 数据真实性 fingerprint

Run:
```bash
echo "=== Data files md5sum ===" > /tmp/phase_a_audit_data.txt
md5sum /Users/krisjiang/Desktop/grid/data/vxx_*.csv /Users/krisjiang/Desktop/grid/data/uvxy_*.csv >> /tmp/phase_a_audit_data.txt

echo "" >> /tmp/phase_a_audit_data.txt
echo "=== Data files wc -l ===" >> /tmp/phase_a_audit_data.txt
wc -l /Users/krisjiang/Desktop/grid/data/vxx_*.csv /Users/krisjiang/Desktop/grid/data/uvxy_*.csv >> /tmp/phase_a_audit_data.txt

echo "" >> /tmp/phase_a_audit_data.txt
echo "=== Data files header + first/last bar ===" >> /tmp/phase_a_audit_data.txt
for f in /Users/krisjiang/Desktop/grid/data/vxx_4h.csv /Users/krisjiang/Desktop/grid/data/vxx_1h.csv /Users/krisjiang/Desktop/grid/data/vxx_1d.csv; do
  echo "--- $f ---" >> /tmp/phase_a_audit_data.txt
  head -2 "$f" | tail -1 >> /tmp/phase_a_audit_data.txt
  tail -1 "$f" >> /tmp/phase_a_audit_data.txt
done

cat /tmp/phase_a_audit_data.txt
```

**预期**: VXX 4h 列结构 `c,h,l,n,o,t,v,vw`, 起 `2021-05-17T12:00:00Z`, 终 `2026-05-14T16:00:00Z`, 3066 行。VXX 1h 9109 行, VXX 1d 1256 行。

### Step 3: Q1 — Fresh backtest 复现 +226.79%

Run two times to check bit-identical:
```bash
echo "=== Run 1 ===" >> /tmp/phase_a_audit_data.txt
python /Users/krisjiang/Desktop/grid/backtest.py \
    --csv data/vxx_4h.csv --interval 4h --capital 10000 2>&1 \
    | grep -E "总收益率|年化收益率|最大回撤|Sharpe|交易次数" >> /tmp/phase_a_audit_data.txt

echo "=== Run 2 ===" >> /tmp/phase_a_audit_data.txt
python /Users/krisjiang/Desktop/grid/backtest.py \
    --csv data/vxx_4h.csv --interval 4h --capital 10000 2>&1 \
    | grep -E "总收益率|年化收益率|最大回撤|Sharpe|交易次数" >> /tmp/phase_a_audit_data.txt

tail -20 /tmp/phase_a_audit_data.txt
```

**预期**: 两次输出完全一致, ret=+226.79%, 年化=+26.78%, MDD=15.17%, Sharpe=0.321, trades=279。

### Step 4: Q2 — 4-cell baseline MDD 表

Run:
```bash
echo "" >> /tmp/phase_a_audit_data.txt
echo "=== Q2: Baseline MDD 4 cells ===" >> /tmp/phase_a_audit_data.txt

for combo in "vxx_4h.csv:4h" "uvxy_4h.csv:4h" "vxx_1d.csv:1d"; do
  csv=$(echo $combo | cut -d: -f1)
  iv=$(echo $combo | cut -d: -f2)
  echo "--- $csv $iv ---" >> /tmp/phase_a_audit_data.txt
  python /Users/krisjiang/Desktop/grid/backtest.py \
      --csv data/$csv --interval $iv --capital 10000 2>&1 \
      | grep -E "总收益率|年化收益率|最大回撤|Sharpe" >> /tmp/phase_a_audit_data.txt
done

echo "--- Multi UVXY+VXX 4h ---" >> /tmp/phase_a_audit_data.txt
python /Users/krisjiang/Desktop/grid/scripts/run_multi_backtest.py \
    --symbols UVXY VXX --csv data/uvxy_4h.csv data/vxx_4h.csv \
    --allocations 0.5 0.5 --capital 10000 --interval 4h \
    --label audit_multi_4h 2>&1 | grep -E "总回报|年化|最大回撤" >> /tmp/phase_a_audit_data.txt

tail -30 /tmp/phase_a_audit_data.txt
```

**预期**:
- VXX 4h: MDD 15.17%
- UVXY 4h: MDD 15.86%
- VXX 1d: MDD 17.35%
- Multi 4h: MDD 15.09%

### Step 5: Q4 — 战术化主路径无 import

Run:
```bash
echo "" >> /tmp/phase_a_audit_data.txt
echo "=== Q4.1: 战术化主路径 import 检查 ===" >> /tmp/phase_a_audit_data.txt
grep -rn "import tactical\|import session_manager\|from tactical\|from session_manager" \
    --include="*.py" /Users/krisjiang/Desktop/grid/ \
    | grep -vE "archive/|__pycache__|/tests/|test\.py" \
    | tee -a /tmp/phase_a_audit_data.txt
```

**预期**: 0 hits.

```bash
echo "" >> /tmp/phase_a_audit_data.txt
echo "=== Q4.2: archive/tactical/ 目录结构 ===" >> /tmp/phase_a_audit_data.txt
ls /Users/krisjiang/Desktop/grid/archive/tactical/ >> /tmp/phase_a_audit_data.txt
ls /Users/krisjiang/Desktop/grid/archive/tactical/scripts/ >> /tmp/phase_a_audit_data.txt
ls /Users/krisjiang/Desktop/grid/archive/tactical/reports/ >> /tmp/phase_a_audit_data.txt
```

**预期**:
- archive/tactical/: README.md, USAGE_BEFORE_ISOLATION.md, CLEANUP_LOG.md, session_manager.py, tactical_config.py, tactical_rules.py, scripts/, reports/, tests/, runtime/
- scripts/: _proof_runner.py, prove_tactical.py, tune_tactical.py
- reports/: tactical_proof_of_impossibility.md, tactical_extended_screening.md

### Step 6: Q4 — Multi-symbol 复现 + test.py + commits

```bash
echo "" >> /tmp/phase_a_audit_data.txt
echo "=== Q4.3: Multi-symbol +153.93% 复现 ===" >> /tmp/phase_a_audit_data.txt
python /Users/krisjiang/Desktop/grid/scripts/run_multi_backtest.py \
    --symbols UVXY VXX --csv data/uvxy_4h.csv data/vxx_4h.csv \
    --allocations 0.5 0.5 --capital 10000 --interval 4h \
    --label audit_multi_check 2>&1 | grep -E "总回报|UVXY|VXX" >> /tmp/phase_a_audit_data.txt

echo "" >> /tmp/phase_a_audit_data.txt
echo "=== Q4.4: test.py 全绿 ===" >> /tmp/phase_a_audit_data.txt
python /Users/krisjiang/Desktop/grid/test.py 2>&1 | tail -5 >> /tmp/phase_a_audit_data.txt

echo "" >> /tmp/phase_a_audit_data.txt
echo "=== Q4.5: Refactor commits in main ===" >> /tmp/phase_a_audit_data.txt
cd /Users/krisjiang/Desktop/grid
git log --oneline | grep -iE "refactor|tactical|archive|tune|phase|symbol" | head -25 >> /tmp/phase_a_audit_data.txt

tail -50 /tmp/phase_a_audit_data.txt
```

**预期**:
- Multi: 总回报 +153.93%, UVXY sub +76.46%, VXX sub +237.48%
- test.py: `Ran 252 tests / OK (skipped=70)` 0 fails / 0 errors
- Git log: 看到 12 个 refactor commits (6402ed7-2833ca5) + 4 个 fix commits + tuning commits

### Step 7: 无 commit (Task 1 仅数据收集, Task 2 写报告时 commit)

---

## Task 2: Phase A — 写 audit 报告 + commit

**Files:**
- Create: `reports/audit_2026-05-16.md`

### Step 1: 读 Task 1 收集的数据

Run:
```bash
cat /tmp/q1_grep.txt
cat /tmp/phase_a_audit_data.txt
```

### Step 2: 写 audit 报告

Use Write tool to create `/Users/krisjiang/Desktop/grid/reports/audit_2026-05-16.md`. 

模板 (用 Task 1 实测填充):

```markdown
# Audit Report (2026-05-16) — Q1 / Q2 / Q4 Verification

> Spec: docs/superpowers/specs/2026-05-16-audit-and-active-tuning-design.md
> Phase A of audit + active tuning combined spec.

## Q1: 当前年化 26% 是否伪代码/硬编码幻觉?

### Q1.1: 业务代码硬编码 grep
[paste /tmp/q1_grep.txt 完整内容. 如为空, 写 "0 hits in business code (PASS)".]

业务路径范围: `grid_bot.py`, `risk_manager.py`, `backtest.py`, `simulated_executor.py`, `state_machine.py`, `entry_filter.py`, `bot_factory.py`, `orchestrator.py`, `main.py`.

**结论**: [PASS / FAIL with file:line refs]

### Q1.2: 数据真实性 fingerprint
[paste md5sum 输出]

[paste wc -l 输出]

[paste first/last bar 输出]

**结论**: 数据列结构 (`c,h,l,n,o,t,v,vw`) 与 Alpaca v2 IEX 一致. 时间范围真实 5y. 行数合理.

### Q1.3: Fresh backtest 复现 +226.79%
[paste Run 1 + Run 2 输出]

**结论**: 两次跑 bit-identical, 收益数 ret=[实测]%, 年化=[实测]% 来自实跑代码 + 实数据, 非硬编码或缓存。

### Q1 总结

[PASS / FAIL with breakdown]

## Q2: 当前最大回撤

| Symbol | Interval | Ret | 年化 | Sharpe | MDD |
|---|---|---|---|---|---|
| VXX | 4h | [实测]% | [实测]% | [实测] | [实测]% |
| UVXY | 4h | [实测]% | [实测]% | [实测] | [实测]% |
| VXX | 1d | [实测]% | [实测]% | [实测] | [实测]% |
| Multi 50/50 | 4h | [实测]% | [实测]% | N/A | [实测]% |

**当前 winner (VXX 4h) MDD**: [实测]%
**多标的 (Multi 4h) MDD**: [实测]%

## Q4: Refactor 仍在?

### Q4.1: 战术化主路径 import
[paste grep 输出 — 应为空]

**结论**: 0 hits in 主路径 (PASS) / [N hits — 见详情] (FAIL)

### Q4.2: archive/tactical/ 完整
[paste ls 输出]

**结论**: 模块 + 工具 + reports + README 全部到位 (PASS)

### Q4.3: Multi-symbol +153.93% 复现
[paste Multi backtest 输出]

**结论**: bit-identical with D5 baseline (PASS) / 偏差 [pp] (FAIL)

### Q4.4: test.py 全绿
[paste test.py 末尾输出]

**结论**: [Ran X tests / OK (skipped=N)] — 0 fails, 0 errors (PASS)

### Q4.5: Refactor commits in main
[paste git log 输出]

**结论**: 12 production refactor commits + symbol-interval tuning commits 全部存在 (PASS)

### Q4 总结

[PASS — 4/4 子检查通过 / FAIL — 详见上文]

## 总结

| Question | Status |
|---|---|
| Q1 (无硬编码幻觉) | [PASS / FAIL] |
| Q2 (当前 MDD 已列) | [DONE] |
| Q4 (refactor 仍在) | [PASS / FAIL] |

[如果全 PASS: "所有验证通过, 现有系统可信."]
[如果有 FAIL: 列出 issue + 推荐修复.]
```

填实测数据 — 无 placeholder。

### Step 3: Commit

```bash
cd /Users/krisjiang/Desktop/grid
git add reports/audit_2026-05-16.md
git commit -m "$(cat <<'EOF'
docs(audit): Phase A audit report (Q1/Q2/Q4 verification)

Q1 (无硬编码幻觉): 业务代码 grep 0 hits + 数据 fingerprint + fresh backtest 复现 +226.79% bit-identical
Q2 (当前 MDD): VXX 4h 15.17% / UVXY 4h 15.86% / VXX 1d 17.35% / Multi 4h 15.09%
Q4 (refactor 仍在): 战术化 archive 隔离 + multi +153.93% 复现 + test.py 全绿 + commits 在 main

整体: 全 PASS / Issues found (见报告)
EOF
)"
```

(根据实测调 commit message — 如有 FAIL 改为 "Issues found")

---

## Task 3: Phase B — 修 scripts/tune.py 加 EXTENDED_GRID + active-score

**Files:**
- Modify: `scripts/tune.py`

### Step 1: 读 scripts/tune.py 看当前结构

Use Read tool to inspect:
- Lines 38-100 (GRIDs + score + worker base)
- Lines 110-170 (_worker_run 输出)
- Lines 380-450 (main + CLI args)

记下关键 line numbers:
- `FULL_GRID` 字典 (~L48)
- `score()` 函数 (~L89)
- `_worker_run` (~L111) → 看返回 dict 的字段
- `main()` 的 argparse (~L380)

### Step 2: 加 EXTENDED_GRID 常量

Edit `scripts/tune.py`. 在 `QUICK_GRID = {...}` 之后, `BASELINE = {...}` 之前插入:

```python
# 2026-05-16: 扩展 grid for Phase B active tuning (更激进 value).
# 总 combos = 5 × 4 × 5 × 4 × 5 = 2000.
EXTENDED_GRID = {
    "ENTRY_MAX_ADX":                [15, 20, 25, 30, 40],
    "ENTRY_MAX_ATR_PCT":            [0.035, 0.045, 0.055, 0.07],
    "GRID_SPACING_ATR_MULTIPLIER":  [0.25, 0.30, 0.40, 0.50, 0.60],
    "GRID_RECENTER_THRESHOLD_ATR":  [0.6, 0.8, 1.0, 1.2],
    "EXIT_MAX_ADX":                 [20, 22, 25, 28, 35],
}
```

### Step 3: 加 active_score() 函数

Edit `scripts/tune.py`. 在 `score()` 之后插入:

```python
def active_score(row: dict) -> float:
    """Active strategy 评分: annualized × (trades / 100) × wf_pass_rate.

    2026-05-16 Phase B: 接受 Sharpe drop, 优先 trade 频率 + WF 稳健 + 收益.
    单 cell grid search 时 wf_pass_rate 用 1.0 占位 (walk-forward 阶段才有实际值).
    """
    ann = row.get("annualized_return_pct", 0)
    trades = row.get("total_trades", 0)
    wf_pass = row.get("wf_pass_rate", 1.0)  # default 1.0 在 grid search 阶段
    return float(ann) * (float(trades) / 100.0) * float(wf_pass)
```

### Step 4: 改 _worker_run 捕获 total_trades

Find `_worker_run` (~L111). Look for the line that does `out = {...}` building the result dict. It currently has fields like `total_return_pct`, `annualized_return_pct`, `sharpe`, `max_drawdown_pct`, `sessions`.

Add `total_trades`:

```python
# 在 out = {...} 中加 (具体位置看实际代码):
"total_trades": int(stats.total_trades),
```

如果 _worker_run 已有 `sessions: int(stats.total_grid_sessions)`, 在它旁边加 `total_trades`:

```python
"sessions":             int(stats.total_grid_sessions),
"total_trades":         int(stats.total_trades),
```

### Step 5: 改 grid_search 用 active_score (if flagged)

Find `grid_search` function (~L193). Look for where `out["score"] = score(out)` is set (~L160 in _worker_run). 

实际上 score 已经在 `_worker_run` 内 set, 所以改逻辑是: 让 _worker_run 接受 score 选择, 或在 grid_search 主进程根据 flag 重算 score.

简单做法 (主进程重算):

在 `grid_search` 函数末尾 (在 `df_out.sort_values("score", ...)` 之前), 加 active_score 重算逻辑。但更简单是把 score 选择推到 _worker_run.

实际方案: 把 use_active 作为 _worker_init 的参数, 然后 _worker_run 根据它选择 score 函数.

修改 `_worker_init`:
```python
_WORKER_DF = None
_WORKER_INTERVAL = None
_WORKER_USE_ACTIVE = False  # 新加


def _worker_init(csv_path: str, interval: str, use_active: bool = False):
    global _WORKER_DF, _WORKER_INTERVAL, _WORKER_USE_ACTIVE
    _WORKER_DF = load_market_data("UVXY", interval, 9999, csv_path=csv_path)
    _WORKER_INTERVAL = interval
    _WORKER_USE_ACTIVE = use_active
```

修改 `_worker_run` 末尾:
```python
# 原: out["score"] = score(out)
# 改:
if _WORKER_USE_ACTIVE:
    out["score"] = active_score(out)
else:
    out["score"] = score(out)
```

修改 `run_parallel` 调用 (~L169):

```python
# 原: with Pool(workers, initializer=_worker_init, initargs=(csv_path, interval)) as pool:
# 改: 加 use_active 参数透传

def run_parallel(tasks: list, csv_path: str, interval: str,
                 workers: int = 0, label: str = "", use_active: bool = False) -> list[dict]:
    ...
    with Pool(workers, initializer=_worker_init,
              initargs=(csv_path, interval, use_active)) as pool:
        ...
```

确保所有 `run_parallel(...)` 调用点 (grep "run_parallel(") 都加 `use_active` 参数 (默认 False, 仅 grid_search 阶段从主进程的 args.active_score 透传)。

### Step 6: 加 CLI flags

Find `main()` argparse 区 (~L380). Add 2 new flags:

```python
parser.add_argument("--extended", action="store_true",
                    help="用 EXTENDED_GRID (2000 combos) 替代 FULL_GRID")
parser.add_argument("--active-score", action="store_true",
                    help="用 active_score (ann × trades × wf) 替代默认 score")
```

### Step 7: main() 根据 flag 选 grid + score

Find space 选择代码 (~L405-410):
```python
# 原 (大约):
if args.quick:
    space = QUICK_GRID
elif args.interval == "1d":
    space = GRID_1D
else:
    space = FULL_GRID
```

改为:
```python
if args.quick:
    space = QUICK_GRID
elif args.extended:
    space = EXTENDED_GRID
elif args.interval == "1d":
    space = GRID_1D
else:
    space = FULL_GRID
```

Find run_parallel call point (the one for grid_search, ~L420). 加 use_active 透传:

```python
# 找到形如:
# results = grid_search(csv_path, interval, space, args.workers, exp_dir)
# 改为 (假设 grid_search 内部调 run_parallel, 修 grid_search signature 接 use_active):
results = grid_search(csv_path, interval, space, args.workers, exp_dir,
                      use_active=args.active_score)
```

修 `grid_search` signature:
```python
def grid_search(csv_path: str, interval: str, space: dict,
                workers: int, out_dir: Path,
                use_active: bool = False) -> pd.DataFrame:
    ...
    # 内部调 run_parallel(... use_active=use_active)
```

walk_forward / three_split_oos / stability 阶段在 active-score 模式下也应用 active_score。但为了 minimal, 现阶段仅 grid_search 应用 active score (其他阶段仍用老 score 做 selection)。这是 trade-off — 接受。

### Step 8: 跑 smoke test

Run:
```bash
cd /Users/krisjiang/Desktop/grid
python scripts/tune.py --csv data/vxx_4h.csv --interval 4h --workers 6 \
    --quick --active-score 2>&1 | tail -20
```

**预期**: tune.py 跑通 QUICK_GRID (6 combos) 用 active_score 排序, 输出 chosen_params. 不应崩溃.

如果有 TypeError 或 AttributeError: 看具体, fix.

### Step 9: 单元测试 sanity (现有 test.py 不回归)

```bash
python /Users/krisjiang/Desktop/grid/test.py 2>&1 | tail -5
```

**预期**: 252 tests / OK (skipped=70), 0 fails / 0 errors. 与 audit 状态一致.

### Step 10: Commit

```bash
cd /Users/krisjiang/Desktop/grid
git add scripts/tune.py
git commit -m "$(cat <<'EOF'
feat(tune): EXTENDED_GRID (2000 combos) + active-score (Phase B prep)

新增 CLI flags:
- --extended: 用 EXTENDED_GRID 替代 FULL_GRID (5×4×5×4×5 = 2000 combos)
  加更激进 value: ENTRY_MAX_ADX 30/40, GRID_SPACING 0.25/0.30, ENTRY_ATR 0.07
- --active-score: 用 active_score (ann × trades/100 × wf_pass_rate) 替代默认 score
  适配"更活跃交易系统"目标 (接受 Sharpe drop, 优先 trade 频率)

修 _worker_run 捕获 total_trades 字段 (原仅 sessions).
修 _worker_init / run_parallel / grid_search signature 透传 use_active.

不改默认 score / 默认 grid — 老 tune.py 行为 backwards-compatible.

Smoke: --quick --active-score 跑通 (QUICK_GRID 6 combos).
test.py: 0 fails / 0 errors (无回归).
EOF
)"
```

---

## Task 4: Phase B — 跑 EXTENDED_GRID active tune

**Files:**
- 无 code change
- Output: `runtime/experiments/4h_active/` (tune.py 内部生成)

### Step 1: 准备输出目录

注意: `scripts/tune.py` 输出到 `runtime/experiments/<interval>/`. 上一轮 VXX 4h tune 在 `runtime/experiments/4h/`. 为避免冲突, 这次跑要么:
- (a) mv 旧的: `mv runtime/experiments/4h runtime/experiments/4h_passive_legacy 2>/dev/null || true`
- (b) 改 tune.py 输出路径用 `--label` flag (但 tune.py 目前没这 flag)

选 (a) — 最简单.

```bash
mv /Users/krisjiang/Desktop/grid/runtime/experiments/4h /Users/krisjiang/Desktop/grid/runtime/experiments/4h_passive_legacy 2>/dev/null || true
ls /Users/krisjiang/Desktop/grid/runtime/experiments/ | head -10
```

### Step 2: 跑 extended tune

```bash
cd /Users/krisjiang/Desktop/grid
python scripts/tune.py --csv data/vxx_4h.csv --interval 4h --workers 6 \
    --extended --active-score 2>&1 | tee /tmp/tune_active.log
```

**预期时间**: ~35-45 min (2000 combos × ~1.5s + walk-forward top-8 × 8 windows + OOS + stability).

如果超时: 等. 如果 fail: 看 stderr.

### Step 3: 改名输出目录

```bash
mv /Users/krisjiang/Desktop/grid/runtime/experiments/4h /Users/krisjiang/Desktop/grid/runtime/experiments/4h_active
ls /Users/krisjiang/Desktop/grid/runtime/experiments/4h_active/
```

**预期**: 5 个 csv (search_log, best_by_metric, walkforward, oos_report, stability) + 1 FINAL.json.

### Step 4: 读 FINAL.json

```bash
python -m json.tool /Users/krisjiang/Desktop/grid/runtime/experiments/4h_active/FINAL.json | head -50
```

记录:
- `chosen_params` (5 个 param 值)
- `comparison.chosen` metrics (5y full ret/ann/sharpe/MDD/trades)
- `walkforward_summary` (mean_valid_sharpe, mean_valid_return_pct, windows)

### Step 5: 读 search_log top 5 active_score

```bash
python -c "
import csv
rows = list(csv.DictReader(open('/Users/krisjiang/Desktop/grid/runtime/experiments/4h_active/search_log.csv')))
rows = [r for r in rows if r.get('score', '')]
rows.sort(key=lambda r: float(r['score']), reverse=True)
print('Top 5 by active_score:')
for r in rows[:5]:
    print(f\"  score={r['score']} ret={r.get('total_return_pct', '?')}% sharpe={r.get('sharpe', '?')} trades={r.get('total_trades', '?')}\")
"
```

记录 top 3-5 candidates.

### Step 6: Commit artifacts

```bash
cd /Users/krisjiang/Desktop/grid
git add runtime/experiments/4h_active/FINAL.json \
    runtime/experiments/4h_active/search_log.csv \
    runtime/experiments/4h_active/walkforward.csv \
    runtime/experiments/4h_active/oos_report.csv \
    runtime/experiments/4h_active/stability.csv \
    runtime/experiments/4h_active/best_by_metric.csv 2>&1
git commit -m "$(cat <<'EOF'
data(phase_b): tune.py extended + active-score on VXX 4h

EXTENDED_GRID 2000 combos × VXX 4h × active_score:
- chosen_params: [实测填]
- chosen 5y ret: [实测]% / ann [实测]% / Sharpe [实测] / MDD [实测]% / trades [实测]
- walkforward: mean_valid_sharpe [实测], pass [N]/8

旧 FULL_GRID 输出 mv 到 4h_passive_legacy/.
EOF
)"
```

(填实测值)

---

## Task 5: Phase B — Cross-symbol sanity (UVXY with tuned params)

**Files:**
- Create: `runtime/experiments/4h_active/cross_symbol.csv`

### Step 1: 读 chosen_params

```bash
python -c "
import json
final = json.load(open('/Users/krisjiang/Desktop/grid/runtime/experiments/4h_active/FINAL.json'))
chosen = final['chosen_params']
print('export commands (paste into next step):')
for k, v in chosen.items():
    print(f'  {k}={v}')
"
```

记录 5 个 param 值. 如 chosen == baseline (fallback 触发): 跳到 Step 4 直接落 baseline 等价值, 不重跑 UVXY (baseline UVXY 4h = +82.81% 已知).

### Step 2: Env override 跑 UVXY 4h backtest

(如 chosen != baseline)

```bash
cd /Users/krisjiang/Desktop/grid
ENTRY_MAX_ADX=<chosen.ENTRY_MAX_ADX> \
ENTRY_MAX_ATR_PCT=<chosen.ENTRY_MAX_ATR_PCT> \
GRID_SPACING_ATR_MULTIPLIER=<chosen.GRID_SPACING_ATR_MULTIPLIER> \
GRID_RECENTER_THRESHOLD_ATR=<chosen.GRID_RECENTER_THRESHOLD_ATR> \
EXIT_MAX_ADX=<chosen.EXIT_MAX_ADX> \
python backtest.py --csv data/uvxy_4h.csv --interval 4h --capital 10000 2>&1 \
    | grep -E "总收益率|年化|最大回撤|交易次数" | tee /tmp/uvxy_tuned.txt
```

**重要**: config.py 是否读 ENV override? 检查:
```bash
grep -E "ENTRY_MAX_ADX|ENTRY_MAX_ATR_PCT|GRID_SPACING_ATR_MULTIPLIER|GRID_RECENTER_THRESHOLD_ATR|EXIT_MAX_ADX" /Users/krisjiang/Desktop/grid/config.py | grep -i "getenv\|os.environ"
```

如果只有 `ENTRY_MAX_ADX_SLOPE` 用 getenv, 其他都 hardcoded — 那 env override 不生效, 需要 inline patch:

写 `/tmp/uvxy_tuned.py`:
```python
import sys
sys.path.insert(0, "/Users/krisjiang/Desktop/grid")
import config

# Inject chosen params
config.ENTRY_MAX_ADX = <chosen.ENTRY_MAX_ADX>
config.ENTRY_MAX_ATR_PCT = <chosen.ENTRY_MAX_ATR_PCT>
config.GRID_SPACING_ATR_MULTIPLIER = <chosen.GRID_SPACING_ATR_MULTIPLIER>
config.GRID_RECENTER_THRESHOLD_ATR = <chosen.GRID_RECENTER_THRESHOLD_ATR>
config.EXIT_MAX_ADX = <chosen.EXIT_MAX_ADX>

import backtest
import sys
sys.argv = ["backtest.py", "--csv", "data/uvxy_4h.csv", "--interval", "4h", "--capital", "10000"]
backtest.main()
```

```bash
python /tmp/uvxy_tuned.py 2>&1 | grep -E "总收益率|年化|最大回撤|交易次数" | tee /tmp/uvxy_tuned.txt
```

### Step 3: 计算 cross-symbol diff

读 audit 报告 (Task 2 写的) UVXY 4h baseline ret. Or grep search:

```bash
grep "UVXY.*4h" /Users/krisjiang/Desktop/grid/reports/audit_2026-05-16.md | head -3
# 预期: +82.81%
```

UVXY tuned ret (Step 2 实测) − UVXY baseline ret = diff.

### Step 4: 写 cross_symbol.csv

Use Write tool 创建 `/Users/krisjiang/Desktop/grid/runtime/experiments/4h_active/cross_symbol.csv`:

```csv
winner_symbol,winner_interval,winner_baseline_ret,winner_tuned_ret,other_symbol,other_baseline_ret,other_tuned_ret,diff_pp,sanity_pass
VXX,4h,226.79,<tuned VXX>,UVXY,82.81,<tuned UVXY>,<diff>,<True if diff >= -30 else False>
```

填实测值.

### Step 5: Commit

```bash
cd /Users/krisjiang/Desktop/grid
git add runtime/experiments/4h_active/cross_symbol.csv
git commit -m "data(phase_b): cross-symbol sanity UVXY 4h with VXX-tuned params (diff [N]pp)"
```

(替换 [N])

---

## Task 6: Phase B — Gate 5 项 + 决策

**Files:**
- Create: `runtime/experiments/4h_active/gate_verify.csv`

### Step 1: 收集 Gate 5 项数据

写 `/tmp/gate_b.py`:

```python
import json, csv

# 读 FINAL.json
final = json.load(open("/Users/krisjiang/Desktop/grid/runtime/experiments/4h_active/FINAL.json"))
chosen = final["chosen_params"]
cmp_chosen = final.get("comparison", {}).get("chosen", {})

# Baseline (VXX 4h 已知)
BL_ANN = 26.78
BL_TRADES = 279
BL_MDD = 15.17
BL_RET = 226.79
BL_WF_POSITIVE = 7  # WF 7/8 from Phase A

# Tuned
tuned_ann = float(cmp_chosen.get("annualized_return_pct", 0))
tuned_trades = int(cmp_chosen.get("total_trades", 0))
tuned_mdd = float(cmp_chosen.get("max_drawdown_pct", 0))
tuned_ret = float(cmp_chosen.get("total_return_pct", 0))

# WF tuned (from walkforward_summary in FINAL.json or walkforward.csv)
wf_sum = final.get("walkforward_summary", {})
# 这是 in-sample WF (tune.py 内部跑的). 需要额外跑 tuned params 8-window walk-forward.
# 现简化: 用 tune.py 内部 walkforward 数据 (6 windows 或 8 windows)
wf_windows = wf_sum.get("windows", 0)
wf_mean_ret = wf_sum.get("mean_valid_return_pct", 0)
# Count positive windows from walkforward.csv
wf_rows = list(csv.DictReader(open("/Users/krisjiang/Desktop/grid/runtime/experiments/4h_active/walkforward.csv")))
tuned_wf_positive = sum(1 for r in wf_rows if r.get("valid_return_pct", "0").lstrip("-").replace(".", "").isdigit() and float(r["valid_return_pct"]) > 0)
tuned_wf_total = len(wf_rows)

# Cross-symbol
cs_rows = list(csv.DictReader(open("/Users/krisjiang/Desktop/grid/runtime/experiments/4h_active/cross_symbol.csv")))
cs_diff = float(cs_rows[0]["diff_pp"]) if cs_rows else 0
cs_pass = cs_rows[0]["sanity_pass"] == "True" if cs_rows else False

# Gate 5 项
gate1_ann = tuned_ann >= BL_ANN * 0.5
gate2_trades = tuned_trades >= BL_TRADES * 1.5
gate3_mdd = tuned_mdd <= BL_MDD * 1.5
gate4_wf = tuned_wf_positive >= 4
gate5_cs = cs_pass

print(f"Gate 1 (ann >= bl × 0.5 = +{BL_ANN*0.5:.2f}%): tuned ann={tuned_ann:.2f}% → {'PASS' if gate1_ann else 'FAIL'}")
print(f"Gate 2 (trades >= bl × 1.5 = {int(BL_TRADES*1.5)}): tuned trades={tuned_trades} → {'PASS' if gate2_trades else 'FAIL'}")
print(f"Gate 3 (MDD <= bl × 1.5 = {BL_MDD*1.5:.2f}%): tuned MDD={tuned_mdd:.2f}% → {'PASS' if gate3_mdd else 'FAIL'}")
print(f"Gate 4 (WF >= 4/8 positive): tuned={tuned_wf_positive}/{tuned_wf_total} → {'PASS' if gate4_wf else 'FAIL'}")
print(f"Gate 5 (cross-symbol diff >= -30pp): {cs_diff}pp → {'PASS' if gate5_cs else 'FAIL'}")

all_pass = gate1_ann and gate2_trades and gate3_mdd and gate4_wf and gate5_cs
print(f"\nOverall: {'PASS - 应用 config' if all_pass else 'FAIL - 留 baseline + 诊断'}")

# 写 gate_verify.csv
rows = [
    ("annualized_pct", f"{BL_ANN}", f"{tuned_ann}", f">= bl × 0.5 (≥ {BL_ANN*0.5:.2f})", "PASS" if gate1_ann else "FAIL"),
    ("trades", f"{BL_TRADES}", f"{tuned_trades}", f">= bl × 1.5 (≥ {int(BL_TRADES*1.5)})", "PASS" if gate2_trades else "FAIL"),
    ("mdd_pct", f"{BL_MDD}", f"{tuned_mdd}", f"<= bl × 1.5 (≤ {BL_MDD*1.5:.2f})", "PASS" if gate3_mdd else "FAIL"),
    ("walkforward_positive", f"{BL_WF_POSITIVE}/8", f"{tuned_wf_positive}/{tuned_wf_total}", ">= 4", "PASS" if gate4_wf else "FAIL"),
    ("cross_symbol_diff_pp", "N/A", f"{cs_diff}", ">= -30", "PASS" if gate5_cs else "FAIL"),
    ("overall", "N/A", "N/A", "all PASS", "PASS" if all_pass else "FAIL"),
]
with open("/Users/krisjiang/Desktop/grid/runtime/experiments/4h_active/gate_verify.csv", "w") as f:
    f.write("metric,baseline,tuned,threshold,gate_passed\n")
    for r in rows:
        f.write(",".join(r) + "\n")
print("\nGate verify CSV 落盘.")
```

Run:
```bash
python /tmp/gate_b.py 2>&1 | tee /tmp/gate_b.txt
```

记录 Overall: PASS or FAIL.

### Step 2: Commit gate_verify.csv

```bash
cd /Users/krisjiang/Desktop/grid
git add runtime/experiments/4h_active/gate_verify.csv
git commit -m "data(phase_b): Gate 5 项 verify - overall [PASS/FAIL]"
```

(替换 [PASS/FAIL])

### Step 3: 条件 - Gate PASS 路径

如果 Overall == PASS: 进 Task 7 应用 config.

如果 Overall == FAIL: 跳过 Task 7, 直接进 Task 8 (final report 诊断).

---

## Task 7: (Conditional, Gate PASS only) 应用 tuned config + README + CHANGELOG

仅在 Task 6 Gate overall == PASS 时执行。Gate FAIL 跳到 Task 8。

**Files:**
- Modify: `config.py` (5 个 param 默认值)
- Modify: `README.md` (§5 winner 更新)
- Modify: `CHANGELOG.md` (追加 entry)

### Step 1: 修 config.py 5 个 param

读 chosen_params from FINAL.json。

Edit `/Users/krisjiang/Desktop/grid/config.py`. 找到这 5 行 (line numbers 可能漂移, grep 找):

```bash
grep -n "^ENTRY_MAX_ADX\b\|^ENTRY_MAX_ATR_PCT\b\|^GRID_SPACING_ATR_MULTIPLIER\b\|^GRID_RECENTER_THRESHOLD_ATR\b\|^EXIT_MAX_ADX\b" /Users/krisjiang/Desktop/grid/config.py
```

对每行 Edit 改默认值:
- 例 chosen.ENTRY_MAX_ADX=25:
  - OLD: `ENTRY_MAX_ADX = 20.0`
  - NEW: `ENTRY_MAX_ADX = 25.0  # 2026-05-16 Phase B active tune (winner: VXX 4h)`

5 个 param 全改.

### Step 2: Sanity backtest 用新默认 (无 env override)

```bash
cd /Users/krisjiang/Desktop/grid
python backtest.py --csv data/vxx_4h.csv --interval 4h --capital 10000 2>&1 | grep -E "总收益率|年化|交易次数"
```

**预期**: 与 Task 4 chosen 输出一致 (±0.01pp).

### Step 3: 更新 README §5

读 README §5:
```bash
grep -n "^## 5\|^### 5\|当前默认基线" /Users/krisjiang/Desktop/grid/README.md | head -5
```

Edit README §5. 关键改动: 当前默认基线从 "VXX 4h passive" 改为 "VXX 4h active tuned", 实测数字用 chosen 的.

保留历史"V49 baseline"注释。

### Step 4: 追加 CHANGELOG entry

读 CHANGELOG.md 末尾, append:

```markdown
## [2026-05-16 Phase B] Active Tuning Applied

### Changed
- Default trading config: VXX 4h **active** tuned (was VXX 4h passive baseline)
- 5 个 tuned params from EXTENDED_GRID + active_score:
  - ENTRY_MAX_ADX: 20 → <tuned>
  - ENTRY_MAX_ATR_PCT: 0.045 → <tuned>
  - GRID_SPACING_ATR_MULTIPLIER: 0.50 → <tuned>
  - GRID_RECENTER_THRESHOLD_ATR: 1.0 → <tuned>
  - EXIT_MAX_ADX: 22 → <tuned>
- Backtest baseline (VXX 4h): +226.79% → +<tuned>% / trades 279 → <tuned> / MDD 15.17% → <tuned>%

### Verified
- 5y full backtest reproduces tuned metrics bit-identical
- Walk-forward <tuned_wf>/8 positive
- Cross-symbol sanity: UVXY 4h with tuned params → ret diff <X>pp (>= -30 PASS)
- Gate 5 项: 全 PASS

### Reports
- `reports/active_tuning_2026-05-16.md`
- `runtime/experiments/4h_active/FINAL.json`
```

填实测值.

### Step 5: 跑 test.py + multi 回归

```bash
python /Users/krisjiang/Desktop/grid/test.py 2>&1 | tail -5

python /Users/krisjiang/Desktop/grid/scripts/run_multi_backtest.py \
    --symbols UVXY VXX --csv data/uvxy_4h.csv data/vxx_4h.csv \
    --allocations 0.5 0.5 --capital 10000 --interval 4h \
    --label phase_b_multi_sanity 2>&1 | grep "总回报"
```

**预期**:
- test.py 0 fails / 0 errors (与 baseline 一致)
- Multi 总回报: 不再是 +153.93% (因为 config 默认变了). 记录新值.

### Step 6: Commit

```bash
cd /Users/krisjiang/Desktop/grid
git add config.py README.md CHANGELOG.md
git commit -m "$(cat <<'EOF'
feat(tune): apply Phase B active tuned params (VXX 4h, more trades)

Gate 5 项全 PASS:
- ann: 26.78% → <tuned>% (>= bl × 0.5)
- trades: 279 → <tuned> (>= bl × 1.5)
- MDD: 15.17% → <tuned>% (<= bl × 1.5)
- WF: 7/8 → <tuned>/8 (>= 4)
- Cross-symbol diff: <N>pp (>= -30)

详见 reports/active_tuning_2026-05-16.md.
EOF
)"
```

---

## Task 8: 最终 Phase B 报告

**Files:**
- Create: `reports/active_tuning_2026-05-16.md`

### Step 1: 收集 artifacts

```bash
ls /Users/krisjiang/Desktop/grid/runtime/experiments/4h_active/
cat /Users/krisjiang/Desktop/grid/runtime/experiments/4h_active/gate_verify.csv
cat /Users/krisjiang/Desktop/grid/runtime/experiments/4h_active/cross_symbol.csv
```

### Step 2: 写 report

Use Write tool 创建 `/Users/krisjiang/Desktop/grid/reports/active_tuning_2026-05-16.md`:

```markdown
# Phase B Active Tuning Report (2026-05-16)

> Spec: docs/superpowers/specs/2026-05-16-audit-and-active-tuning-design.md
> Plan: docs/superpowers/plans/2026-05-16-audit-and-active-tuning.md
> Phase A audit: reports/audit_2026-05-16.md

## TL;DR

- **Goal**: 找比 VXX 4h baseline (+226.79% / 279 trades) 更活跃 (≥420 trades) 的参数, 接受 Sharpe drop
- **Approach**: EXTENDED_GRID (2000 combos) + new score formula (ann × trades/100 × wf_pass)
- **Result**: [Gate PASS — applied | Gate FAIL — kept baseline]
- **Tuned params** (if PASS): [list 5 changes]
- **Tuned metrics**: ret [实测]% / 年化 [实测]% / Sharpe [实测] / MDD [实测]% / trades [实测]

## 1. Score Formula 改动

老 (Phase 3 passive): `score = 0.6 × sharpe + 0.4 × (ann / mdd^0.5)`
新 (Phase B active): `score = annualized_ret × (trades / 100) × wf_pass_rate`

**理由**: User 明确要"更活跃交易系统, 接受 Sharpe drop". 新公式 trades 因子显式. Walk-forward gate 保留作为稳健性 floor.

## 2. EXTENDED_GRID

```
ENTRY_MAX_ADX:                [15, 20, 25, 30, 40]    (加 30, 40)
ENTRY_MAX_ATR_PCT:            [0.035, 0.045, 0.055, 0.07]  (加 0.07)
GRID_SPACING_ATR_MULTIPLIER:  [0.25, 0.30, 0.40, 0.50, 0.60]  (加 0.25, 0.30)
GRID_RECENTER_THRESHOLD_ATR:  [0.6, 0.8, 1.0, 1.2]    (加 0.6)
EXIT_MAX_ADX:                 [20, 22, 25, 28, 35]    (加 35)
```

2000 combos. 相比 FULL_GRID 324 combos: 6.2× 搜索空间。

## 3. Tune.py 修改

[简述 Task 3 改动]

## 4. Tune 结果

### Grid search top 5 by active_score

[paste Task 4 Step 5 输出]

### chosen_params (FINAL.json)

```json
[paste actual chosen_params]
```

### Tune.py 跑完信息

- Grid search: 2000 combos, ~[N] min
- Walk-forward top-8: [N] windows
- 3-split OOS top-8: [pass/fail]
- ±20% stability: [N] perturbations

### chosen vs baseline (5y full)

| Metric | Baseline (VXX 4h passive) | Tuned |
|---|---|---|
| Ret | +226.79% | +[实测]% |
| Annualized | +26.78% | +[实测]% |
| Sharpe | 0.321 | [实测] |
| MDD | 15.17% | [实测]% |
| Trades | 279 | [实测] |

## 5. Cross-symbol Sanity

| Winner Symbol | Winner Tuned Ret | Other Symbol | Other Baseline Ret | Other Tuned Ret | Diff |
|---|---|---|---|---|---|
| VXX 4h | +[实测]% | UVXY 4h | +82.81% | +[实测]% | [实测]pp |

[Sanity PASS / FAIL]

## 6. Gate 5 项

| Gate | Baseline | Tuned | Threshold | Pass |
|---|---|---|---|---|
| 1. Annualized | +26.78% | +[实测]% | >= +13.40% | [P/F] |
| 2. Trades | 279 | [实测] | >= 420 | [P/F] |
| 3. MDD | 15.17% | [实测]% | <= 22.76% | [P/F] |
| 4. WF positive | 7/8 | [实测]/8 | >= 4 | [P/F] |
| 5. Cross-symbol | N/A | [实测]pp | >= -30 | [P/F] |

**Overall**: [PASS | FAIL]

## 7. 决策落地

### Gate PASS 路径
- config.py 5 个 param → tuned 默认 (commit [SHA])
- README.md §5 winner 更新
- CHANGELOG.md 追加 Phase B entry

### Gate FAIL 路径 (诊断)
[哪一项 fail + 为什么]
[推荐 next step]
[baseline 保持不变]

## 8. 仍未做

1. 1h 周期 active tune (1h 当前负回报, 留 follow-up)
2. 实盘 paper account ≥ 4 周对账 (CLAUDE.md §7)
3. V49 -27pp 修复 (first bad commit `e369447` 已定位)
4. EXTENDED_GRID 是否 5 个 param 区间已足 — 可能漏掉更激进 ENTRY_MAX_BB_WIDTH 等其他参数

## 附

详细数据: `runtime/experiments/4h_active/` 下
- `FINAL.json`
- `search_log.csv` (2000 combos 全量)
- `walkforward.csv`
- `oos_report.csv`
- `stability.csv`
- `cross_symbol.csv`
- `gate_verify.csv`
```

填实测值.

### Step 3: Commit

```bash
cd /Users/krisjiang/Desktop/grid
git add reports/active_tuning_2026-05-16.md
git commit -m "docs(report): Phase B active tuning final report (Gate [PASS/FAIL])"
```

(替换 [PASS/FAIL])

---

## Self-Review

### Spec coverage check

| Spec section | Task 覆盖 | OK? |
|---|---|---|
| §2 Phase A Q1 grep + fingerprint + fresh backtest | Task 1 + 2 | ✅ |
| §2 Phase A Q2 4-cell MDD | Task 1 + 2 | ✅ |
| §2 Phase A Q4 战术化 + multi + test.py + commits | Task 1 + 2 | ✅ |
| §3 Phase B EXTENDED_GRID + active_score | Task 3 | ✅ |
| §3 Phase B 跑 tune.py | Task 4 | ✅ |
| §3 Phase B cross-symbol sanity | Task 5 | ✅ |
| §3 Phase B Gate 5 项 | Task 6 | ✅ |
| §3 Phase B Gate PASS 应用 | Task 7 (conditional) | ✅ |
| §3 Phase B 报告 | Task 8 | ✅ |
| §4 验收清单 8 项 | Task 1-8 全部 | ✅ |
| §5 不在 scope (1h, paper, V49) | 不在 plan ✅ | ✅ |
| §6 风险 + 缓解 (硬编码 / 时间 / cherry-pick / cross-symbol crash / fallback) | 各 Task 内 fail-mode 说明 | ✅ |

### Placeholder scan

- `[实测填]` 等是**运行时填充** ✓
- 无 "TBD" / "implement later" / "Similar to..." ✓
- 所有 step 含 actual command 或 actual code ✓

### Type consistency

- `active_score()` signature 在 Task 3 Step 3 定义, Task 3 Step 5 调 — 一致 ✓
- `EXTENDED_GRID` 在 Task 3 Step 2 定义, Task 3 Step 7 + Task 4 Step 2 调 — 一致 ✓
- `runtime/experiments/4h_active/` 路径在 Task 4-8 一致 ✓
- `chosen_params` (5 key dict) 在 Task 4-7 一致 ✓
- Gate 5 项阈值在 spec §3 + Task 6 一致 (ann ×0.5, trades ×1.5, MDD ×1.5, WF 4/8, cross-sym -30pp) ✓

---

## Execution Handoff

Plan 完成, 落 `docs/superpowers/plans/2026-05-16-audit-and-active-tuning.md`. 8 个 Task, 工作量 ~2-3h (主要瓶颈 Task 4 tune.py extended 跑 ~40 min + Task 3 tune.py 改造 ~30 min).

两个执行选项:

**1. Subagent-Driven (推荐)** — 每 Task 派 fresh subagent + 双阶段 review. 上轮 9-task tuning 用过, 适合本次 8 Task scope.

**2. Inline Execution** — 当前 session 顺序跑. Phase A audit 是机械操作, 可直接 inline; Phase B tune.py 长时跑可能不适合 inline.

哪种?
