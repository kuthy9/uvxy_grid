# 用户挑战的回应: 是的, 交易逻辑层有真实回归 — 找到并修了一个, 还有一个

date: 2026-05-14
方法: 用 `git worktree` 拿出原始 V49 baseline (commit `4fdb801`) 副本到
`/private/tmp/grid_v49`, 在两边跑同一份 5y UVXY 4h 数据, 直接对比.

---

## TL;DR

1. **V49 baseline (HEAD initial commit 代码) 5y 实测**:
   - **+109.91% / 5y, 年化 +15.01%, dd 8.88%, Sharpe 0.536, win_rate 84.6%**
   - 同一份 newer data 上跑出 — 不是历史声明, 是现场重跑.
   - PROJECT_STATUS.md (已从 HEAD 删除, 仍在 worktree 中) 记录 +103.78% / 年化 +15.34% / 1y 略老数据.

2. **当前代码 (今天) 用 V49 完全相同的参数, 关掉所有战术层, 跑同一份数据**:
   - 修复前: **-21.6%** (HARD_STOP 触发)
   - 修复 1 个 bug 后: **+49.05%** / 年化 +8.32% / dd 19.91%
   - 仍比 V49 worktree **少 60pp**.

3. **代码层 (不是交易逻辑层) 有一个 critical regression — 已修**, 还有一个区域回归.

---

## 1. 已修复的 critical regression

### 1.1 Bug 描述

`_should_check_dynamic_adjustment()` 的设计是"按 strategy bar (4h) 节奏检查动态调整",
有 side-effect: 调用一次会消耗内部 `_last_recenter_check` 计时器.

在战术化重构里, 这个函数被引入两个 caller (`_update_market_context` 和
`_handle_active_grid` step 5). 每个 bar 流程:
1. `_handle_active_grid` 进来
2. step 2 调 `_update_market_context` → 内部 `_should_check_dynamic_adjustment()` → True, 消耗计时器
3. step 5 再调 `_should_check_dynamic_adjustment()` → False (计时器刚刚被消耗)
4. **`_apply_dynamic_adjustment` 不被调用** — `grid_engine.should_exit` / `should_recenter` 全部哑火

### 1.2 影响范围

`_apply_dynamic_adjustment` 在整个 5y 回测里**只在 session 第一个 bar 跑一次**, 之后再
也不跑. 这导致:
- `grid_engine.should_exit` (ADX>22 / ATR%>7% / 价格偏离>4×ATR) **全部失效**
- `grid_engine.should_recenter` 全部失效
- 战术 override 模式下的 `record_engine_exit_signal` 全部失效
- S1 rescue_recenter 几乎失效

### 1.3 修复

`grid_bot.py`:
- 把 `_should_check_dynamic_adjustment` 拆成纯检查 `_strategy_bar_due()` 和消费
  `_consume_strategy_bar()` 两步.
- `_handle_active_grid` 在循环顶部 pure check 一次, 拿到 `bar_due` 布尔值, 同时
  传给 step 2 `_update_market_context(force_refresh=bar_due)` 和 step 5
  `if bar_due: _apply_dynamic_adjustment(...)` 两路, 在 step 5 之后才 consume.
- 保留旧名 `_should_check_dynamic_adjustment` (一次调用 = check + consume) 向后兼容,
  业务路径已切到新 API.

测试: `python test.py` → 245 OK.

### 1.4 修复实测

| 配置 | 修复前 ret | 修复后 ret | 改善 |
|---|---|---|---|
| 当前代码 + V49 参数 + TURBO=OFF | **-21.61%** (HARD_STOP) | **+49.05%** | **+70.66 pp** |
| 当前代码 + "tactical best" (T1+T3+S2+S3) | +6.09% | +6.09% | 0 |

第二行没变化是因为 S2 `no_fill_timeout=6` 在大多数 session 第 6 个 bar 就强制退出,
此时 `_apply_dynamic_adjustment` 是否能跑都已经无关.

---

## 2. 仍然存在的差距 (TURBO=OFF +49% vs V49 worktree +109%)

修复 bug 后还差 60pp. 不是单一 bug, 多个微小变化叠加:

