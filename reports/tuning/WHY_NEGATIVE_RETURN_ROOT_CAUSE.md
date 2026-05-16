# 为什么 total_return 仍是负数 / timeout 全屏 / profit_protect 不触发 — 根因分析

date: 2026-05-14
data source: P0 sweep top-1 配置 5y 单次回测, 持久化到 `/tmp/p0_top1_5y.db`
config: TACTICAL_BASE_POSITION_RATIO=0.0, TACTICAL_OVERRIDE_GRID_ENGINE_EXIT=True

---

## 1. 现象层 (三个表面问题)

| 现象 | 数值 |
|---|---|
| 5y total_return | **-5.91%** |
| timeout_exit | 32/39 sessions (82%), 加上 candidate v1 是 60/61 (98%) |
| profit_protect 触发 | 7/39 sessions (18%) |

这三个不是独立现象, 是同一根因的不同侧面.

---

## 2. 把 39 个 session 按行为分桶 (5y P0 top-1 实测)

| 桶 | 占比 | 含义 | 财务结果 |
|---|---|---|---|
| **零成交 session** (grid_buys=0 AND grid_sells=0) | **10/39 = 26%** | 入场后价格没碰到 -1 档, 全程空仓直到 age timeout | peak=$0, final=$0, 只损 commission ≈ $0 |
| **BUY 多 / SELL 零** (grid_buys>0, grid_sells=0) | **19/39 = 49%** | BUY 填进来后价格不反弹回中轴上方, SELL 全程未触发, 最后 EXIT_GRID 砸盘清仓 | peak 多为 $0, final $-40 ~ $-160 |
| **真 mean-revert** (grid_buys>0 AND grid_sells>0) | **10/39 = 26%** | grid 真正按设计工作: BUY 填→反弹→SELL 配对 | peak $15 ~ $280, 其中 7 个走 profit_protect |

**75% 的 session (零成交 + 单边 BUY) 是策略前提失效的 session.** 只有 26% 真正在
做"网格 scalp".

---

## 3. peak_pnl 分布 (为什么 profit_protect 不触发)

| peak 区间 | session 数 | 占比 |
|---|---|---|
| peak = $0 (从未浮盈) | 19/39 | **49%** |
| 0 < peak < $50 | 9/39 | 23% |
| $50 ≤ peak < $100 (>= profit_protect 门槛) | 9/39 | 23% |
| peak ≥ $100 | 2/39 | 5% |

- `SESSION_MIN_PROFIT_TO_PROTECT_PCT = 0.005` → 门槛 = $10,000 × 0.5% = **$50**
- 只有 **11/39 (28%) 的 session 整个生命周期内 peak 超过 $50**
- 其中 7 个真的走 profit_protect 退出, 另外 4 个: peak 短暂越过 $50 但
  `TRAILING_GIVEBACK_RATIO = 0.50` 要求 peak 回吐 50% 才触发, 那 4 个等不到 50%
  giveback 就先被 age timeout 切了

**所以 profit_protect 不是"逻辑没生效", 是"达到门槛的样本太少"**.

---

## 4. start_price → end_price 漂移分布

5y 39 sessions 的价格漂移 (entry → close 之间 UVXY 价格变化):

| 区间 | 数量 |
|---|---|
| 下跌 session | 24/39 (62%) |
| 上涨 session | 15/39 (38%) |
| 真"flat" (|drift| < 0.5%) | **0/39 (0%)** |

中位漂移 -3.07%, 均值 +14.01% (右尾被 2024-04-01 的 +496% 拉爆).

**关键发现**: 整个 5y 样本里, 没有一个 session 是真正"横盘震荡"的. UVXY 在 4h 周期上
要么 trending up, 要么 trending down — 没有 grid 设计需要的"range-bound" 区间.

漂移极端例子:

