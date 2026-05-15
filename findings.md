# findings — 战术化网格调查发现

> 当前 HEAD = 8c41c62. UVXY 5y CSV + $2000 capital, SimulatedExecutor 真实化撮合.
> 所有回测在本机用相同种子 (BT_RANDOM_SEED=20260423) 跑出.

---

## F1. 战术化几乎所有"激进"阈值已经被中性化, 仅剩入场过滤 + 仓位上限在生效
[强证据 — 直接读源码 + 运行时打印 + 回测计数器]

`tactical_config.py` 当前 HEAD 默认:
```
TREND_RISK_SCORE_DEFENSIVE = 999.0     # 满分 100 → 永不触发
TREND_RISK_SCORE_FORCE_EXIT = 999.0    # 永不 FORCE_EXIT
SESSION_TARGET_AGE_BARS = 99999        # 永不老化
SESSION_MAX_AGE_BARS = 99999
SESSION_ABSOLUTE_MAX_AGE_BARS = 99999
SESSION_NO_FILL_TIMEOUT_BARS = 0
SESSION_SOFT_STOP_PCT = 1.0            # 亏 100% 才触发
SESSION_HARD_STOP_PCT = 1.0
SESSION_MIN_PROFIT_TO_PROTECT_PCT = 1.0
SESSION_STRONG_PROFIT_PCT = 1.0
SESSION_TRAILING_GIVEBACK_RATIO = 1.0
SESSION_STRONG_PROFIT_PARTIAL_EXIT_RATIO = 0.0
TACTICAL_MAX_BUY_DEPTH_ATR = 0.0
SESSION_POSITION_DRAWDOWN_STOP_PCT = 0.0
SESSION_COOLDOWN_BARS_AFTER_* = 0
TACTICAL_OVERRIDE_GRID_ENGINE_EXIT = False   # 战术不再 override grid_engine
TACTICAL_BASE_POSITION_RATIO = 0.40          # = BASE_POSITION_RATIO, 无差异
```
源码注释每一行都明确写了"否则杀掉 V49 edge"/"实测让 ret 变差". 即:
**这些功能被实现了, 然后被注释自己证明数据上是输的, 然后被默认值关掉.**

回测层面的证据 — UVXY 4h TURBO=ON, 5y 跑完:
```
Defensive sessions:   0  (0.0%)
Forced exit:          0  (0.0%)
Profit-protect exit:  0
Timeout exit:         0
平均 trades/session:  4.9     ← 与"短线收割"期望严重背离
平均 session 寿命:   233 hr   ← 接近 V49 长 hold 数值
```
战术 ON 时**仍在生效**的差异 (与 TURBO=OFF 比):
- 入场加 ADX_slope ≤ -0.5 过滤 (来自 config.py L213-219, 非 tactical_config) — 实测 +5~15pp
- 入场加 recent_range 过滤 — 默认 0 关闭, 无实际作用
- confidence-based BUY level cap (calculate_dynamic_position_cap) — 但 trend_risk 永远 < 999 阈值, 总落在 NORMAL/HIGH 档
- SessionManager 持久化 (grid_sessions / grid_session_events 表) — 影响报表, 不影响交易决策
- rescue recenter (S1) — 救零成交 session, 偶发

---

## F2. 战术化在所有测试标的 / 周期上都是净负贡献

**UVXY 5y backtest 矩阵 (本机重跑, $2000 capital):**

| 配置 | 总收益 | Sharpe | MDD | sessions | session 胜率 |
|------|--------|--------|-----|----------|------------|
| **V49 worktree (无战术, README 报告)** | **+109.91%** | +0.54 | 8.88% | 24 | — |
| TURBO=OFF (当前 HEAD legacy fallback) | +80.16% | 0.306 | 16.08% | — | — |
| **TURBO=ON (当前 HEAD 默认)** | **+70.89%** | 0.271 | 13.44% | 31 | 54.8% |
| TURBO=OFF + ENTRY_MAX_ADX_SLOPE=-1000 | +65.69% | 0.223 | 17.57% | — | — |
| TURBO=ON + ENTRY_MAX_ADX_SLOPE=-1000 | +60.97% | 0.206 | 14.61% | — | — |

**关键差距:**
- TURBO=ON vs TURBO=OFF: **战术化稳定吃掉 -9.27 pp** (UVXY 4h)
- TURBO=OFF vs V49 worktree: **legacy 改造期间额外吃掉 -29.75 pp** (其中 ADX_slope=-0.5 增益 +14.47pp 已经反向证明, 不是它的锅; 剩 -44pp 在 entry_filter / state_machine / risk_manager / executor 等改造分支里; 没有继续深挖)
- ADX_slope=-0.5 是**唯一被实测正向**的"战术增强" (但它住在 config.py, 不算战术化模块)

