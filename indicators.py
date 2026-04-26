"""
indicators.py — 技术指标计算

所有指标都返回与输入DataFrame相同长度的Series
"""

import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    """指数移动均线"""
    return series.ewm(span=period, adjust=False).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    """简单移动均线"""
    return series.rolling(window=period, min_periods=period).mean()


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Average True Range — 衡量波动性
    需要 High, Low, Close 列
    """
    high = df["High"]
    low = df["Low"]
    close = df["Close"]

    # True Range = max(H-L, |H-PrevClose|, |L-PrevClose|)
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    # Wilder's smoothing (等价于 EMA with alpha=1/period)
    return tr.ewm(alpha=1/period, adjust=False).mean()


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Average Directional Index — 衡量趋势强度
    
    ADX < 20: 无趋势 (适合网格)
    ADX 20-25: 弱趋势 (可以网格但需谨慎)
    ADX > 25: 趋势确立 (不适合网格)
    ADX > 50: 强趋势 (绝对不适合)
    """
    high = df["High"]
    low = df["Low"]
    close = df["Close"]

    # +DM 和 -DM
    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0),
        index=df.index
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0),
        index=df.index
    )

    # ATR (用于归一化)
    atr_val = atr(df, period)

    # +DI 和 -DI (Wilder smoothing)
    plus_di = 100 * plus_dm.ewm(alpha=1/period, adjust=False).mean() / atr_val
    minus_di = 100 * minus_dm.ewm(alpha=1/period, adjust=False).mean() / atr_val

    # DX
    di_sum = plus_di + minus_di
    di_diff = (plus_di - minus_di).abs()
    dx = 100 * di_diff / di_sum.replace(0, np.nan)

    # ADX = DX的Wilder平滑
    adx_val = dx.ewm(alpha=1/period, adjust=False).mean()

    return adx_val.fillna(0)


def bollinger_bands(series: pd.Series, period: int = 20,
                    std_dev: float = 2.0) -> tuple:
    """
    布林带 — 返回 (中轨, 上轨, 下轨, 带宽百分比)
    带宽百分比 = (上轨 - 下轨) / 中轨
    """
    middle = sma(series, period)
    std = series.rolling(window=period, min_periods=period).std()
    upper = middle + std_dev * std
    lower = middle - std_dev * std
    width_pct = (upper - lower) / middle
    return middle, upper, lower, width_pct


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """相对强弱指数"""
    delta = series.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def compute_all_indicators(df: pd.DataFrame, ema_period: int = 20,
                           atr_period: int = 14,
                           adx_period: int = 14,
                           bb_period: int = 20) -> pd.DataFrame:
    """
    一次性计算所有指标，返回扩展后的DataFrame
    """
    out = df.copy()
    out["EMA"] = ema(df["Close"], ema_period)
    out["ATR"] = atr(df, atr_period)
    out["ATR_PCT"] = out["ATR"] / out["Close"]
    out["ADX"] = adx(df, adx_period)

    bb_mid, bb_upper, bb_lower, bb_width = bollinger_bands(df["Close"], bb_period)
    out["BB_MID"] = bb_mid
    out["BB_UPPER"] = bb_upper
    out["BB_LOWER"] = bb_lower
    out["BB_WIDTH_PCT"] = bb_width

    out["RSI"] = rsi(df["Close"])
    out["EMA_DIST_ATR"] = (out["Close"] - out["EMA"]) / out["ATR"]

    return out


# ──────────────────────────────
#  独立测试
# ──────────────────────────────
if __name__ == "__main__":
    # 创建一些假数据测试
    np.random.seed(42)
    n = 200
    base = 100 + np.cumsum(np.random.randn(n) * 1.5)
    df_test = pd.DataFrame({
        "Open": base + np.random.randn(n) * 0.5,
        "High": base + abs(np.random.randn(n)) * 1.0,
        "Low": base - abs(np.random.randn(n)) * 1.0,
        "Close": base + np.random.randn(n) * 0.3,
        "Volume": np.random.randint(1e6, 1e7, n)
    })

    result = compute_all_indicators(df_test)
    print("最近5行的指标值:")
    print(result.tail()[["Close", "EMA", "ATR", "ATR_PCT", "ADX",
                         "BB_WIDTH_PCT", "RSI", "EMA_DIST_ATR"]].round(3))