| session | age | start→end | peak | final | 网格行为 |
|---|---|---|---|---|---|
| 2024-04-01 | 65 | $6.35 → $37.85 (+496%) | $0 | $0 | grid 锚定 $6.35, 价格直接跑掉 — 0 成交 |
| 2026-02-26 | 65 | $37.75 → $46.25 (+22%) | $0 | $0 | 同上, 单边上行 — 0 成交 |
| 2023-09-19 | 77 | $13.35 → $16.30 (+22%) | $0 | $0 | 同上 |
| 2023-03-01 | 55 | $5.09 → $6.32 (+24%) | $280 | $110 | **唯一一个上行 session 中网格还能工作**: 价格反复震荡上行, 抓到 mean-revert |

---

## 5. 根因 (4 条互相耦合的结构性问题)

### A. 网格 entry 时 anchor, OFFENSIVE 期间禁止 recenter

`tactical_config.py:138`:
```python
TACTICAL_RECENTER_ALLOWED_IN_OFFENSIVE = _env_bool(
    "TACTICAL_RECENTER_ALLOWED_IN_OFFENSIVE", False
)
```

这是上一阶段为"避免 recenter 续命失败 session"加的策略性禁令. 副作用是:
**只要 entry 时刻的 grid_center 不对, 整个 session 就废了**. 价格只要往一个方向
走 > 1.5×ATR, 整个一边的 levels 永远填不上.

UVXY 4h ATR 大约 3-5%, 单 session 65 bars (~ 11 天) 价格平均能走 10-30%. 远超
±1.5 ATR 的网格范围.

### B. 入场过滤的反向选择

`ENTRY_MAX_ADX=25` (P0 用的 20) + `ATR%↑` 意味着"低 ADX, 高波动". 但低 ADX 后
经常**正在转入 trend**, 入场后立即趋势确立. 24/39 session 是下行, 15/39 上行,
0/39 真 flat — entry 信号在统计上选择的是"将要 break 出来的 chop", 而不是
"持续 chop". 这是结构性 selection bias, 不是参数调整能解决的.

### C. profit_protect 门槛 + giveback 比率的相互作用

- `MIN_PROFIT_TO_PROTECT_PCT = 0.005` → peak 必须 ≥ $50 才进入保护带
- `TRAILING_GIVEBACK_RATIO = 0.50` → peak 必须回吐 50% 才触发退出

这套设计在"有连续 mean-revert"的市场是对的 — 让利润奔跑, 回吐一半才出. 但在
UVXY 这种"要么不动要么爆走"的标的, peak 通常只能在很短的 1-2 个 bar 里冲到
$50-$100, 然后立刻被反向价格抹平, 中间根本没有"trail"的机会. 等系统检测到回吐
50%, 实际 PnL 已经回到 $20 以下了 (session 9 / 18 / 35 都符合这个 pattern).

### D. base=0 之后丢失了"directional carry"

旧策略 (BASE_POSITION_RATIO=0.40) 在 2024-04-01 session 22 那种 +496% 大涨里
靠底仓拿满涨幅. 现在 base=0, 那 +496% 完全没赚到 — peak=$0, final=$0.
这是改成 base=0 的**预期代价**, 但需要明确认识到: 旧 +19.4% 5y return 里有
相当大一部分来自这类"罕见大涨 + 底仓 long" 的 carry, 不是网格 scalp 本身的 edge.

---

## 6. 哪些参数调了也救不了

| 参数 | 调整方向 | 为什么救不了 |
|---|---|---|
| `SESSION_MAX_AGE_BARS` 加大 (40 → 60) | 给更多 mean-revert 时间 | 19 个 BUY-only session 给再多时间也不会反弹 — UVXY 不是 ranging |
| `SESSION_MIN_PROFIT_TO_PROTECT_PCT` 调小 (0.005 → 0.002) | 让保护带更早进入 | peak=$0 的 19 个 session 调到 $0.01 也没用 |
| `SESSION_TRAILING_GIVEBACK_RATIO` 调小 (0.50 → 0.30) | 锁利更早 | 帮助有限, 因为 peak 区间 $50-$100 的 session 本来就少 |
| `GRID_SPACING_ATR_MULTIPLIER` 调大 (0.5 → 1.0) | 网格更宽, 给反弹更多空间 | 帮的是"有反弹但反弹幅度不够"的 session, 不解决"零反弹"的 19 个 |
| `ENTRY_MAX_ADX` 调小 (25 → 18) | 入场更挑剔 | 让 zero_window 变多 — 入场更少, total trades 更少, 但单 session 不变 |

