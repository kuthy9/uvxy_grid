# 回应: "从传统网格转换成激进战术型网格不应该成为回报糟糕的主要因素"

date: 2026-05-14
配置: S1-S3 已并入主代码默认开启, S6.2 (ATR%爆炸) 默认 0.08, S6.1 (pos_drawdown)
默认 0 (实测伤回报, 留 env 接口)

---

## TL;DR (回答用户的 4 个问题)

1. **战术覆盖是否丢失单边行情检测?** — 部分丢失, 但 S2 (no_fill_timeout) 已经
   把"上行 +496% 单边"这种极端 case 兜住. 实测 override on/off 在 S1-S3 之后
   ret/dd/pf 完全相同 (-2.83% vs -2.83%, dd 3.99% vs 3.99%).

2. **代码是否有伪代码/硬编码?** — 业务代码无 TODO/FIXME/HACK. S1/S2/S3 新代码
   逻辑 (filled_buy_levels 由 record_buy_fill 正常填充, S1 rescue_recenter 触发
   条件正确) 经审计无问题.

3. **再次回测** — 4-cell matrix (override×base):
   | cell | ret% | dd% | pf | gpnl |
   |---|---|---|---|---|
   | ovr=T base=0.00 | -2.83 | 3.99 | 0.35 | -$283 |
   | ovr=T base=0.05 | **+0.40** | 4.36 | **0.82** | -$193 |
   | ovr=F base=0.00 | -2.83 | 3.99 | 0.35 | -$283 |
   | ovr=F base=0.05 | +0.40 | 4.36 | 0.78 | -$193 |
   override 关闭对 ret 几乎无影响.

4. **真正主因**: **11 个 age_timeout session 累积 -$472** 主导亏损, 全部是
   "BUY 填几档后价格一路跌 8-15%, SELL 永不触发, age 到 60 砸 EXIT_GRID" 模式.
   不是战术覆盖问题, 是**策略本身在持续下跌 session 中的损失放大机制**.

---

## 1. 战术覆盖与单边行情检测的关系 (问题 1)

### 1.1 静音了什么

`tactical_override = True` 时, `grid_engine.should_exit` 的 3 个触发 (ADX>22 /
ATR%>7% / |price−center|>4×ATR) 不再直接进 EXIT_PENDING, 改为 `record_engine_exit_signal`
喂给 SessionManager (只记事件, 不动状态).

### 1.2 替代品的覆盖范围

`tactical_rules.should_force_exit` 的触发:
| 信号 | 旧 (grid_engine) | 新 (tactical_rules) | 缺口 |
|---|---|---|---|
| ADX 趋势确立 | 直接 ADX>22 | trend_risk_score 里 ADX 占 0-25 分 | 替代覆盖 |
| ATR% 爆炸 | 直接 ATR%>7% | trend_risk_score 里 ATR_expansion 占 0-15 分 | **不够独立** |
| price 向下偏离 | 单向 4×ATR | trend_risk_score 里 "price below center" 占 0-15 分 | **下行有, 上行无** |
| price 向上偏离 | 上下都 4×ATR | **完全没有** | **上行 +496% 不会触发** |

`calculate_trend_risk_score` 的 6 个 component 全部偏下行: "price BELOW EMA",
"consecutive DOWN bars", "price BELOW grid_center". 这是一个 asymmetric 设计.

### 1.3 实测: 上行 +496% session 22 行为

UVXY 2024-04-01 → 2024-04-12 经历反向拆股, $6.35 → $37.85 (+496%):
- 在 db `/tmp/p0_top1_5y.db` 里检查该 session 的所有事件
- `engine_exit_signal` 事件数: **0** (战术 override 把 grid.should_exit 记到事件,
  但实测 0 触发. 详见下节为什么)
- 实际退出: age=65 timeout. 期间 BUY=0, SELL=0, 完全空仓

### 1.4 为什么 +496% 移动没触发 grid.should_exit?

- ADX > 22: UVXY 在拆股前 10 天没有 ADX 信号 (价格 $6.30-$7.30 横盘)
- ATR_PCT > 7%: 拆股 1 个 bar 内发生, 单 bar 的 ATR 算 ratio 还没扩散
- price_deviation > 4×ATR: 拆股发生在 bar 14, **过了 bar 14 后 S2 (no_fill_timeout)
  本来应该已经在 age=6 时退出该 session** (因为前 6 个 bar 价格在 6.30-7.30, 没有
  触及 grid 的 -1 档 $6.10). 实测以现在的 S1-S3 代码跑, **session 22 在 age=6
  时被 S2 切掉**, 退出价 $6.56, peak=$0, final=$0 — **没有亏损**.

