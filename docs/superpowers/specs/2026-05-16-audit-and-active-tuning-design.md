# Audit + Active Tuning 设计 (2026-05-16)

## 1. 目标

用户 4 个 verification + 探索请求, 合并为单 spec 2 phase:

| # | Question | Phase |
|---|---|---|
| Q1 | 当前年化 26% 是否伪代码/硬编码幻觉? | A audit |
| Q2 | 当前最大回撤? | A audit |
| Q3 | 我想要更多交易次数 (动机: 更活跃的交易系统, 接受 Sharpe 退化) | B active tuning |
| Q4 | 是否修复重大漏洞 + 去战术化 + 保留多标的能力 + 回退稳定网格核心? | A audit |

## 2. Phase A — Audit

### Q1: 验证 +26.78% 年化不是硬编码幻觉

**步骤**:

1. **业务代码 grep 硬编码收益数字**:
```bash
grep -rnE "26\.78|226\.79|153\.93|82\.81|268\.41|201\.69|\+30169|25393" \
    --include="*.py" /Users/krisjiang/Desktop/grid/ \
    | grep -vE "archive/|/docs/|/reports/|/tests/|__pycache__"
```
预期: 0 hits 在主业务路径 (`grid_bot.py`, `risk_manager.py`, `backtest.py`, `simulated_executor.py`, `state_machine.py`, `entry_filter.py`, `bot_factory.py`, `orchestrator.py`, `main.py`)。

允许出现位置: `tests/` (test fixtures 可以 hardcode 已知 baseline), `docs/`, `reports/`, `archive/`。

如果业务代码出现硬编码收益: **REAL ISSUE**, 写到 audit 报告 + 修复。

2. **数据真实性验证**:
```bash
# 列结构与 Alpaca 一致
head -1 data/vxx_4h.csv
# Output: c,h,l,n,o,t,v,vw (Alpaca standard)

# Fingerprint
md5sum data/vxx_4h.csv data/vxx_1h.csv data/vxx_1d.csv data/uvxy_4h.csv

# Bar 数量 vs 5y 期望
wc -l data/vxx_*.csv data/uvxy_*.csv
```
预期: 列正确, 行数合理, fingerprint 记入报告 (供 future 验证).

3. **fresh backtest 复现 +226.79% / +26.78%**:
```bash
python backtest.py --csv data/vxx_4h.csv --interval 4h --capital 10000 \
    2>&1 | grep -E "总收益率|年化|交易次数|Sharpe|最大回撤"
```
预期: 与之前一致 (±0.01pp)。两次跑确认 bit-identical。

### Q2: 当前 baseline MDD

**步骤**: 跑 4 个 baseline backtest, 收 MDD:
```bash
# VXX 4h (current winner)
python backtest.py --csv data/vxx_4h.csv --interval 4h --capital 10000 | grep "最大回撤"

# UVXY 4h (legacy baseline)
python backtest.py --csv data/uvxy_4h.csv --interval 4h --capital 10000 | grep "最大回撤"

# Multi 4h
python scripts/run_multi_backtest.py --symbols UVXY VXX --csv data/uvxy_4h.csv data/vxx_4h.csv \
    --allocations 0.5 0.5 --capital 10000 --interval 4h --label audit_multi_4h | grep "最大回撤"

# VXX 1d (Sharpe-best alternative)
python backtest.py --csv data/vxx_1d.csv --interval 1d --capital 10000 | grep "最大回撤"
```

输出表格: symbol/interval/MDD/ret/Sharpe。

### Q4: 验证 refactor 仍在 main 分支

**步骤**:

1. **战术化在 archive/ 不在主路径**:
```bash
# 主路径 import 战术化
grep -rn "import tactical\|import session_manager\|from tactical\|from session_manager" \
    --include="*.py" /Users/krisjiang/Desktop/grid/ \
    | grep -vE "archive/|__pycache__|/tests/"
```
预期: 0 hits.

```bash
# archive/tactical 目录结构完整
ls archive/tactical/ archive/tactical/scripts/ archive/tactical/reports/
```
预期: 3 模块 + 3 sweep 工具 + 2 reports + README 都在.

