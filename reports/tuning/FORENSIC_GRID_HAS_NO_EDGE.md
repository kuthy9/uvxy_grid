# Forensic: 当前 +6.09% 5y 回报的实际成分 + 网格机制是否有 edge

date: 2026-05-14
source: `/tmp/best_5y.db` (跑当前最佳配置一次, 全量取值)
方法: 不抽样, 不估算, 每个数字来自 SQLite 直接 query

---

## TL;DR (3 句话)

1. **"+6.09% 5y" 中 +$588 (96.5%) 来自 BASE_POSITION_RATIO=0.05 的 UVXY 长期 carry,
   网格机制本身只贡献 +$21 / 5y (≈ +0.04%/年)**.
2. **5 年只触发 6 个 GRID_BUY 和 3 个 GRID_SELL** — T1+T3+S2+S3 过滤把网格压到几乎不
   trade 的程度.
3. **进一步实验显示: 关掉任何一个过滤让网格多 trade, 回报都变差**. 网格在 UVXY 4h
   的当前 spec 下 **net 净亏损**, 不存在被掩盖的 edge.

**结论**: 当前系统功能上等价于 "5% UVXY 长仓 + 一堆 no-op session". "+6.09%" 是
buy-and-hold 的回报, 不是 grid alpha.

---

## 1. 5y 实测原始数据 (config = T1+T3+S2+S3+base=0.05)

```
ret = +6.0915%   dd = 2.2203%   pf = 1.8759
sessions = 59    trades = 133
```

### 1.1 全部 133 笔 trade 分类 (DB groupby `action × order_type`)

```
action  order_type   n   sum_qty   sum_pnl   sum_commission
BUY     BASE_BUY    59    2617.0    $0.00    $20.66
BUY     GRID_BUY     6     350.0    $0.00     $2.10
SELL    EXIT_BASE   59    2617.0    $0.00    $22.12
SELL    EXIT_GRID    6     133.0  -$19.56     $2.19
SELL    GRID_SELL    3     217.0  +$40.42     $1.12
```

> 数字直接读自 `trades` 表 — 不是抽样.

### 1.2 三个 SELL 类别的 PnL 分布

```
GRID_SELL  (FIFO 配对) n=3   sum=+$40.42  min=+$9.84  max=+$15.80  median=+$14.79
EXIT_GRID  (砸盘清仓)  n=6   sum=-$19.56  min=-$16.64 max= +$3.40  median= $0.00
PARTIAL_PROFIT_EXIT    n=0
```

### 1.3 BASE_BUY → EXIT_BASE 现金流

```
BASE_BUY 总买入现金:  $28,973.58 (commission $20.66)
EXIT_BASE 总卖出现金: $29,604.64 (commission $22.12)
BASE 净 cash flow:    +$588.28
```

### 1.4 把 +$609.15 拆解到组件

| 组件 | $ | % of total |
|---|---|---|
| GRID_SELL 配对 (网格 scalp 利润) | +$40.42 | 6.6% |
| EXIT_GRID 砸盘 (清仓损失) | -$19.56 | -3.2% |
| **网格净贡献** | **+$20.86** | **3.4%** |
| **BASE 净 cash flow (UVXY long carry)** | **+$588.28** | **96.5%** |
| 账面合计 | +$609.15 | 100.0% |

**这是不可辩驳的事实**: 5y +6.09% 中 **96.5% 来自 buy-and-hold 5% UVXY**, 不是
来自网格交易.

---

## 2. session-by-session 详情 (59 行, 部分抽取)

完整 59 行表格在 `/tmp/forensic_audit.py` 输出里. 关键模式:

| 子集 | session 数 | 占比 | 平均 grid_BUY 数 |
|---|---|---|---|
| 全程 0 grid 成交 (BASE_BUY + 6-bar 后 EXIT_BASE) | **51 / 59** | **86%** | 0.0 |
| 触发 1-3 个 grid_BUY 但无 SELL | 5 / 59 | 8% | ~1.4 |
| 完整 grid round-trip (BUY + SELL) | **3 / 59** | **5%** | ~1.0 |

**86% 的 session 是"BASE_BUY 入场 → 6 bar 后无 grid 成交 → no_fill_timeout 退出"**.
S2 (no_fill_timeout=6) 在 6 bar 之内把它们切掉, BASE 部分按当前价 EXIT_BASE 落袋.
这就是"BASE long carry"的实际机制 — 短短 6 bar 内 UVXY 价格变化决定 base 净盈亏.

