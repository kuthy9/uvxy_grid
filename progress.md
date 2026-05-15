# progress — 调查会话日志

## 2026-05-14 会话 1
**目标**: 完成 A–I 9 项调查.

### 已完成
- 创建 task_plan.md / findings.md / progress.md
- 通读: tactical_config.py, tactical_rules.py, session_manager.py, bot_factory.py,
  orchestrator.py, config.py, README.md, PROJECT_STATUS.md
- 派 Explore agent 调研 grid_bot.py / state_machine.py / risk_manager.py /
  entry_filter.py / backtest.py / simulated_executor.py / scripts/tune_tactical.py / test.py
  → 拿到 8 处战术化分支落点 + 6 状态 FSM 实证 + entry_filter datetime.now() 违规位置
- 跑 test.py: **248/248 全过** (回归基线 OK)
- 跑回测矩阵:
  - UVXY 4h TURBO=ON: +70.89% / Sharpe 0.271 / MDD 13.44%
  - UVXY 4h TURBO=OFF: +80.16% / Sharpe 0.306 / MDD 16.08% (**比 ON 高 9.27pp**)
  - UVXY 4h TURBO=OFF + ADX_slope OFF: +65.69% (ADX_slope=-0.5 给 +14.47pp, 是唯一被实证正向的"战术增强")
  - UVXY 4h TURBO=ON + ADX_slope OFF: +60.97%
  - UVXY 1h TURBO=ON: **-20.48%** / Sharpe -0.557 / MDD 45.73%
  - UVXY 1d TURBO=ON: 0 笔
  - QQQ 4h TURBO=ON/OFF: 0 笔 (入场过滤永远拒绝, UVXY-tuned 参数不通用)
  - TQQQ 4h TURBO=ON: +45.38%, TURBO=OFF: +48.40% (战术化吃 -3pp)

### 关键发现 (写入 findings.md)
- F1: 战术化阈值已系统性中性化 (999/99999/1.0/0.0), Defensive=0/Forced=0/Profit-protect=0
- F2: 战术化在所有跑得动的样本上都是净负贡献
- F3: QQQ/TQQQ 与 README §2 "弱趋势"目标市场结构矛盾, 不应作为多标的候选
- F4: CLAUDE.md / README 与代码漂移 (4 vs 6 状态, README §5 数据过时)
- F5: entry_filter.py 3 处 datetime.now() fallback 违反 CLAUDE.md §9
- F6: 测试覆盖盲区 — 战术化用例全是单元级, 没有"功能存活" assertion

### 综合根因 (写入 findings.md G)
- 轴1: 战术化"激进"路径全部失活, 吃 -9pp (设计假设 vs UVXY 真实 edge 不匹配)
- 轴2: 战术化期间 legacy 路径被改动, 额外吃 -29pp (未 git-bisect 到具体 commit)

### 已用证据回答 A–I
所有问题都已经在 findings.md 中有明确证据指向. 见最终汇总.

### 未做的事 (明确说明而不是含糊)
- 没有 git-bisect 定位"-29pp"的具体 commit, 因为:
  (a) 本会话目标是诊断, 不是修复
  (b) 战术化本身的 -9pp 是设计问题, bisect 救不回来
  (c) 需要用户确认是否值得投入这条路径 (vs 直接回退到 V49 + 渐进引入新 feature)
- 没有改任何业务代码 — 用户问的是诊断与可行性, 不是修复任务.

---

## 2026-05-15 会话 2 — 严证伪 implementation 完成

**Implementation Plan**: `docs/superpowers/plans/2026-05-15-tactical-impossibility-proof.md`
**Design Spec**: `docs/superpowers/specs/2026-05-14-tactical-impossibility-proof-design.md`

### Commits (按顺序)
| Task | Commit | 说明 |
|---|---|---|
| P1 | 67d9edf | F5 fix: entry_filter 移除 datetime.now() fallback |
| P1 fixup | 36fbae6 | _check_earnings_blackout 同类 date.today() fallback |
| P2 | b34998b | BACKTEST_DEFAULT_CAPITAL 2000 → 10000 |
| P3 | 940c1e7 | README §3.2 4 状态 → 6 状态 |
| P4 | c261dcc | data/vxx.py + vxx_4h.csv (Alpaca, 3066 bars, ~5y) |
| P5 | cf5af6f | scripts/_proof_runner.py + smoke test |
| P6 | c65532c | scripts/prove_tactical.py + UVXY/VXX single_dim sweep (50×2 trial) |
| P7 | 02daa48 | joint + cost sweep (81 UVXY + 54 VXX + 8×2 cost trial) |
| P8 | 93d6634 | reports/tactical_proof_of_impossibility.md 完整证明报告 |
| P9 | 5237573 | TURBO_ENABLED 默认 OFF + tactical_config EXPERIMENTAL banner + 4 中性 regression test |
| P9 fixup | ef22764 | 12 affected test 加 _patch_tactical_on 显式 TURBO=ON |
| P10 | (本次) | README §5 + findings F7 + progress 收尾 |

### 严证伪结论
**通过**. 详见 findings.md F7 + reports/tactical_proof_of_impossibility.md.

### 测试状态
252/252 全绿 (4 个新 TestTacticalActionsReachable 中性 regression + 12 个 P9 affected test 显式 patch TURBO=ON).

### 未做的事 (诚实声明)
1. Code quality review P1 阶段 3 个 Important issue 未修 (config.py L209 stale "默认改 -1000" comment / entry_filter.py getattr fallback `-1.0` vs config `-0.5` / T1+S3 入场过滤缺单测) — 这些是上轮战术化遗留, 不在本轮 spec 范围.
2. CLAUDE.md §1 "4 状态 FSM" 描述过时未改 (用户明确要求).
3. V49 -29pp 回归未 git-bisect (独立 issue, 与本命题正交).

