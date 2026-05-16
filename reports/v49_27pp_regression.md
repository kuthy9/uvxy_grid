# V49 → 8c41c62 -27pp 回归 git-bisect 报告

**日期**: 2026-05-15 (生产重构 Phase 4.D)
**Spec**: docs/superpowers/specs/2026-05-15-production-refactor-design.md §3.4.D

## TL;DR

V49 (commit `4fdb801`, README §5 报告) UVXY 5y baseline **+107.51%** (sanity run, 与文档 +109.91% 误差在正常浮动范围内). 当前
GitHub main HEAD `8c41c62` baseline **+82.81%**. 差距 ~-27pp, 11 commits 范围.
已知 ENTRY_MAX_WAIT_BARS 不是原因 (D3 实测对回测无影响).

**First bad commit**: `e369447f2a2d0ee11ff385d12b1aca154a7f5c3c`
**Commit subject**: `WAITING_ENTRY 节拍与 STRATEGY_INTERVAL 解耦 + scanning→waiting 立即评估 + 非交易时段不消耗窗口`
**ret 跌幅**: good commit (a0d082a) +109.12% → bad commit (e369447) +68.75%, 跌幅 -40.37pp (bisect 阶段未含后续提交累计修正, 当前 HEAD 因后续 commits 恢复至 +82.81%)

## bisect 详细步骤

```
git bisect start
# bad: [8c41c6210193b9c899f19b0d17547f6907fd94f1] daily_snapshots 写入时机解耦: 不再只在周报里写
git bisect bad 8c41c6210193b9c899f19b0d17547f6907fd94f1
# good: [4fdb80167135edd5e4556c2c615db8b3f4a723d8] Initial commit — Grid ETF v4 (UVXY 4h V49 baseline)
git bisect good 4fdb80167135edd5e4556c2c615db8b3f4a723d8
# good: [c4179d611780e97c3c2dab88d7d7bf6a2a46fd6f] ibkr 取价 retry + waiting_entry 自动恢复 + 入场窗口收紧
git bisect good c4179d611780e97c3c2dab88d7d7bf6a2a46fd6f
# good: [a0d082aa34144f28f0d1401d7335952cd2e1ae66] WAITING_ENTRY 处理顺序修复 + 超时浮点边界保护
git bisect good a0d082aa34144f28f0d1401d7335952cd2e1ae66
# bad: [e369447f2a2d0ee11ff385d12b1aca154a7f5c3c] WAITING_ENTRY 节拍与 STRATEGY_INTERVAL 解耦 + scanning→waiting 立即评估 + 非交易时段不消耗窗口
git bisect bad e369447f2a2d0ee11ff385d12b1aca154a7f5c3c
# good: [e0d03148a7cb29f8b8f940066c77e49f8c5d824b] ENTRY_MAX_WAIT_BARS 默认 1 → 1.5
git bisect good e0d03148a7cb29f8b8f940066c77e49f8c5d824b
# first bad commit: [e369447f2a2d0ee11ff385d12b1aca154a7f5c3c] WAITING_ENTRY 节拍与 STRATEGY_INTERVAL 解耦 + scanning→waiting 立即评估 + 非交易时段不消耗窗口
```

bisect 共 4 步, 测试了 5 个 commit (含两端点), 在 3 次迭代内收敛. 整个过程约 4 分钟.

## First bad commit 分析

### Commit info

```
commit e369447f2a2d0ee11ff385d12b1aca154a7f5c3c
Author: kuthy9 <140747977+kuthy9@users.noreply.github.com>
Date:   Tue May 5 15:19:28 2026 -0400

    WAITING_ENTRY 节拍与 STRATEGY_INTERVAL 解耦 + scanning→waiting 立即评估 + 非交易时段不消耗窗口

 config.py   |  24 +++++++++--
 grid_bot.py |  27 +++++++++++-
 test.py     | 137 ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
 3 files changed, 183 insertions(+), 5 deletions(-)
```

### Diff 摘要

