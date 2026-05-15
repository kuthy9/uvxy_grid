"""Symbol screening v2 — 用更接近实际网格机制的代理指标.

对每个符合 S3 入场条件的时间点 (低 ADX + 20-bar range >= 2×ATR), 模拟一个 60-bar
session, 统计:
  - round_trip_count: session 内"价格从中轴向下跑 >=0.5×ATR 后再回中轴" 的次数
                       (一次完整 BUY-then-SELL 的可能性)
  - successful_sessions: 至少有 1 个 round trip 完成
  - zero_fill_sessions: 整段 60-bar 价格都没下穿过 entry - 0.5×ATR (0 成交)
  - one_way_sessions: 下穿过 entry-0.5×ATR 但从未上回 entry (BUY only)
"""
import sys, statistics
import pandas as pd
import numpy as np

try:
    import yfinance as yf
except ImportError:
    print("pip install yfinance"); sys.exit(1)

CANDIDATES = [
    "UVXY", "VXX", "SVXY", "TQQQ", "SQQQ", "SOXL", "SOXS",
    "SPXL", "SPXS", "TLT", "GLD", "USO", "UNG", "QQQ", "SPY",
]

GRID_SPACING_ATR = 0.5      # 网格 -1 档距中轴 0.5×ATR
SESSION_BARS = 60           # 假设 session 长度 (P0 baseline 是 60 bar 内)
RANGING_LOOKBACK = 20
RANGING_MIN_ATR = 2.0
ADX_MAX = 25

def resample_to_4h(df):
    agg = df.resample("4h", origin="start_day").agg({
        "Open":"first","High":"max","Low":"min","Close":"last","Volume":"sum"
    }).dropna()
    return agg

def compute_indicators(df):
    df = df.copy()
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low, (high - prev_close).abs(), (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    df["ATR"] = tr.rolling(14).mean()
    df["ATR_PCT"] = df["ATR"] / df["Close"]
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0)
    plus_di = 100 * (plus_dm.rolling(14).mean() / df["ATR"])
    minus_di = 100 * (minus_dm.rolling(14).mean() / df["ATR"])
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    df["ADX"] = dx.rolling(14).mean()
    return df.dropna()

def simulate_session(df_ind, entry_idx, atr_at_entry):
    """从 entry_idx 开始模拟 SESSION_BARS 期内的网格行为.

    简化模型:
      - 中轴 = Close[entry_idx]
      - -1 档 BUY price = center - 0.5×ATR
      - +1 档 SELL price = center + 0.5×ATR (相对中轴)
      - 完整 round trip: 价格触及 -1 (BUY 填) 之后又触及 +1 (SELL 平 BUY 配对)
      - 也可以是: 价格触及 +1 (空头?) - 但我们只做 long grid, 不算这种
      - zero_fill: 整段最低价 > -1 档
      - one_way: 触及 -1 档但整段最高价始终 < center (从未回到中轴上方)
    """
    center = float(df_ind["Close"].iloc[entry_idx])
    buy_lvl = center - GRID_SPACING_ATR * atr_at_entry
    sell_lvl = center + GRID_SPACING_ATR * atr_at_entry
    window = df_ind.iloc[entry_idx + 1: entry_idx + 1 + SESSION_BARS]
    if len(window) == 0:
        return None

    # 价格序列
    lows = window["Low"].values
    highs = window["High"].values
    closes = window["Close"].values

    # 状态机: 等 BUY 触发
    in_buy = False
    round_trips = 0
    min_low = float(lows.min())
    max_high = float(highs.max())

    for lo, hi in zip(lows, highs):
        if not in_buy:
            if lo <= buy_lvl:
                in_buy = True
        else:
            # 已 BUY, 等 SELL (回到 sell_lvl)
            if hi >= sell_lvl:
                round_trips += 1
                in_buy = False  # 准备下一轮

    zero_fill = (min_low > buy_lvl)
    one_way = (min_low <= buy_lvl) and (max_high < center) and round_trips == 0
    successful = round_trips >= 1
    return {
        "round_trips": round_trips,
        "zero_fill": zero_fill,
        "one_way": one_way,
        "successful": successful,
    }