---

## 3. 关键 ablation: 过滤越松 → 网格越多 trade → 回报越差

固定 base=0 (隔离网格本身), 关掉单个过滤:

| 配置 | ret% | dd% | pf | gB | gS | grid净$ |
|---|---|---|---|---|---|---|
| current best (全过滤) | +0.19 | 1.10 | 0.85 | 6 | 4 | **+$19** |
| − T1 (放行 ADX 上升) | +0.29 | 1.10 | 1.07 | 7 | 5 | +$29 |
| − T3 (放行深 BUY) | -1.63 | 3.80 | 0.49 | 31 | 9 | -$163 |
| − S2 (不再 0 成交早退) | -0.92 | 1.63 | 0.55 | 18 | 7 | -$93 |
| − S3 (放行低 range 入场) | +0.19 | 1.10 | 0.85 | 6 | 4 | +$19 |
| **全部 OFF** (只剩 ADX/ATR/BB) | **-5.86** | 6.43 | 0.36 | **66** | 15 | **-$587** |

**这张表是判决**:
- 网格成交越多 (gB 从 6 → 66), 净亏损越严重 (+$19 → -$587)
- 网格 BUY-SELL 配对成功率: 15/66 = **23%** (全部 OFF case)
- 剩下 77% 的 BUY 演变为 EXIT_GRID 砸盘 (-$858 累计)

**网格机制在 UVXY 4h 下是 net 净亏损工具**. 不存在被填充率 / commission / 入场时机
等次要原因掩盖的 hidden edge.

---

## 4. T1-T3 改进的真实机制

之前报告里 ret 从 -5.91% → +6.09% 的 +12 pp "改善", 拆开看:

| 改进步 | 操作 | 真实机制 (从 forensic 数据看) |
|---|---|---|
| S1 rescue_recenter | 0 成交时追一次价 | 几乎不触发 (50/59 session 在 S2 时刻就已切走) |
| S2 no_fill_timeout=6 | 6 bar 0 成交早退 | **关键**: 把 86% 的 session 在 6 bar 内切掉, **没让 grid 有机会亏钱** |
| S3 recent_range≥2 | 入场要求 20-bar range≥2×ATR | **几乎无效**: 关掉 S3 ret 完全不变 (+0.19% → +0.19%) |
| T1 ADX_slope≤0 | 拒绝 ADX 上升 | **微小效果**: ret 0.29 → 0.19 时差 0.1pp |
| T3 max_buy_depth=1.0 | 距中轴 >1×ATR 不加 BUY | **关键**: 把 grid_BUY 从 31 (无 T3) 切到 6 |
| base=0 → 0.05 | 5% UVXY 长仓 | **占改善 96%**: +$588 直接归功于 5y UVXY +243% rise |

**真实情况**:
- **S2 + T3 是"让网格不要 trade"** — 这是它们改善回报的机制 (因为 grid trade 是 net 亏损)
- **base=0.05 才是回报来源** — UVXY 5y net +243% × 5% × 持仓时长折扣 ≈ +5-6%
- T1 / S3 / S1 各贡献 < 0.5pp, 可有可无

**这不是网格策略的胜利, 是网格策略被关到不工作 + 一个小 buy-and-hold**.

---

## 5. 为什么网格在 UVXY 4h 没有 edge

### 5.1 数据观察

UVXY 5y 价格演化: $11.57 → $39.71, **net +243%** (含 2024-04 反向拆股的 5×
adjustment, 实际经济回报可能略小, 但方向正确).

期间没有持续 ranging 的 4h × 60-bar 窗口 (前面 ROOT_CAUSE_NOT_TACTICAL_OVERRIDE.md
已记: 24 个 session 下行, 15 个上行, **0 个 flat**).

### 5.2 网格机制对 UVXY 4h 失效的逻辑链

1. 网格本质 = 价格 mean-revert 时反复 BUY-SELL 配对
2. UVXY 在 4h 周期上不 mean-revert, 而是要么爆涨要么阴跌
3. 在阴跌窗口: BUY 反复填进来, SELL 永远到不了 → 累积浮亏
4. 在爆涨窗口: BUY 一档都不填, SELL 也卡在网格外 → 空仓
5. 唯一"真"ranging 的窗口 < 5% of time → 平均下来网格成交 = 净亏