2. **多标的能力仍工作**:
```bash
python scripts/run_multi_backtest.py --symbols UVXY VXX \
    --csv data/uvxy_4h.csv data/vxx_4h.csv --allocations 0.5 0.5 \
    --capital 10000 --interval 4h --label audit_multi_check \
    2>&1 | grep -E "总回报|UVXY|VXX"
```
预期: +153.93% / UVXY +76.46% / VXX +237.48% (与 production refactor 终态一致)。

3. **关键 fix commits 在 main**:
```bash
git log --oneline | head -30 | grep -E "refactor|tactical|fix|tune|phase"
```
预期: 看到 `6402ed7 chore(archive): tactical 模块`, `0b1abcf fix(refactor): IBKRExecutor 多标的`, `11cfb72 fix(risk): check_daily_loss`, `5d0a7be fix(test): T1-T12 残留`, `8f54309 data(phase3): tune.py 跑 VXX 4h`, `8fc9ac4 docs(report)` 等 12 个 production refactor commits + tuning commits.

4. **test.py 当前状态**:
```bash
python test.py 2>&1 | tail -5
```
预期: `Ran 252 tests / OK (skipped=70)`, **0 failures / 0 errors** (T1-T12 5d0a7be 后的状态)。

### Phase A 产物

`reports/audit_2026-05-16.md` — 单页报告, 5 节:
- §1 Q1 grep 结果 + fingerprint + fresh backtest 复现
- §2 Q2 MDD 表 (4 cells)
- §3 Q4 战术化隔离验证
- §4 Q4 多标的 + test.py + commits 验证
- §5 结论 (全部 PASS or 列出 issue)

## 3. Phase B — Active Tuning (Q3)

### 目标

在 VXX 4h baseline 上找比 baseline 更活跃 (≥1.5× trades) 的参数, 接受 Sharpe 退化, 但保持系统盈利能力 (ret ≥ baseline × 0.5)。

### 新评分公式

```
score = annualized_ret × (trades / 100) × (wf_pass_rate / 100)
```

- 与 Phase 3 老公式 `ann × wf / mdd` 对比: 加 `trades/100` 因子, 移除 `/mdd` (放宽风险约束)
- 例: baseline ann=26.78, trades=279, wf=87.5%
  - 老 score = 26.78 × 0.875 / 15.17 = 1.545
  - 新 score = 26.78 × 2.79 × 0.875 = **65.4**
- 双倍 trade 情景: ann=15, trades=560, wf=50%
  - 老 score = 15 × 0.5 / 25 = 0.30 (差)
  - 新 score = 15 × 5.60 × 0.50 = **42.0** (略低于 baseline 65.4, 但有竞争力)

### 扩展 grid

老 FULL_GRID 已被 walk-forward 拒绝 (chosen=baseline)。Phase B 加入更激进 value:

```python
EXTENDED_GRID = {
    "ENTRY_MAX_ADX":                [15, 20, 25, 30, 40],          # 加 30/40 → 更宽容入场
    "ENTRY_MAX_ATR_PCT":            [0.035, 0.045, 0.055, 0.07],   # 加 0.07 → 更高波动率允许
    "GRID_SPACING_ATR_MULTIPLIER":  [0.25, 0.30, 0.40, 0.50, 0.60],# 加 0.25/0.30 → 网格更密 → 更多 trade
    "GRID_RECENTER_THRESHOLD_ATR":  [0.6, 0.8, 1.0, 1.2],          # 加 0.6 → 更敏感 recenter
    "EXIT_MAX_ADX":                 [20, 22, 25, 28, 35],          # 加 35 → 更晚退出 → 更多 hold
}
```

= **2000 combos** (5×4×5×4×5). 大约 35-40 min @ 6 workers (vs 老 FULL 324 combos × 10 min)。

### Phase B Gate (5 项, 全过才 PASS)

