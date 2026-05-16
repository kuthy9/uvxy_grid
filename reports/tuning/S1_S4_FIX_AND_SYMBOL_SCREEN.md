# S1-S4 结构性修复 + 候选标的筛选结果

date: 2026-05-14
配置: TACTICAL_BASE_POSITION_RATIO 仍为 env 控 (默认 0.0), S1/S2/S3 已并入主代码默认开启

---

## 1. S1-S3 代码改动 (默认开启)

### S1: OFFENSIVE rescue_recenter

`tactical_rules.should_disable_recenter` + `tactical_config`:
- 新增 `TACTICAL_OFFENSIVE_RESCUE_RECENTER_ENABLED = True`
- 新增 `TACTICAL_OFFENSIVE_RESCUE_MIN_AGE_BARS = 8`
- 规则: `mode==OFFENSIVE AND recenter_used_count==0 AND filled_buy_levels=[] AND age >= 8` → 放行一次 recenter (强制, 不走 should_recenter 的 1.5×ATR 阈值)
- 新增 `SessionContext.recenter_used_count` + `SessionManager.record_recenter()`, grid_bot 在 recenter 完成后 +1

### S2: SESSION_NO_FILL_TIMEOUT_BARS

`tactical_rules.should_force_exit`:
- 新增 `SESSION_NO_FILL_TIMEOUT_BARS = 6` (默认, env 覆盖, 设 0 关闭)
- 规则: `age >= 6 AND filled_buy_levels=[]` → ACTION_FORCE_EXIT reason='no_fill_timeout'
- `scripts/tune_tactical.classify_exit_reason` 加 `no_fill_timeout` 分类 (优先级在 `age` 之前)

### S3: ENTRY_MIN_RECENT_RANGE_ATR

`entry_filter._check_conditions` + `config.py`:
- 新增 `ENTRY_RECENT_RANGE_LOOKBACK_BARS = 20` 
- 新增 `ENTRY_MIN_RECENT_RANGE_ATR = 2.0` (设 0 关闭)
- 规则: 过去 20 bar 的 `(max High − min Low) / ATR < 2.0` → 拒绝入场, reason "近20bar震荡幅度不足"

测试: `python test.py` → **245 tests OK**, 无回归.

---

## 2. S4: base ratio A/B (P0 top-1 + S1-S3 active, 5y UVXY 4h)

| base_ratio | ret% | dd% | pf | sharpe | sessions | gcwr% | swr% | avgT/sess | pp_exit | TO_exit | base_pnl | grid_pnl |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **0.00** | -2.38 | 3.93 | 0.453 | -3.06 | 62 | 66.7 | 11.3 | 0.95 | 3 | 59 | $0 | -$238 |
| **0.05** | **+0.64** | 4.63 | **0.861** | -1.98 | 64 | **72.0** | **57.8** | **1.86** | **7** | 57 | -$70 | -$193 |
| **0.10** | **+1.58** | 8.51 | 0.947 | -1.27 | 64 | 72.0 | 57.8 | 1.86 | 8 | 55 | **+$103** | -$263 |

exit reason breakdown:
- base=0.00: `{no_fill_timeout: 48, age: 11, profit_protect: 3}`
- base=0.05: `{no_fill_timeout: 50, age: 7, profit_protect: 7}`
- base=0.10: `{no_fill_timeout: 49, hard_stop: 1, age: 6, profit_protect: 8}`

### S4 解读

**vs 之前 base=0 + 无 S1-S3 (P0 top-1 原版):**
- 旧: ret -5.91% / dd 6.48% / pf 0.355 / sessions 39 / age_timeout 32
- 新 base=0: ret -2.38% / dd 3.93% / pf 0.453 / sessions 62 / no_fill_timeout 48
- **S1-S3 单独贡献: +3.5 pp 回报, -2.5 pp DD**.  no_fill_timeout (48/62 = 77%) 替代了大量 age timeout, 释放出 age 预算让 sessions 数量翻倍 (39 → 62).

**vs base=0 (S1-S3 active):**
- base=0.05 把 ret 拉到 **正** (+0.64%), DD 只多 +0.7 pp
- profit_factor 0.45 → **0.86** (近翻倍)
- session_win_rate 11% → **58%** (一个小 base 让"无网格成交但价格走对方向"的 session 也能赚到方向 carry)
- avg_trades/session 0.95 → 1.86 (BUY/SELL 配对率改善)
- base_exit_loss = -$70 (5y, 平均 -$1/session) — 远低于旧 base=0.40 的 -$1,420 / 81 = -$17.5/session