### 2.1 同 commit V49 与当前 HEAD 之间的代码变化

`git log --oneline 4fdb801..HEAD -- grid_bot.py`:
```
8c41c62 daily_snapshots 写入时机解耦
e369447 WAITING_ENTRY 节拍与 STRATEGY_INTERVAL 解耦 + scanning→waiting 立即评估 + 非交易时段不消耗窗口
a0d082a WAITING_ENTRY 处理顺序修复 + 超时浮点边界保护
c4179d6 ibkr 取价 retry + waiting_entry 自动恢复 + 入场窗口收紧
4fdb801 Initial commit — V49 baseline
```

`scanning→waiting 立即评估` (commit e369447): 改变了 entry 时序. V49 worktree 有时
"WAITING_ENTRY → 1 bar 等待 → ACTIVE_GRID" (在 timing 失败重试时下一 bar 入场).
当前代码"WAITING_ENTRY → 同 bar ACTIVE_GRID". 入场价比 V49 略高 (没等到下一 bar
的 dip).

### 2.2 EXIT_GRID PnL 是关键差异

| Trade type | V49 worktree | 当前 (TURBO=OFF) |
|---|---|---|
| EXIT_GRID 笔数 | 13 | 21 |
| EXIT_GRID **sum_pnl** | **+$500.03** | **-$128.71** |
| GRID_SELL 笔数 | 26 | 35 |
| GRID_SELL sum_pnl | +$60.75 | +$75.96 |

V49 的 EXIT_GRID 几乎全是**向上方向的利润退出** (ADX 升过 22 时, 已经持仓在上行价位,
EXIT_GRID 落袋止盈). 当前代码的 EXIT_GRID 反而亏 — 时机晚一步或位置错.

具体差异需要更深 forensic, 但**主要原因是 entry 时序差异 + 多入了 9 个 session
(33 vs 24), 每多一个 session 多承担一次 entry 风险**.

---

## 3. TURBO=ON 战术层 vs TURBO=OFF 还要再亏 36pp

| 配置 | ret% | annu% | dd% | sessions |
|---|---|---|---|---|
| V49 worktree | **+109.91** | **+15.01** | 8.88 | 24 |
| TURBO=OFF, V49 legacy 路径 | +49.05 | +8.32 | 19.91 | 33 |
| **TURBO=ON, base=0.40, override=OFF, 无 S2/T3** | **+12.85** | **+2.45** | 18.10 | 61 |
| TURBO=ON, prev "best" (T1+T3+S2+S3, base=0.05) | +0.70 | +0.14 | 2.88 | 61 |

`TURBO=ON + V49-like params`: 61 sessions (V49 的 2.5x), 平均更短. **战术 session
管理把 V49 的长 session 切成 2.5 倍数量的短 session**, 每个都太短, grid 没时间
工作.

V49 单 session 平均 237 小时 (~10 trading days). 战术 session_max_age=40 bars =
160 小时 (~6 trading days). DEFENSIVE_GRID 又把 age>40 的 session 切掉 BUY,
进入"清算等待"模式. 实际有效 grid trading 时间更短.

**V49 让 session 活足够长, 是它能等到 ADX>22 把持仓在 UP-trend 的价位 EXIT_GRID
止盈的前提**. 战术 SOFT_STOP/HARD_STOP/AGE 全部都让 session 早退, **吃掉了 V49 的 edge
来源**.

---

## 4. 为什么原始版本能年化 15% (用户记忆中"19%")

数据答案 (本次重跑出来, 不靠记忆):
- 旧 PROJECT_STATUS.md (HEAD 中已删) 记录 V49: 总收益 +103.78%, **年化 +15.34%**,
  Sharpe 0.545, MDD 8.88%, 胜率 83.8%, 37 round trips, 5 年.
- worktree 现在跑同样代码 + newer data: 总收益 +109.91%, 年化 +15.01%.
- 数字本身可复现, 不存在"造假".