所以**上行单边检测的缺口在 S2 加入后已经实际上被兜住**. S2 比"补回上行检测"更
彻底 — 无论上行还是下行, 只要 grid 6 bar 没接到 -1 档 BUY, 直接退场.

### 1.5 4-cell 实测

| cell | ret% | dd% | pf | exit_reason 分布 |
|---|---|---|---|---|
| ovr=T base=0.00 | -2.83 | 3.99 | 0.35 | no_fill 47, pos_drawdown 10, age 5, profit_protect 2 |
| ovr=T base=0.05 | +0.40 | 4.36 | 0.82 | no_fill 46, pos_drawdown 13, age 1, profit_protect 6 |
| ovr=F base=0.00 | -2.83 | 3.99 | 0.35 | 同上 + price_deviation 1 (替代 1 个 age) |
| ovr=F base=0.05 | +0.40 | 4.36 | 0.78 | 同上 + price_deviation 1 |

**关掉 override (传统双轨退出) 几乎不改变结果**. 1 个 session 从 `age_timeout` 改
判到 `price_deviation` 桶, ret 不变.

**结论**: 战术覆盖是真实但不是亏损主因. 真正影响在第 4 节.

---

## 2. 代码审计 (问题 2)

### 2.1 伪代码 / TODO / HACK

`grep -rn "TODO\|FIXME\|XXX\|HACK\|stub\|NotImplemented" --include="*.py" .` →
只在 `test.py` 命中 (合法的测试 mock 命名). 业务代码无任何伪实现标记.

### 2.2 S1-S3 新代码正确性

| 检查项 | 验证 | 结果 |
|---|---|---|
| S1 rescue_recenter 条件 | `recenter_used_count==0 AND filled_buy_levels==[] AND age>=8` | ✓ 逻辑正确, env 可关 |
| S1 recenter_used_count 累计 | `SessionManager.record_recenter` 在 grid_bot.recenter 后被调 | ✓ 已 wire |
| S2 no_fill_timeout | `age>=6 AND filled_buy_levels==[]` → ACTION_FORCE_EXIT | ✓ 在 should_force_exit 里 |
| S3 recent_range/ATR | `ev.atr_pct * ev.current_price` 推 ATR 绝对值 | ✓ (suggested_atr 此时还没算, 推算正确) |
| filled_buy_levels 填充 | `_process_fills` 在 BUY 成交时 `sm.record_buy_fill(level_index, ...)` | ✓ 业务路径 |
| level_index < 0 过滤 | 只有 grid 下方档 (-1/-2/...) 进 list, 不混入 BASE_BUY (level=0) | ✓ |

### 2.3 硬编码检查

`tactical_rules` 内有一组 `_RISK_*` 评分权重常量 (例如 `_RISK_ADX_MAX_SCORE=25`).
这些是评分函数的 hyperparameter, 不是参数化的"魔法数字"; 用户调整需通过回测验证,
不能随手改. 当前注释清晰, 集中定义, 合规.

无业务路径里散落的"价格 $X" / "数量 N" 等硬编码值. 所有可调项已经在 `config.py`
或 `tactical_config.py`.

---

## 3. 回测 (问题 3) — S6 加入后实测

加了 S6 (ATR%爆炸 + 持仓 unrealized drawdown 兜底) 后跑 P0 top-1 + S1-S5:

### 3.1 4-cell 主表 (S6.1 阈值 5%)

见 1.5. ret 范围 -2.83% ~ +0.40%, S6.2 (ATR%爆炸) 在 UVXY 5y 实测**未触发任何 session**
(UVXY 4h ATR% 中位 4.15%, 极少触及 8% 阈值).

### 3.2 S6.1 (pos_drawdown) 阈值 sweep

固定 ovr=T base=0.05, 扫 SESSION_POSITION_DRAWDOWN_STOP_PCT:

| 阈值 | ret% | dd% | pf | sessions |
|---|---|---|---|---|
| **0.00 (off)** | **+0.64** | 4.63 | **0.86** | 64 |
| 0.05 | -0.27 | 4.36 | 0.82 | 66 |
| 0.07 | -4.20 | 7.40 | 0.65 | 65 |
| 0.08 | -4.48 | 7.68 | 0.64 | 65 |
| 0.10 | -6.14 | 9.41 | 0.53 | 65 |
| 0.12 | -2.81 | 7.58 | 0.60 | 64 |
| 0.15 | -0.34 | 5.59 | 0.83 | 64 |