**vs base=0.10:**
- ret +1.58% 看似最好, 但 DD 从 4.6% 跳到 **8.5%**, 1 个 hard_stop 触发 — base 占比太大让 directional 风险回来
- profit_factor 0.95 vs 0.86 — 边际改善很小
- 不推荐.

### S4 建议

**Recommended: `TACTICAL_BASE_POSITION_RATIO = 0.05`**

- ret 转正, DD 控制在 5% 内
- profit_factor 0.86 (向 1.0 靠近, edge 实在化)
- base_exit_loss 控制在 $70 量级 (vs 旧 $1,420)
- 触发 profit_protect 的 session 翻倍 (3 → 7)
- session_win_rate 从 11% 跳到 58% — 这是最有说服力的指标改善

不需要重新跑 P0/P1/P2 完整 sweep, 直接把 `TACTICAL_BASE_POSITION_RATIO` 默认从 0.0
改成 0.05 即可. 上层 anchor 不变.

---

## 3. 候选标的筛选 (`scripts/screen_symbols.py`)

### 方法

对每个候选 symbol 拉 yfinance 2y 1h 数据聚合到 4h. 在每个时间点 t 检查:
- ADX(t) < 25 (低趋势)
- (max High − min Low) of last 20 bars / ATR(t) >= 2.0 (S3 入场条件)

满足的时间点 = 假想 entry. 然后模拟 60-bar session, 检查:
- **successful_pct**: session 内是否出现 ≥1 次完整 BUY→SELL 配对 (price hit center-0.5×ATR then back to center+0.5×ATR)
- **zero_fill_pct**: 整段 60-bar 价格从未触及 BUY level
- **one_way_pct**: 触及 BUY level 但从未回到 center 上方

这是"上限指标" — 实际回测会因 commission / slippage / profit_protect / age timeout 等
原因把 successful_pct 打折. 但用同一套规则跨标的比较是公平的.

### 结果 (entries 数量代表"机会数", successful% 代表"机会兑现率")

| symbol | entries | succ% | rt/sess | zero% | one_way% | atr%med | $M/day | score |
|---|---|---|---|---|---|---|---|---|
| **UVXY** | 362 | **76.5** | 1.81 | 7.2 | 1.7 | **4.15** | 339.5 | **1.323** |
| SOXS | 500 | 71.4 | 1.54 | 5.6 | 2.6 | 4.75 | 6433.3 | 1.230 |
| SOXL | 505 | 73.3 | 1.50 | 10.9 | 0.8 | 4.57 | 5933.8 | 1.225 |
| **TQQQ** | 511 | **76.5** | **1.90** | 10.6 | 1.6 | 2.55 | 4442.8 | 1.213 |
| **SQQQ** | 504 | 73.8 | 1.84 | 5.2 | 2.2 | 2.64 | 3519.2 | 1.206 |
| UNG | 435 | 76.8 | 1.57 | 7.8 | 2.3 | 2.57 | 120.7 | 1.173 |
| VXX | 373 | 73.7 | 1.65 | 8.0 | 2.1 | 2.75 | 346.3 | 1.168 |
| SPXL | 397 | 74.6 | 1.71 | 11.6 | 2.0 | 1.82 | 648.5 | 1.099 |
| QQQ | 506 | 77.5 | 1.88 | 11.7 | 1.2 | 0.86 | 30556.8 | 1.089 |
| USO | 536 | 77.2 | 1.61 | 11.0 | 1.9 | 1.28 | 4002.0 | 1.073 |
| TLT | 566 | 77.6 | 1.75 | 8.1 | 1.4 | 0.61 | 2313.1 | 1.066 |
| SVXY | 359 | 73.3 | 1.49 | 6.7 | 1.1 | 1.35 | 113.8 | 1.043 |
| SPXS | 398 | 67.3 | 1.48 | 6.0 | 2.3 | 1.90 | 584.7 | 1.022 |
| SPY | 410 | 73.2 | 1.67 | 12.9 | 2.0 | 0.62 | 43578.6 | 0.982 |
| GLD | 393 | 77.4 | 1.42 | 15.0 | 1.0 | 0.67 | 4560.0 | 0.981 |

### 关键发现