| # | Gate | 阈值 | 含义 |
|---|---|---|---|
| 1 | Annualized | ≥ baseline × 0.5 = +13.4% | 接受半折但不亏 |
| 2 | Trades | ≥ baseline × 1.5 = 420 trades | 明显更活跃 |
| 3 | MDD | ≤ baseline × 1.5 = 22.76% | 放宽风险 (vs 老 ×1.1) |
| 4 | Walk-forward | ≥ 4/8 positive | 50% pass rate, 放宽 (vs 老 6/8) |
| 5 | Cross-symbol | UVXY-tuned ret diff ≥ -30pp | 防 VXX-specific overfit |

### 步骤

1. **扩展 grid scripts/tune.py**:
   - 修改 `FULL_GRID` 添加新 values (或加 `EXTENDED_GRID` 常量 + 命令行 `--extended` flag)
   - 修改 `score()` 公式加 trades 因子 (或加 `--active-score` flag)

2. **跑 tune.py extended VXX 4h**:
   ```bash
   python scripts/tune.py --csv data/vxx_4h.csv --interval 4h --workers 6 --extended --active-score
   ```

3. **读 FINAL.json chosen_params**

4. **Cross-symbol sanity** (env override 跑 UVXY 4h):
   ```bash
   ENTRY_MAX_ADX=<v> ... python backtest.py --csv data/uvxy_4h.csv --interval 4h --capital 10000
   ```

5. **Gate 5 项判定**

6. **若 PASS**: 修 config 默认 + README + CHANGELOG. **若 FAIL**: 留 baseline + 诊断报告.

### Phase B 产物

- `runtime/experiments/4h_active/FINAL.json` (tune.py 输出)
- `runtime/experiments/4h_active/{search_log, walkforward, oos_report, stability}.csv`
- `reports/active_tuning_2026-05-16.md` (Phase B 完整报告)
- (Gate pass only) config.py + README.md + CHANGELOG.md 更新

## 4. 验收清单

- [ ] Phase A Q1: grep 主路径 0 hardcoded ret hits, fingerprint 落盘, fresh backtest 复现 +226.79%/+26.78%
- [ ] Phase A Q2: 4-cell MDD 表落盘
- [ ] Phase A Q4: 战术化主路径 0 imports, multi-symbol +153.93% 复现, test.py 0 fails/errors, 关键 fix commits 在 main
- [ ] `reports/audit_2026-05-16.md` 单页 5 节完整
- [ ] Phase B EXTENDED_GRID 2000 combos 跑完
- [ ] Phase B Gate 5 项判定 (PASS/FAIL 文档化)
- [ ] (Gate PASS) config + README + CHANGELOG 更新
- [ ] `reports/active_tuning_2026-05-16.md` 完整

## 5. 不在 scope 内

- 1h 周期 active tuning (1h 当前 5y 负回报, 调整风险大, 留 follow-up)
- 实盘 paper 对账 (CLAUDE.md §7 user manual)
- V49 → 8c41c62 -27pp 回归修复 (T8 已定位 first bad `e369447`, 留独立 spec)
- 数据 puller 验证 (Alpaca 历史拉数已在 D5 阶段完成)

## 6. 风险 + 缓解

| 风险 | 缓解 |
|---|---|
| Phase A grep 发现真硬编码 | 立即修复 + 重跑 backtest 验证收益数仍真 |
| EXTENDED_GRID 2000 combos 时间过长 | 接受 ~40 min 一次性投入, 或加 `--quick` flag 缩到 ~400 combos |
| 新 score 公式 cherry-pick "高 trade 但低 wf 通过率" 组合 | Gate 4 (wf ≥ 4/8) 仍守底 |
| Cross-symbol 崩盘 (Gate 5 fail) | 直接 Phase B Gate fail, 保留 baseline |
| Tune.py extended 触发 fallback (chosen=baseline) | 与 Phase 3 同结局 — baseline 已是稳健最优, 接受 |

## 7. Self-check

- Placeholder: 无 TBD / TODO / "Similar to..."
- 内部一致性: Phase A 与 Phase B 输入/输出不耦合, 各自独立
- Scope: 单 spec 适中 (audit 30 min + tuning 40 min + report 20 min ≈ 90 min 总)
- 二义性: Gate 5 条无歧义, score 公式精确, grep 排除规则明确