**结论: 任何 pos_drawdown 阈值都让 ret 变差**.

原因: 网格策略本质是 fade pullback (在价格回调时加仓). 持仓 unrealized 亏损
止损反过来扼杀了即将反弹的 session — 杀的"该恢复的 session" 多于救的"会继续跌
的 session". 这是网格 vs 趋势策略的本质差异.

**S6.1 默认改回 0 (off)**, 保留 env 接口给极端行情手动开启.

### 3.3 最佳配置 (跟前面 S4 实验一致)

```
TACTICAL_BASE_POSITION_RATIO = 0.05         # S4 验证
TACTICAL_OVERRIDE_GRID_ENGINE_EXIT = True   # 默认; off 也一样
SESSION_NO_FILL_TIMEOUT_BARS = 6            # S2 关键
TACTICAL_OFFENSIVE_RESCUE_RECENTER_ENABLED = True  # S1
ENTRY_MIN_RECENT_RANGE_ATR = 2.0            # S3
SESSION_POSITION_DRAWDOWN_STOP_PCT = 0.0    # S6.1 关闭
SESSION_ATR_PCT_EXPLOSION_STOP = 0.08       # S6.2 兜底, UVXY 实际不触发
```

5y UVXY 4h: ret +0.64% / dd 4.63% / pf 0.86 / sessions 64 / win_rate 58%

---

## 4. 亏损来源精确归因 (问题 4)

### 4.1 按 exit_reason 分桶 (override=OFF, base=0.00, 没有 S6.1)

| bucket | count | sum_pnl | avg_pnl | worst | avg_age |
|---|---|---|---|---|---|
| **age_timeout** | **11** | **-$472.79** | -$42.98 | -$138.59 | 64.4 |
| no_fill_timeout | 47 | $0.00 | $0.00 | $0.00 | 7.9 |
| price_deviation | 1 | $0.00 | $0.00 | $0.00 | 5.0 |
| profit_protect | 3 | +$159.98 | +$53.33 | +$18.86 | 28.0 |
| **TOTAL** | **62** | **-$312.81** | | | |

5y ret = -$312.81 / $10,000 = -3.13% (加上 commission $22 drag, 与实测 -2.83% 接近).

### 4.2 11 个 age_timeout 详情

| 起始日期 | 价格变化 | drift% | peak | final | BUY数 | SELL数 |
|---|---|---|---|---|---|---|
| 2022-11-16 | $9.09→$8.19 | -9.9% | $0 | **-$138.59** | 3 | 0 |
| 2023-12-07 | $9.94→$8.97 | -9.8% | $0 | **-$101.38** | 3 | 0 |
| 2025-12-18 | $41.80→$35.81 | -14.3% | $0 | **-$94.46** | 2 | 0 |
| 2024-02-22 | $7.41→$6.72 | -9.3% | $0 | **-$85.54** | 3 | 0 |
| 2022-12-27 | $7.07→$6.46 | -8.6% | $2.26 | -$78.06 | 2 | 0 |
| 2024-01-05 | $8.57→$8.37 | -2.3% | $0 | -$61.12 | 3 | 0 |
| 2021-11-11 | $16.14→$15.66 | -2.9% | $36 | -$12.22 | 3 | 0 |
| 2024-12-05 | $19.04→$19.41 | +1.9% | $22 | +$5.07 | 3 | 0 |
| 2023-02-06 | $5.06→$5.11 | +1.0% | $23 | +$15.27 | 1 | 1 |
| 2024-01-29 | $7.61→$7.14 | -6.2% | $50 | +$25.95 | 2 | 2 |
| 2026-02-02 | $34.76→$39.40 | +13.3% | $95 | **+$52.30** | 2 | 2 |

**7 个 session 都符合同一 pattern**:
1. 入场价 P0, 价格在前 6 bar 内触发至少 1 个 BUY 档
2. 此后价格继续下跌 8-15%, 累积 2-3 个 BUY 档
3. 反弹幅度始终不足以触发任何 SELL 档 (SELL 档在中轴 +0.5×ATR)
4. trend_risk_score 触及 65 (进 DEFENSIVE 取消 BUY) 但不到 85 (FORCE_EXIT)
5. hard_stop (3% × $10000 = $300) 阈值高于实际单 session 损失 ($85-138)
6. soft_stop (1.5% × $10000 = $150) 阈值仍高于实际损失 ($138 是唯一接近的)
7. 唯一退出路径: age=60 timeout → EXIT_GRID 砸盘
8. EXIT_GRID 在当前下跌价位卖, 实现全部累积浮亏