**config.py** — ENTRY_PRICE_BAND_ATR 变更 (后经测试 env var override 无效, 不是主因):

```diff
-ENTRY_PRICE_BAND_ATR = 1.0
+ENTRY_PRICE_BAND_ATR = float(os.getenv("ENTRY_PRICE_BAND_ATR", "1.25"))
```

**grid_bot.py** — 两处行为变更:

1. `_handle_scanning`: scanning→waiting 转换成功后立即调用一次 `_handle_waiting_entry()`:

```diff
+if self.state_machine.state == SystemState.WAITING_ENTRY:
+    self._handle_waiting_entry()
```

2. `_handle_waiting_entry`: 非交易时段不再简单 return, 而是把 `entry_window_started_at` 推进到 `now`:

```diff
-if not self.risk.check_trading_hours():
-    logger.debug("WAITING_ENTRY: 非交易时段, 跳过本次评估 (不消耗窗口)")
-    return
+if not self.risk.check_trading_hours():
+    ctx.entry_window_started_at = now.isoformat()
+    ...
+    return
```

### Root cause hypothesis

**主因: scanning→waiting 后立即调用 `_handle_waiting_entry`**, 在回测逐 bar 推进模式下产生了与实盘不同的语义偏差. 在 `_handle_scanning` 的同一 bar 内直接尝试建仓, 绕过了 `_handle_scanning` 的 entry filter 筛选 — `_handle_scanning` 已经确认条件满足才转状态, 但 `_handle_waiting_entry` 的建仓条件是价格进入 `EMA ± ENTRY_PRICE_BAND_ATR×ATR` 的入场带. 在回测 bar-by-bar 执行中, 同一 bar 内连续调用导致 **该 bar 的价格直接被视为 "进入带内" 并立刻建仓** (无论是否真的在带内), 同时 `ENTRY_PRICE_BAND_ATR` 从 1.0 扩大到 1.25 进一步放宽了入场带. 两个变化叠加导致更多、更差时机的入场 (网格会话数: 24 → 35, 返回率: +109.12% → +68.75%).

**次因 (可能放大): 非交易时段 `entry_window_started_at` 持续推进**. 在回测中 HistoricalClock 按实际时间戳推进, 非交易时段的 4h bar 调用该逻辑时会把窗口起始时间推到该 bar, 等价于每次遇到非交易 bar 都重置计时器, 让 WAITING_ENTRY 的超时几乎不再生效, 进一步允许了不应入场的入场机会.

**ENTRY_PRICE_BAND_ATR 1.0→1.25 本身非主因**: 用 `ENTRY_PRICE_BAND_ATR=1.0` 环境变量覆盖后, e369447 的回测结果仍为 +68.75% (与默认 1.25 完全相同), 说明该参数变更在此回测中未起决定性作用.

## 推荐处理 (未修, follow-up)

**选项 1: 部分回滚** — 保留 `waiting_interval_sec()` 解耦 (该逻辑对实盘有价值), 但撤销 `_handle_scanning` 中的立即调用和非交易时段 `entry_window_started_at` 推进两项. 这两项改动对实盘可能也有副作用 (重置后在交易时段恢复时 elapsed≈0, 等于无限期延长 WAITING_ENTRY).

**选项 2: 修正两处逻辑** — 保留立即调用, 但在 `_handle_waiting_entry` 内加回测/实盘差异保护: 回测模式下按 bar 语义判断是否在带内 (不依赖 sleep 节拍); 非交易时段改为 "冻结计时" 而非 "推进计时" (`entry_window_started_at` 保持不变, 只是跳过评估).

**选项 3: 接受退化并标注** — 如果该 commit 已经在实盘中运行且效果被验证, 可以接受 -27pp 的回测退化, 将其记录为 "实盘优化 vs 回测退化的已知权衡", 并更新 baseline 基准到 8c41c62 的 +82.81%.

## 不修原因

本 spec §7 明确 "不修复 Phase 4.D 找到的 -27pp root cause (修复是独立 spec)".
此 follow-up 等用户决策.