---

## 2026-05-15 会话 3 — 扩展筛选 (50 候选) + 多标的回测

**Implementation Plan**: `docs/superpowers/plans/2026-05-15-tactical-extended-screening.md`

### Commits (按顺序)
| Task | 说明 |
|---|---|
| E1 | screen_symbols.py CANDIDATES 15 → 50 (加杠杆 ETF + 高波动单股 + option income ETF + VIX) |
| E2 | yfinance 50 标的 screen + Part 1B 4 条规则 → 5/50 全过 (UVXY, RIOT, SOXL, SOXS, MARA) |
| E3 | data/multi_pull.py + Alpaca 拉 RIOT/SOXL/SOXS/MARA 4h 5y 数据 (各 ~3000 bars) |
| E4 | scripts/run_baseline_grid.py + 6 标的 × {ON, OFF} = 12 backtest baseline |
| E5 | Top-3 (MARA/SOXL/RIOT) × full sweep (single 50 + joint 54-81 + cost 8) ≈ 390 trial |
| E6 | reports/tactical_extended_screening.md 完整 report (Part 1-6) |
| E7 | findings.md F8 + progress.md 会话 3 + commit (本次) |

### 主要数据 (5 标的累计 641 trial)
- **B3a (≥ baseline) 反例**: 12 个 (UVXY/VXX 0, MARA 6 / SOXL 5 / RIOT 1) — 全部 trade ret ≤ +0.74%, 反例本质"战术化让大亏变小亏"
- **B3b (绝对 ≥ 0) 反例**: 3 个 (UVXY 2 = S2 no_fill_timeout 驱动 ret +26.21%/+21.68%; SOXL 1 = profit_protect 驱动 ret +0.74%)
- **B3c (≥ 20%) 反例**: 2 个 (UVXY, S2 no_fill_timeout)
- **关键 nuance**: UVXY 的 S2 反例 ret +26.21% 仍 < baseline +82.81% (-56pp), 即"战术化让你少赚 56pp"

### 结论
**严证伪 (按原 B3a) 扩展到 5 标的仍通过**. 战术化 4-action 在 UVXY 上的 S2 (no_fill_timeout) 能真触发 + 短 session + 绝对盈利, 但跑输 baseline. 不改 P9 决策 (TURBO 默认 OFF).

### 测试状态
252/252 全绿 (与会话 2 终态一致, 本次未改业务代码).

---

## 2026-05-15 会话 4 — 多标的并行 + V49 Default 恢复 + Walk-Forward

**Implementation Plan**: `docs/superpowers/plans/2026-05-15-multi-symbol-alpha.md`
**Design Spec**: `docs/superpowers/specs/2026-05-15-multi-symbol-alpha-design.md`

### Commits (按顺序)

| Task | Commit | 说明 |
|---|---|---|
| D1 | 97cdbc6 | scripts/walk_forward_fixed.py + UVXY smoke test (8 窗口, 5/8 positive) |
| D2 | ca345e3 | VXX walk-forward Gate 1 PASS (7/8 positive, 中位 +9.93%) |
| D3 | 8daa751 | config: ENTRY_MAX_WAIT_BARS default 1.5 → 12 (V49 恢复; 意外发现: 对回测路径无影响) |
| D4 | 3e10473 | scripts/run_multi_backtest.py + 单标的退化 smoke test (delta < 0.01pp) |
| D5 | 76ebb4d | UVXY+VXX 50/50: 全周期 +153.93% + 8 窗口 WF 5/8 正向 + risk_manager bug 修复 |
| D6 | (本次) | reports/multi_symbol_alpha.md + findings F9 + progress 会话 4 |

### D 整体 Verdict

**Step 1 (VXX walk-forward gate)**: ✅ PASS (7/8 正向, 中位 +9.93%)
**Step 2 (V49 default 恢复)**: ⚠️ Borderline — default 改了但实测对回测无影响; V49 -27pp 回归真实来源仍未定位
**Step 3 (多标的 50/50)**: ✅ PASS (+153.93% full 5y, 5/8 WF 正向, MDD 15.09%)
**整体**: ✅ 推荐多标的 UVXY+VXX 50/50 作为新 default 候选

### 主要数据

- 多标的 5y 合并: +153.93% (年化 +20.78%, MDD 15.09%)
- UVXY sub ($5k): +70.38%, VXX sub ($5k): +237.48%
- Walk-Forward 8 窗口: 5/8 正向, 中位 +15.89%, 最佳 +120.47% (win3), 最差 -9.19% (win7)
- risk_manager bug 修复: `_capital_reference()` helper 解决多标的 hard_stop baseline 错用全局 TOTAL_CAPITAL 问题

### 测试状态
252/252 全绿 (risk_manager 修复后 single-symbol 回归不变, test.py 全绿).

### 未做的事 (诚实声明)

1. V49 → HEAD -27pp 真实根本原因仍未 git-bisect (ENTRY_MAX_WAIT_BARS 已排除, 其他候选未追查)
2. `run_multi_backtest.py` 是回测驱动; 实盘多标的需 `bot_factory` + `ibkr_executor` 适配 + paper trading ≥ 4 周对账
3. `BT_*` 成本参数未与实盘对账 (属于 CLAUDE.md §8 优先级 2, 独立任务)
4. 多标的异常熔断机制未实现 (属于 CLAUDE.md §8 优先级 3, 独立任务)
5. 其他周期 (1h/1d) 多标的行为未测试