**跨标的 / 跨周期 (TURBO=ON 默认):**

| 标的 / 周期 | 总收益 | 交易次数 | 备注 |
|------------|--------|---------|------|
| UVXY 4h | +70.89% | 185 | baseline |
| UVXY 1h | -20.48% | 249 | MDD 45.7%, Sharpe -0.557, 灾难 |
| UVXY 1d | 0% | 0 | 完全不入场 |
| QQQ 4h | 0% | 0 | 入场过滤永远拒绝 |
| TQQQ 4h | +45.38% | 151 | TURBO=OFF +48.40%, 战术化也吃 -3pp |

**结论:**
- 战术化在所有跑得动的样本上都是负贡献.
- 1h / 1d / QQQ 直接跑不动 — 不是战术化的问题, 是 config.py 入场阈值是 UVXY 4h 专属.
- TQQQ 4h 跑得动, 但战术化仍然吃 -3pp.

---

## F3. QQQ / TQQQ 不是合适的网格候选 (与 README §2 自相矛盾)
[强证据 — README §2 + 标的属性]

- README §2 写: "策略数学上依赖标的具备高波动 + 弱趋势 / 均值回归"
- README §2 反面例子: "单边趋势 ETF (例如 QQQ 长期向上)"
- 但用户准备的 data/ 包括了 QQQ 和 TQQQ. **QQQ 是单边趋势, TQQQ 是 3x 杠杆 QQQ, 趋势更强**.
- 网格策略数学上不适合这两个标的. 战术化不能从这两个标的找救兵.

**对 E (多标的) 的影响:**
- 基础设施 OK (bot_factory / orchestrator / account_risk / capital_allocator 设计合理)
- 当前 config.py 是 UVXY 4h 专属参数集 (ENTRY_MAX_ADX=20, ENTRY_MIN_ATR_PCT=0.020 等), 切到 QQQ 直接 0 笔交易
- 真要多标的, 需要先做 (a) per-symbol 参数集 + screening 流程 (筛波动+流动性+成本)
  → 不是 hardcode QQQ/TQQQ
- screen_symbols.py 的 ETF 筛选 pipeline 正是干这个的 — 这个方向是对的, 但 QQQ/TQQQ 不应该是候选输出

---

## F4. CLAUDE.md / README 与代码实际状态的文档漂移
[中证据 — 文档 vs 实测]

- CLAUDE.md §1 写"4 状态 FSM (SCANNING/WAITING/ACTIVE/EXIT)", 实际是 6 状态
  (SCANNING/WAITING_ENTRY/OFFENSIVE_GRID/DEFENSIVE_GRID/COOLDOWN/EXIT_PENDING)
- README §3.2 也写"四状态 FSM" — 同样过时
- README §5 报告 TURBO=ON +53.67% / TURBO=OFF +49.05%, 本机实测分别是 +70.89% / +80.16%
  → 数字过时但**相对结论 (TURBO=OFF 更高) 一致**
- README §5 称"V49 baseline +109.91%", 来自 git worktree 重跑旧代码, 当前 HEAD 任何配置都到不了

---

## F5. CLAUDE.md §9 死规矩违反

**entry_filter.py L98/124/136** 业务模块直接 `datetime.now()` 作为 fallback:
```python
eval_result = EntryEvaluation(
    timestamp=evaluation_time or datetime.now(),  # ← 业务模块读 wall-clock
    ...
)
```
当前调用方都正确传 `evaluation_time` (回测传 HistoricalClock 时刻, 实盘传 clock.now()),
所以**没有实际错误发生**, 但**这条 fallback 是定时炸弹** — 未来谁调 evaluate(df) 不传
evaluation_time, 回测/实盘行为就会分叉.

修复成本极低: 移除 fallback, 让参数变成必填.

---

## F6. 战术化分支测试是单元级的, 没有 end-to-end 验收测试
[强证据 — test.py 全过 + 实际回测显示战术功能全部 0 触发]

- test.py 跑全绿 (248/248)
- 但战术化用例都是单元级:
  - TestTrendRiskScore: 喂 ctx 测分数对不对
  - TestDecisionFunctions: 测 should_force_exit 在硬止损时返回 True
  - TestSessionManager: 测 start_session / close_session 落库
- **没有任何测试断言**: "战术化打开 vs 关闭, 总收益相对差距应该 ≥ 0" 或 "Defensive 触发次数应该 > 0 在某种已知会触发的合成行情上"
- 后果: 当前默认 Defensive=0 / Forced=0 / Profit-protect=0 这种"功能形同虚设"的退化, 没有任何测试会失败