**+15.34% (≈ 19% 模糊记忆) 的成因**:
1. **40% BASE position** 长期持有, 在 UVXY 偶发暴涨段 (2021 H1 / 2022 H2 / 2024 H2) 捕获 carry
2. **长 session 平均 237 小时**, grid 有时间 fill 多档 BUY, 等反弹 SELL 配对
3. **`grid.should_exit` 真的在工作**:
   - 13 次 EXIT_GRID, 平均 +$38/笔, 都是 ADX>22 时的趋势确立 → 把持仓在高位卖
   - 多次 4-6×ATR 价格偏离触发, 在过高位置 take profit
4. **没有"过度风控":** 没有 SESSION_MAX_AGE 强制 timeout, 没有 SOFT_STOP 早早进
   DEFENSIVE, 没有 S2 6-bar 早退. 让趋势自然走完.

**当前系统的 -100pp 退化分两个层面**:

| 来源 | 损失 (pp) |
|---|---|
| 已修的 `_should_check_dynamic_adjustment` 双消费 bug | **~70 pp** (-21% → +49%) |
| 入场时序变化 (`scanning→waiting 立即`) + 入场窗口收紧 | ~10-20 pp |
| 战术 session 风控 (SOFT/HARD/AGE) 把长 session 切碎 | **~36 pp** (TURBO=ON +13% vs OFF +49%) |

---

## 5. 答案归纳

**用户问 "代码层没有问题, 是不是交易逻辑层?"** — **NO, 代码层有真实 bug**.

修了一个 (rate-limit 双消费) 之后还差 60pp. 剩下的 60pp 里:
- 一部分是其他代码改动 (waiting_entry 时序) 的副作用
- 大部分是**战术设计理念**和**原始网格**根本冲突: 战术想用 SOFT/HARD/AGE 风控
  限制单次损失, 但 V49 的 edge 恰恰是"忍着浮亏 → 等趋势确立 → 高位 EXIT_GRID 止盈".
  这两套机制是**对立的**, 不是叠加的.

**用户问 "为什么原始能 19% 年化"** — 实测 V49 是 **15.34% 年化**, 不是 19%, 但
是真实的, 可复现的. 来自:
- 40% 重底仓 + 长持
- 让 grid 实际工作 (没被无谓的 early-exit 杀掉)
- `should_exit` 真在 ADX 阈值生效, 把 trend 段当成 take-profit 信号而非 "止损信号"

**当前最佳战术配置 (+6%)** 是双重损失叠加的产物:
1. 代码 bug 让 should_exit 不响应 (修后才看清楚 +49% 而非 -21%)
2. 战术风控把 V49 的关键 hold 机制扼杀

---

## 6. 推荐下一步

1. **立刻**: 接受 V49 (TURBO=OFF) 才是 baseline. 当前 TURBO=ON 战术层是 net 减分.
2. **诚实重设**: 把 `TURBO_ENABLED` 默认改成 `False`, 让系统回到 V49 行为. 战术层
   作为可选实验, 不是主路径.
3. **如果坚持战术化**: 必须把 SESSION_MAX_AGE / SOFT_STOP / no_fill_timeout 这些
   "限制 session 寿命" 的开关全部**关掉或大幅放宽**, 让 session 像 V49 一样
   能活 237 小时以上. 让 `grid.should_exit` 主导退出.
4. **不要再做战术参数 sweep**: 在当前架构下战术参数有上限 — 拿掉 60pp 战术回归
   都拿不回去 V49 baseline. 任何 sweep 都是在低于 baseline 的层做局部优化, 不增加
   绝对 edge.

---

## 7. 文件清单

代码改动:
- `grid_bot.py`: 拆 `_should_check_dynamic_adjustment` 为 pure check + consume,
  `_handle_active_grid` 顶部 single check, 共享给 step 2 和 step 5.
- `grid_bot.py`: `_update_market_context` 加 `force_refresh` 参数.

诊断 (`/tmp/`):
- `run_v49_persist.py` — V49 worktree 跑出来
- `current_code_v49_params.py` — 当前代码 + V49 参数
- `instrument_test.py` — 找到 `_apply_dynamic_adjustment` 只被调 1 次的关键证据
- `compare_legacy_v49.py` — V49 vs 当前 legacy 逐 trade 对比
- `post_fix_matrix.py` — 修复后的 4 cell 矩阵

报告:
- `reports/tuning/REAL_REGRESSION_FOUND.md` — 本文

测试: 245 OK