def screen_one(symbol):
    try:
        df = yf.download(symbol, period="729d", interval="1h",
                          progress=False, auto_adjust=True)
        if df is None or len(df) < 100:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0] for c in df.columns]
        df = df[["Open","High","Low","Close","Volume"]].dropna()
        df.index = pd.to_datetime(df.index)
        df_4h = resample_to_4h(df)
        df_ind = compute_indicators(df_4h)
        if len(df_ind) < 200:
            return None
    except Exception as e:
        print(f"  {symbol}: {e}"); return None

    n = len(df_ind)
    # 找所有 entry candidates (ADX < ADX_MAX AND 20-bar range >= 2.0×ATR)
    entries = []
    for i in range(RANGING_LOOKBACK, n - SESSION_BARS - 1):
        adx_i = float(df_ind["ADX"].iloc[i])
        if adx_i >= ADX_MAX:
            continue
        atr_i = float(df_ind["ATR"].iloc[i])
        if atr_i <= 0:
            continue
        win = df_ind.iloc[i - RANGING_LOOKBACK:i]
        rng = float(win["High"].max() - win["Low"].min())
        if rng / atr_i < RANGING_MIN_ATR:
            continue
        entries.append((i, atr_i))

    # --- Part 1B (i): ranging density per year ---
    BARS_PER_YEAR_4H = 410
    n_years = n / BARS_PER_YEAR_4H
    ranging_density = len(entries) / n_years if n_years > 0 else 0

    # --- Part 1B (ii): trend period proxy (ADX > 25 consecutive runs) ---
    adx_high = df_ind["ADX"] > 25
    trend_lengths = []
    current_len = 0
    for v in adx_high.values:
        if v:
            current_len += 1
        else:
            if current_len > 0:
                trend_lengths.append(current_len)
                current_len = 0
    if current_len > 0:
        trend_lengths.append(current_len)
    trend_avg_length = float(np.mean(trend_lengths)) if trend_lengths else 0.0
    trend_total_pct = float(adx_high.mean() * 100)
    trend_periods_count = len(trend_lengths)

    if not entries:
        return {
            "symbol": symbol, "entries": 0, "n_bars": n,
            "successful_pct": 0, "round_trips_per_session": 0,
            "zero_fill_pct": 0, "one_way_pct": 0,
            "atr_pct_median": 0, "avg_daily_M": 0,
            "ranging_density": 0.0,
            "trend_avg_length": trend_avg_length,
            "trend_total_pct": trend_total_pct,
            "trend_periods_count": trend_periods_count,
            "cost_ratio_pct": 999.0,
            "pass_i": False,
            "pass_ii": trend_avg_length >= 5 and trend_total_pct >= 15,
            "pass_iii": False,
            "pass_iv": False,
            "pass_all": False,
        }

    # 模拟每个 entry → 60 bar session
    succ = 0
    zf = 0
    ow = 0
    rt_total = 0
    for entry_idx, atr_at in entries:
        sim = simulate_session(df_ind, entry_idx, atr_at)
        if sim is None: continue
        if sim["successful"]: succ += 1
        if sim["zero_fill"]: zf += 1
        if sim["one_way"]:   ow += 1
        rt_total += sim["round_trips"]

    n_e = len(entries)
    atr_pct_median = float(df_ind["ATR_PCT"].median())
    # liquidity (4h bar level → daily)
    bar_dollar = (df_ind["Volume"] * df_ind["Close"])
    by_day = bar_dollar.groupby(df_ind.index.date).sum()
    avg_daily_M = float(by_day.tail(60).mean()) / 1e6 if len(by_day) else 0

    # --- Part 1B (iii): cost ratio proxy per round trip ---
    GRID_CAP_RATIO = 0.5
    GRID_LEVELS = 6
    CAPITAL = 10000
    SPREAD_BPS = 5
    COMM_MIN = 0.35
    COMM_PER_SHARE = 0.0035

    cost_ratios = []
    for entry_idx, atr_at in entries:
        close_at = float(df_ind["Close"].iloc[entry_idx])
        if close_at <= 0 or atr_at <= 0:
            continue
        shares_per_level = (CAPITAL * GRID_CAP_RATIO / GRID_LEVELS) / close_at
        if shares_per_level <= 0:
            continue
        gross_pnl = GRID_SPACING_ATR * atr_at * shares_per_level
        commission = 2 * max(COMM_MIN, COMM_PER_SHARE * shares_per_level)
        spread_cost = 2 * (SPREAD_BPS / 10000) * close_at * shares_per_level
        total_cost = commission + spread_cost
        if gross_pnl > 0:
            cost_ratios.append(min(total_cost / gross_pnl, 999))
    cost_ratio_median = float(np.median(cost_ratios)) * 100 if cost_ratios else 999.0  # % units

    # --- Part 1B (iv): cost sensitivity proxy = atr_pct_median (already computed) ---

    pass_i   = ranging_density >= 30
    pass_ii  = trend_avg_length >= 5 and trend_total_pct >= 15
    pass_iii = cost_ratio_median <= 10
    pass_iv  = (atr_pct_median * 100) >= 1.5

    return {
        "symbol": symbol,
        "n_bars": n,
        "entries": n_e,
        "successful_pct": succ / n_e * 100,
        "round_trips_per_session": rt_total / n_e,
        "zero_fill_pct": zf / n_e * 100,
        "one_way_pct": ow / n_e * 100,
        "atr_pct_median": atr_pct_median * 100,
        "avg_daily_M": avg_daily_M,
        # Part 1B metrics
        "ranging_density": ranging_density,
        "trend_avg_length": trend_avg_length,
        "trend_total_pct": trend_total_pct,
        "trend_periods_count": trend_periods_count,
        "cost_ratio_pct": cost_ratio_median,
        # threshold booleans
        "pass_i": pass_i,
        "pass_ii": pass_ii,
        "pass_iii": pass_iii,
        "pass_iv": pass_iv,
        "pass_all": pass_i and pass_ii and pass_iii and pass_iv,
    }