1. **UVXY 是机会×波动的最优组合** (score 1.32 #1) — 虽然 entries 只有 362 (中等),
   但 ATR%中位 4.15% (最高), 单次 round-trip 利润空间最大.

2. **TQQQ / SQQQ 是流动性 + 节奏的现实替代** (score 1.21) — $4-3B/天的成交额是
   UVXY 13x, 适合多标的并行 / 大资金部署. successful_pct 与 UVXY 持平 (76-74%).
   缺点: ATR% 中位只有 2.5%, 单次 scalp 利润比 UVXY 小约 40%.

3. **SOXL / SOXS** (score 1.23, 1.22) — 半导体 3x 杠杆 ETF, ATR% 接近 UVXY 但
   流动性 17x 大. successful_pct 略低 (71-73%), 但量级靠谱.

4. **不推荐**: SPY (score 0.98) 太平; GLD (0.98) 单向爆炸概率高; SVXY (1.04)
   流动性差 ($114M/天); UNG (1.17) 流动性也偏低.

5. **统计反直觉**: 看似低波动的 QQQ / TLT 也有 77% successful% — 因为它们的
   "ranging" 节奏更稳定, 价格反复 cross center. 但 ATR%太低 (<1%), 单笔 net
   profit 难覆盖 commission, 实际回测大概率亏成本.

### 推荐 (按风险偏好排序)

| 偏好 | 主标 | 备选 |
|---|---|---|
| 当前思路延续 (高波动 vol ETF) | UVXY | VXX |
| 扩展到杠杆 ETF (流动性更好, 节奏类似) | TQQQ, SOXL | SQQQ, SPXL |
| 多标的组合 (相关性低) | UVXY + TQQQ + SOXL | + USO 做能源 leg |
| 不推荐 | — | SPY, GLD, SVXY |

### 下一步具体行动建议

1. **先把 `TACTICAL_BASE_POSITION_RATIO` 默认从 0.0 改成 0.05** (S4 已验证).
   单标的 UVXY 5y +0.64% / dd 4.6% / pf 0.86, 比当前 -2.38% 显著改善.

2. **跑 TQQQ 4h 数据 (本地拉一份 csv) 做 5y P0 单点回测**, 用 UVXY 当前 P0 top-1
   参数 + base=0.05, 看实际回测能否复现 screen 给出的 76% successful_pct.

3. **走 multi-symbol 路径前** (orchestrator + capital allocator 已就位), 至少
   先把 UVXY 单标的 ret 推到 +5% / pf > 1.0 才有意义扩展. 当前 +0.64% 还不到
   "确认 edge" 的程度.

---

## 4. 文件清单

代码改动:
- `tactical_config.py`: 新增 4 个配置项 (S1×2 + S2×1 + 保留 S4 ratio)
- `tactical_rules.py`: SessionStateView.recenter_used_count, should_disable_recenter
  rescue 分支, should_force_exit no_fill_timeout 分支
- `session_manager.py`: SessionContext.recenter_used_count, record_recenter()
- `grid_bot.py`: 在 _apply_dynamic_adjustment 里加 rescue_recenter 强制路径,
  调 record_recenter 计数
- `entry_filter.py`: _check_conditions 加 recent_range/ATR 校验
- `config.py`: ENTRY_RECENT_RANGE_LOOKBACK_BARS, ENTRY_MIN_RECENT_RANGE_ATR
- `scripts/tune_tactical.py`: classify_exit_reason 加 no_fill_timeout
- `scripts/screen_symbols.py`: 标的筛选工具 (新增)

实验产物:
- `reports/tuning/S1_S4_FIX_AND_SYMBOL_SCREEN.md` — 本文
- `/tmp/diagnose_p0.py`, `/tmp/diag2.py` — 之前 root cause 诊断
- `/tmp/ab_base_ratio.py` — S4 A/B 实测脚本
- `/tmp/screen_symbols.py` (已迁到 scripts/) — 标的筛选脚本

测试: `python test.py` → 245 tests OK

---

## 5. 测试覆盖 (未做)

**未补的测试** (建议下一步加):
- S1: `TestTacticalOffensiveRescueRecenter` — 验证零成交+age≥8 时 should_disable_recenter 返回 False
- S2: `TestSessionNoFillTimeout` — 验证 age≥6 + filled_buy_levels=[] 时 should_force_exit 返回 True
- S3: `TestEntryRecentRangeFilter` — 构造 20-bar 低 range 数据, 验证入场被拒

这三个测试可以参考已有的 `TestTacticalExitOverride` 写法. 当前 245 tests
覆盖了不被破坏 (regression-free) 的层面.