---

## 7. 真要改善 → 必须动结构, 不能只动参数

按影响力排序的结构性改造方向:

### S1. OFFENSIVE 期间允许 recenter (有条件)

把 `TACTICAL_RECENTER_ALLOWED_IN_OFFENSIVE` 默认改成 `True`, 但加约束:
- 仅在 session 内首次 `grid_buys == 0` 且 age >= 8 bar 时允许一次 recenter
- 后续 recenter 仍受 `should_disable_recenter` 控制 (软止损后禁止)

这能直接救 26% "零成交" 的 session — 价格漂走之后追上去重新搭网格.

### S2. 加 "no-fill timeout" (short-circuit)

新加 `SESSION_NO_FILL_TIMEOUT_BARS = 6`: 入场后 6 个 bar 都没有任何 grid_buy
fill, 直接 close session (zero cost), 不浪费 age 预算. 这把"零成交"的 26%
直接切掉, 让资金更快回到 SCANNING 等更好的入场点.

### S3. 入场过滤里加"过去 N bar 实际震荡"过滤

不只是用 ADX (前瞻性差), 加一个"过去 20 bar 价格高低差 / ATR" 必须 ≥ 2.0 — 用
实际历史震荡幅度过滤, 而不是 ADX (预测性指标).

### S4. 重新评估 base=0 vs base=0.05~0.10

考虑到 26% 的 session 是"零成交 + 价格大幅漂走", 把这些"漂走"事件当成
"未实现的 carry trade" 看, 可以给一个 base=0.05~0.10 (5%-10%) 的最小底仓:
- 涨/跌都会有 5-10% 的暴露
- 在 BUY-only 的 19 个 session 里, base 会跟着 BUY 一起亏 (扣分), 但帮助有限
- 在零成交 26% 的 session 里, base 是唯一能赚到方向钱的东西 (加分)

需要回测验证净效果. **这不是回到 0.40, 是用一个小 carry 换 26% session 的浪费**.

### S5. 换标的

UVXY 4h 0/39 真 flat session 这件事本身说明: 当前 entry filter + 4h 周期下,
UVXY 没有给网格策略的"range-bound" 窗口. 应该用同样代码跑一遍 SVXY / VXX /
TQQQ / QQQ, 看哪些标的 ranging 比例更高.

---

## 8. 总结

**total_return 负** 的原因不是 "参数没调好", 是:

1. 75% 的 session 在策略前提失效的市场状态下被启动 (UVXY 不 ranging)
2. 19 个 BUY-only session 累积 ~$1,000+ 网格亏损 (砸盘清仓)
3. 10 个真正 mean-revert 的 session 累积约 $500 利润, 7 个被 profit_protect
   锁住一半 (avg ~$30/session × 7 ≈ $210 锁利)
4. 旧 base=0.40 那部分 carry (尤其 2024H2 +37.7% 单窗口) 在 base=0 后完全消失
5. 净结果: -$591 grid pnl / $10,000 = **-5.91% 5y return**, 和实测吻合

**timeout 全屏** 的原因: 75% session 既没盈利达到 protect 门槛, 又没亏到
hard_stop. 它们的唯一退出路径就是 age. 这是设计的"兜底", 不是问题, 但**说明这
75% session 的存在本身就是浪费 age 预算**.

**profit_protect 不触发** 的原因: peak < $50 的 session 占 72%. 那些没碰到
门槛的, 系统就没机会触发. 不是逻辑 bug.

---

## 9. 文件清单

- `/tmp/p0_top1_5y.db`        — 持久化的 5y 回测 DB (39 sessions, 109 trades, 168 events)
- `/tmp/diagnose_p0.py`        — 一次性诊断脚本 (P0 top-1 单次回测 + 写 DB)
- `/tmp/diag2.py`              — 二次诊断 (BUY/SELL 分类, peak 分布)
- `reports/tuning/CANDIDATE_V1_VALIDATION.md` — 上一份 candidate v1 vs P0 对比
- `reports/tuning/WHY_NEGATIVE_RETURN_ROOT_CAUSE.md` — 本文