### 5.3 数据印证

- ALL_OFF case 5y: 39 session 实际能触发入场 (现 ADX/ATR/BB 过滤), 66 个 grid_BUY
  fill, 但只有 15 个配对成功. **失败率 77%**
- 73% pf (current best) 是 sample bias: 那 4 个 GRID_SELL 是仅有的"运气好赶上小
  反弹"的样本. 样本量 = 3, 统计意义为 0.

---

## 6. 用户的核心问题: "回报糟糕的根因"

**答案不在过滤参数, 不在战术覆盖, 不在 base ratio**.

**根因**: **UVXY 4h × 网格 = 结构性 negative-edge 组合**.

任何让网格更多 trade 的参数变更都让回报变差. 当前看似不错的 +6.09% 是:
- **关掉 96.5% 的"想 trade 的机会"** (S2/T3 共同 effect)
- **替换为 buy-and-hold** (base=0.05)

把"激进战术 Session 网格" 在 UVXY 4h 上做到 "+6% / 5y / dd 2%", 数学上是因为
**它已经不是网格了**, 是 5% 长仓 + 6-bar 价格波动赌博.

---

## 7. 不假装的诚实评估

1. **当前最佳配置 "+6.09% / 5y / dd 2.22% / pf 1.88" 不是可部署 edge**:
   - 年化 +1.2% (低于美国国债 5y 收益率 ~4%)
   - 几乎全部来自 5% UVXY long, 而 UVXY 长期 carry 有路径风险 (拆股后会怎样, 不确定)
   - 网格机制实际 inactive (3/59 session 才有真 round-trip)

2. **"激进战术 Session 网格" 在 UVXY 4h 上不工作**. 任何调参都不能改变这点 — 
   过去 3 个月的所有 sweep / walk-forward / S/T 改造, 累计起来都没让网格自身
   产生 net 正贡献.

3. **代码本身是健康的**:
   - 245 tests OK
   - 战术化是 strict 增量 (T1-T3 都默认开但可 env 关)
   - HEAD 所有功能保留 (grid_engine.py 0 变化)
   - TURBO_ENABLED 单一开关 (config.py)

   问题不是代码, 不是参数, 是 strategy × symbol × timeframe 的根本错配.

4. **可行的方向** (不再调当前配置):

   a. **换标的**: 之前 `scripts/screen_symbols.py` 已显示 TQQQ / SOXL 在指标层面
      持平 UVXY 但流动性 13-19x. 真正的下一步是拿 4h TQQQ 数据跑同样回测,
      检验是否有跨标的的 edge.

   b. **换周期**: 4h 在 UVXY 上 5y 没有持续 ranging 窗口. 1h 数据可能更适合
      短线网格 (但要重新校准 entry/exit 周期).

   c. **承认这是 long-only**: 把"战术网格"层关掉 (TURBO_ENABLED=False),
      只保留 BASE_POSITION_RATIO=0.05-0.10 作为简单长仓, 用 risk_manager 的
      hard_stop 做风控. 当前代码本来就支持这个 fallback.

   d. **完全重新设计**: 接受网格 + UVXY 4h 不工作的事实, 探索 pair trading
      (UVXY + SVXY 反向对冲)、event-driven (FOMC / 财报前后短仓)、或 day-trading
      (1h 周期 + 当日清仓).

   **不推荐**: 继续在 P0-P5 / T1-T3 / S1-S6 这套框架里调参. 数据已经清晰说明这条
   路是死的.

---

## 8. 本轮改动 & 文件清单

代码改动:
- `config.py`: 新增 `TURBO_ENABLED` 单一总开关 (绑 env, 默认 True)
- `tactical_config.py`: `TACTICAL_GRID_ENABLED` 默认绑 `config.TURBO_ENABLED`

诊断脚本 (`/tmp/` 一次性):
- `run_best_and_persist.py` — 跑 5y 持久化 DB
- `forensic_audit.py` — 全量 trade-level forensic
- `grid_alone.py` — base ratio 隔离 grid 贡献
- `filter_ablation.py` — 过滤项 ablation

报告:
- `reports/tuning/FORENSIC_GRID_HAS_NO_EDGE.md` — 本文

测试: `python test.py` → 245 OK