results = []
for sym in CANDIDATES:
    print(f"screening {sym}...")
    r = screen_one(sym)
    if r is not None:
        results.append(r)

# Score: weight on successful% + round_trips/session - zero_fill% - one_way%
def score(r):
    if r["entries"] == 0:
        return -10
    s = (
        1.0  * r["successful_pct"] / 100
        + 0.5  * min(r["round_trips_per_session"], 3) / 3
        + 0.3  * min(r["atr_pct_median"] / 4.0, 1.0)
        - 0.5  * r["zero_fill_pct"] / 100
        - 0.5  * r["one_way_pct"] / 100
    )
    if r["avg_daily_M"] < 5:
        s -= 0.1
    return s

for r in results:
    r["score"] = score(r)

results.sort(key=lambda r: -r["score"])

print()
print("=" * 130)
print(f"{'symbol':<8} {'entries':>7} {'succ%':>6} {'rt/sess':>7} {'zero%':>6} "
      f"{'one_way%':>9} {'atr%med':>7} {'$M/day':>8} {'score':>7}")
print("-" * 130)
for r in results:
    print(f"{r['symbol']:<8} {r['entries']:>7d} "
          f"{r['successful_pct']:>6.1f} {r['round_trips_per_session']:>7.2f} "
          f"{r['zero_fill_pct']:>6.1f} {r['one_way_pct']:>9.1f} "
          f"{r['atr_pct_median']:>7.2f} {r['avg_daily_M']:>8.1f} {r['score']:>7.3f}")

print()
print("Top-5 推荐:")
for r in results[:5]:
    print(f"  {r['symbol']}: score={r['score']:.3f}  "
          f"成功 round-trip 比例 {r['successful_pct']:.0f}%, "
          f"零成交 {r['zero_fill_pct']:.0f}%, "
          f"单向 {r['one_way_pct']:.0f}%")

print()
print("=" * 130)
print("Part 1B 必要条件 (战术化短线收割可落地的 4 条)")
print("=" * 130)
print(f"{'symbol':<8} {'ranging/yr':>10} {'trend_avg':>10} {'trend_tot%':>10} "
      f"{'cost_pct':>9} {'atr%':>6} {'(i)':>4} {'(ii)':>5} {'(iii)':>6} {'(iv)':>5} {'ALL':>4}")
print(f"{'':8} {'>=30':>10} {'>=5bars':>10} {'>=15%':>10} "
      f"{'<=10%':>9} {'>=1.5%':>6}")
print("-" * 130)
for r in results:
    print(f"{r['symbol']:<8} {r['ranging_density']:>10.1f} "
          f"{r['trend_avg_length']:>10.1f} {r['trend_total_pct']:>10.1f} "
          f"{r['cost_ratio_pct']:>9.2f} {r['atr_pct_median']:>6.2f} "
          f"{'✓' if r['pass_i'] else '✗':>4} {'✓' if r['pass_ii'] else '✗':>5} "
          f"{'✓' if r['pass_iii'] else '✗':>6} {'✓' if r['pass_iv'] else '✗':>5} "
          f"{'✓' if r['pass_all'] else '✗':>4}")

passers = [r for r in results if r.get("pass_all")]
print()
print(f"Part 1B 4 条必要条件全部通过的候选: {len(passers)}/{len(results)}")
for r in passers:
    print(f"  {r['symbol']}: ranging={r['ranging_density']:.0f}/yr "
          f"trend_avg={r['trend_avg_length']:.1f}bars trend_pct={r['trend_total_pct']:.0f}% "
          f"cost={r['cost_ratio_pct']:.1f}% atr={r['atr_pct_median']:.1f}%")

# 3/4 条通过的候选 (接近但未全过)
near_passers = [r for r in results
                if not r.get("pass_all") and
                sum([r["pass_i"], r["pass_ii"], r["pass_iii"], r["pass_iv"]]) >= 3]
if near_passers:
    print()
    print(f"接近全过 (3/4 条) 的候选: {len(near_passers)}")
    for r in near_passers:
        failed = []
        if not r["pass_i"]:   failed.append(f"(i)ranging={r['ranging_density']:.0f}/yr<30")
        if not r["pass_ii"]:  failed.append(f"(ii)trend_avg={r['trend_avg_length']:.1f}b,pct={r['trend_total_pct']:.0f}%")
        if not r["pass_iii"]: failed.append(f"(iii)cost={r['cost_ratio_pct']:.1f}%>10")
        if not r["pass_iv"]:  failed.append(f"(iv)atr={r['atr_pct_median']:.2f}%<1.5")
        print(f"  {r['symbol']}: 未过 → {', '.join(failed)}")