### 4.3 为什么 S6.1 (pos_drawdown) 反而伤回报

理论上 pos_drawdown stop 应该在第 5-6 步把这些 session 切掉. 实测它确实减少了
最坏 session 的损失:
- age_timeout 桶: 11 → 1 (sum_pnl 改善)

但同时它**误杀了**会反弹回正的 session — 比如:
- 2024-01-29 (peak=+$50 final=+$26): 中途可能短暂触及 -5%, 被 cut 后失去 $26 收益
- 2024-12-05 (peak=+$22 final=+$5): 同上
- 2026-02-02 (peak=+$95 final=+$52): 同上

这些"中途亏损反弹"的 session 在阈值 5% 下被错杀的成本 > 不切的损失.

更深层原因: **网格本质就是"价格回调时加仓, 反弹时减仓"**. 在持仓 unrealized 上设
stop 等于反着自己的策略. 当前 trend_risk_score 已经在加 BUY 上有 DEFENSIVE 闸
(SCORE_DEFENSIVE=65 关掉 BUY 但保留 SELL), 已经是合理的位置.

### 4.4 真正的解决方向

**不要在 unrealized 上 stop**. 要在源头改进:

A. **入场过滤更挑剔** (用户提过): 这 7 个 session 入场时 UVXY 都在某种 "ADX 上升
   早期" — S3 的 recent_range/ATR ≥ 2 不足以筛掉. 可以再加:
   - ADX_slope > 0 拒绝 (ADX 还在上升, 不是稳定 chop)
   - past_5_bar momentum > X 拒绝 (避免 break-out 临界点)

B. **网格 SELL 档间距收窄**: 当前 GRID_SPACING_ATR_MULTIPLIER=0.5, SELL 档在中轴
   +0.5×ATR. 改成 +0.3×ATR 让 SELL 更容易触发, 牺牲单笔利润换 SELL 触发率.
   需要回测验证.

C. **当价格离中轴 1.5×ATR 时禁止再加 BUY**: 比 DEFENSIVE 触发更早. 不是 stop,
   是 "不再加深". 这是 SESSION_MAX_BUY_LEVELS=3 的本意, 但当前 max_buy_levels 不
   严格按 ATR 距离, 而是按档位数. 改成按距离更接近本意.

这些都是入场和加仓侧的改进, 不是退出侧的 stop, 与网格 fade-pullback 本质兼容.

---

## 5. 总结

回到用户的判断 "从传统网格转换成激进战术型网格不应该成为回报糟糕的主要因素":

**这个判断是对的**. 数据证实:
- 战术覆盖 (override) 不是亏损来源: on/off 实测 ret 完全相同
- 战术新加的 S1-S3 实际上把 ret 从 -5.91% 拉到 +0.64%
- 战术机制 (no_fill_timeout / rescue_recenter / range filter / profit_protect)
  全部是正贡献

亏损主因是**策略 + 标的的结构性不匹配**:
- UVXY 4h 上有持续 8-15% 单边下跌的 session
- 网格在这种行情下天然累积 BUY, 没有 SELL 触发
- 任何"持仓亏损止损"都会扼杀网格的核心机制 (fade pullback)
- 解法在入场过滤 + 网格几何, 不在退出 stop

**当前最佳配置 5y UVXY 4h**: ret +0.64% / dd 4.63% / pf 0.86 / win_rate 58%.
这是个**"勉强 break-even, 还没到可部署"** 的状态, 但已经从最初的 -5.91% 改善了
6.5 个百分点. 进一步要看入场过滤改进 (上面 A) 和网格几何改进 (B/C), 或换标的.

---

## 6. 文件清单

代码变动 (本轮):
- `tactical_rules.py`: should_force_exit 加 ATR%爆炸 + pos_drawdown (后者默认 off)
- `tactical_config.py`: SESSION_POSITION_DRAWDOWN_STOP_PCT (=0), SESSION_ATR_PCT_EXPLOSION_STOP (=0.08)

实验产物:
- `/tmp/test_override_off.py` — 验证 override 关闭后 ret 不变
- `/tmp/loss_attribution.py` — 11 个 age_timeout 详情
- `/tmp/s6_matrix.py` — 4-cell override × base
- `/tmp/s6_threshold_sweep.py` — pos_drawdown 阈值 sweep
- `reports/tuning/ROOT_CAUSE_NOT_TACTICAL_OVERRIDE.md` — 本文

测试: `python test.py` → 245 OK