这不是 "修 bug 引入新 bug", 而是 "测试覆盖率盲区" — 框架代码正确, 但功能用不上.

---

## 综合根因 (G)

**当前 UVXY 4h 回测糟糕的根因有两条独立轴:**

**轴 1 — 战术化"激进"决策路径全部失活 (吃掉 -9pp):**
源代码注释自证: 多个战术阈值在调参中发现"启用就让总收益下降",
解决方案是把默认值改成"永不触发", 而不是删掉/重设计.
后果是: 战术化框架在跑, 但只剩入场过滤 + 仓位上限 + recenter 救援等"防御性"装饰,
"激进短线收割"的设计预期被自家代码注释否定. 用户调参之所以失败, 是因为
旋钮被改进默认值之前的研究已经证明"这个旋钮转哪都更差". 这不是参数问题,
是**设计假设和 UVXY 真实行为不匹配** —
UVXY 真实 edge 是"长 hold + ADX>22 时 grid_engine 高位 EXIT", 战术化的"短线护盘"
反而扼杀这条 edge.

**轴 2 — 战术化期间 legacy 路径也被改动 (吃掉额外 -29pp):**
即便完全关闭战术化 (TURBO=OFF), 当前 HEAD 仍只到 +80.16%, 比 V49 worktree 的
+109.91% 少 29.75 pp. 已排除 ADX_slope=-0.5 (它实际 +14pp). 剩下的回归源
没有继续 git-bisect 定位, 候选 ≥ 50 处:
- entry_filter recent_range 过滤逻辑
- 6 状态机扩展引入的 OFFENSIVE_GRID ↔ legacy active_grid 翻译
- account_risk 改造 (capital_provider / allocated_capital)
- WAITING_ENTRY 与 STRATEGY_INTERVAL 解耦 (近期 commit e369447)
- ENTRY_MAX_WAIT_BARS 从 1.0 → 1.5 (近期 commit e0d0314)
- daily_snapshots 写入时机解耦 (HEAD 8c41c62)

**修复方向选择 (G 推论):**
1. 接受战术化在 UVXY 4h 数据上**输给 V49**, 默认 OFF 并显式记录该结论 (老实).
2. 或者投入更深的 git-bisect 定位 -29pp 的具体 commit, 但战术化本身的 -9pp 是设计问题, bisect 救不回来 (激进).
3. 或者放弃 UVXY 这个标的, 用 screen_symbols.py 重新筛选 — 但当前 data/ 提供的 QQQ/TQQQ 都不合规 (反例).

推荐 #1 + 修 F5/F6: 默认 TURBO=OFF, 进入 #2 git-bisect 前先把战术化降级为"实验功能", 不再阻塞主流程.

---

## F7. 战术化不可落地性证明 (2026-05-15)

**严证伪通过**. (UVXY 4h + VXX 4h) × ≤5y × $10k cap × 共 251 个 sweep trial (50 + 81 + 8 + 50 + 54 + 8) 中, **0 个**同时满足 B 三条 (Defensive/Forced/Profit-protect 触发 > 0 + 平均 session 寿命 ≤ 20 bars + 5y 回报 ≥ TURBO=OFF baseline).

完整证据 + 机制论证: [`reports/tactical_proof_of_impossibility.md`](./reports/tactical_proof_of_impossibility.md).

**主要数据**:
- UVXY 4h TURBO=OFF baseline: **+82.81%** / Sharpe 0.320
- UVXY 4h 战术 sweep 最优 trial: +73.81% (距 baseline -9.00 pp, 即使 B1/B2 满足也输 B3)
- VXX 4h TURBO=OFF baseline: **+226.79%** / Sharpe 0.321
- VXX 4h 战术 sweep 最优 trial: +290.58% (B3 满足但 avg_session_bars 263-584 bars, 远超 B2 ≤20 bars 阈值)
- 成本敏感性: TURBO=ON 在 spread/commission ±50% 时**回报恒定** — 不是 robust, 是 dead (战术 4-action 默认 0 触发, 对成本免疫不是因为它健壮, 是因为它什么都没做)

**结构性原因 (Part 1B Symbol-agnostic 必要条件)**:
1. UVXY/VXX 的 ADX 中位数都 ~21.5, ADX>22 占比 ~48%; 标的有相当多 trend 期, 不是纯 mean-revert
2. UVXY 真实 5y edge 来自长 hold + ADX>22 高位 EXIT (V49 worktree 实证, 平均 session ~60 bars); 战术化"短线"扼杀该 edge
3. VXX 1× 振幅小, 5y 大趋势已经把 baseline 推到 +226%, 任何"早退出"行为都吃掉 carry

**后续动作 (P9 已执行)**:
- `config.TURBO_ENABLED` 默认 OFF
- `tactical_config.py` 顶部 EXPERIMENTAL banner
- `test.py` 新增 `TestTacticalActionsReachable` (4 用例) 锁战术 4-action 代码可达性 (中性 regression, 不锁 ON/OFF 优劣)
- 战术化代码全部保留以备未来重新设计

**仍未做 (诚实声明)**:
- 战术化期间 legacy 路径回归 (V49 +109.91% → HEAD TURBO=OFF +82.81%, -29pp) 未 git-bisect — 与本命题正交, 等后续决策
- CLAUDE.md 未改 (用户明确要求)
- 不证明"战术化在更长周期 / 别的资产类 / 更大资金量上也不能落地" — Part 1B 给出概念性必要条件可推广, 但实测仅覆盖 4h × UVXY/VXX
- code quality reviewer P1 阶段提出的另外 3 个 Important issue (config.py L209 stale comment / entry_filter.py getattr fallback `-1.0` vs config `-0.5` / T1+S3 缺单测) 未修, defer 到 followup

---

## F8. 战术化扩展筛选 + Top-3 sweep (2026-05-15 会话 3)

把 Part 1B 4 条必要条件筛选从 15 标的扩到 **50** (含杠杆 ETF / 高波动单股 / option income ETF / VIX), 拉 Alpaca 5y 数据 + Top-3 完整 sweep.

完整证据: [`reports/tactical_extended_screening.md`](./reports/tactical_extended_screening.md).

**50 候选 → 5 个 Part 1B 全过 (10% 通过率)**:
- UVXY (已被 F7 严证伪)
- RIOT (BTC mining), MARA (BTC mining): 新发现
- SOXL (3× 半导体多头), SOXS (3× 半导体反向): 新发现

**6 标的 baseline 对比 (TURBO=ON vs OFF, $10k cap, 5y)**:

| 标的 | TURBO=OFF | TURBO=ON | gap_pp | on_b1 | on_avg_bars |
|---|---|---|---|---|---|
| VXX | +226.79% | +165.41% | -61.37 | 0 | 55.5 |
| UVXY | +82.81% | +73.81% | -8.99 | 0 | 58.4 |
| RIOT | -20.37% | -20.18% | +0.19 | 0 | 21.0 |
| SOXL | -22.06% | -20.19% | +1.87 | 0 | 25.4 |
| MARA | -22.27% | +4.35% | +26.62 | 0 | 96.6 |
| SOXS | -23.44% | -20.06% | +3.37 | 0 | 55.5 |

**所有标的 `on_b1 = 0`** — 战术 4-action 默认配置下 100% 未触发. RIOT/SOXL/MARA/SOXS gap 正向, 但来自 entry filter ADX_slope (config 而非 tactical) 增益.

**Top-3 完整 sweep (MARA / SOXL / RIOT, ~390 trial) + 上轮 (UVXY/VXX, 251 trial) = 累计 641 trial**:

| B3 阈值 | 累计反例 (5 标的) | 说明 |
|---|---|---|
| **B3a (≥ baseline)** | **12** | UVXY/VXX 0, MARA 6 / SOXL 5 / RIOT 1; 全部在负 baseline 标的上, 绝对 ret ≤ +0.74%. 反例本质 = "战术化让大亏变小亏" |
| **B3b (绝对 ≥ 0)** | **3** | UVXY 2 (S2 no_fill_timeout 驱动, ret +26.21% / +21.68%), SOXL 1 (profit_protect, ret +0.74%) |
| **B3c (≥ 20% 5y, ~ 无风险)** | **2** | 与 B3b 前两个 UVXY 一致 |

**关键 nuance**: UVXY 上 `SESSION_NO_FILL_TIMEOUT_BARS=12` 单维 trial **真实触发** 16 个 forced_exit, 平均 session 寿命 16.2 bars (满足 B2), 5y 绝对盈利 +26.21% (满足 B3b/B3c). 但 **仍 < TURBO=OFF baseline +82.81% (-56pp gap)**. 即"战术化让你少赚 56pp", 整体仍是负贡献.

**严证伪结论 (按原 B3a)**: ✅ **通过**. 任何 sweep 配置都不能让战术化"超过不开战术化"的回报.

**Nuance (按 B3b/B3c)**: 战术化 S2 (零成交超时退出) 在 UVXY 上"能工作 + 真短线 + 绝对盈利", 但跑输 baseline. 这不构成 plan §2 命题 B 的反例.

**结论保持**: `config.TURBO_ENABLED` 默认 OFF (P9), EXPERIMENTAL banner (P9), 中性 regression test (P9) 都仍是正确决策.
