"""
data_provider.py — 数据获取与缓存

职责:
  - 从Yahoo Finance下载历史数据
  - 本地缓存(parquet格式，速度快、占用小)
  - 增量更新(只下载新增的部分)
  - 提供通用策略周期历史数据入口

依赖: pip install yfinance pandas pyarrow
"""

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

try:
    import yfinance as yf
except ImportError:
    print("请先安装: pip install yfinance pandas pyarrow")
    raise

import config

logger = logging.getLogger("GridTrader.Data")
NY_TZ = "America/New_York"


class DataProvider:
    """数据提供器：下载、缓存、增量更新"""

    def __init__(self, cache_dir: Optional[str] = None):
        self.cache_dir = Path(cache_dir or config.DATA_CACHE_DIR)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # ────────────────────────────
    #  获取数据 (统一接口)
    # ────────────────────────────

    def get_daily(self, symbol: str, years: int = 5,
                  use_cache: bool = True) -> pd.DataFrame:
        """获取日线数据，自动缓存"""
        return self._get_data(symbol, "1d", years * 365, use_cache)

    def get_data(self, symbol: str, interval: str, days: int,
                 use_cache: bool = True) -> pd.DataFrame:
        """通用历史数据入口，供回测等离线分析复用。"""
        return self._get_data(symbol, interval, days, use_cache)

    def get_strategy_data(self, symbol: str,
                          days: int = None,
                          use_cache: bool = True) -> pd.DataFrame:
        """按当前策略周期获取历史数据。"""
        lookback_days = days if days is not None else config.HISTORY_LOOKBACK_DAYS
        return self._get_data(symbol, config.STRATEGY_INTERVAL, lookback_days, use_cache)

    def get_hourly(self, symbol: str, days: int = 60,
                   use_cache: bool = True) -> pd.DataFrame:
        """
        获取1小时K线数据，自动缓存
        Yahoo Finance限制：1h数据最多730天
        """
        days = min(days, 730)
        return self._get_data(symbol, "1h", days, use_cache)

    def get_recent_hourly(self, symbol: str, hours: int = 100) -> pd.DataFrame:
        """获取最近N小时数据，永远从网上拉取最新（不用缓存）"""
        days_needed = max(7, hours // 6 + 2)  # 多一点缓冲
        return self._get_data(symbol, "1h", days_needed, use_cache=False)

    # ────────────────────────────
    #  内部实现
    # ────────────────────────────

    def _get_data(self, symbol: str, interval: str,
                  days: int, use_cache: bool) -> pd.DataFrame:
        """统一的数据获取入口"""
        cache_interval = "4h" if interval == "4h" else interval
        cache_file = self.cache_dir / f"{symbol}_{cache_interval}.parquet"

        # 先尝试加载缓存
        cached_df = None
        if use_cache and cache_file.exists():
            try:
                cached_df = pd.read_parquet(cache_file)
                logger.debug(f"加载缓存 {cache_file.name}: {len(cached_df)} 条")
            except Exception as e:
                logger.warning(f"缓存读取失败 {cache_file}: {e}")

        # 判断是否需要下载/更新
        needs_download = True
        if cached_df is not None and not cached_df.empty:
            last_time = cached_df.index[-1]
            now = datetime.now()
            # 时间间隔判断
            stale_threshold = {
                "1d": timedelta(hours=12),
                "4h": timedelta(hours=4),
                "1h": timedelta(hours=1),
                "5m": timedelta(minutes=10),
            }.get(interval, timedelta(hours=1))

            if isinstance(last_time, pd.Timestamp):
                last_time = last_time.to_pydatetime()
                if last_time.tzinfo:
                    last_time = last_time.replace(tzinfo=None)

            if now - last_time < stale_threshold:
                needs_download = False
                logger.debug(f"{symbol} {interval} 缓存仍新鲜，跳过下载")

        # 下载新数据
        if needs_download:
            try:
                fresh_df = self._download_from_yahoo(symbol, interval, days)
                if fresh_df is not None and not fresh_df.empty:
                    # 合并新旧数据
                    if cached_df is not None:
                        combined = pd.concat([cached_df, fresh_df])
                        combined = combined[~combined.index.duplicated(keep='last')]
                        combined = combined.sort_index()
                    else:
                        combined = fresh_df

                    # 保存缓存
                    combined.to_parquet(cache_file)
                    logger.info(f"✓ 更新缓存 {symbol} {interval}: {len(combined)} 条")
                    return combined
            except Exception as e:
                logger.error(f"下载失败 {symbol} {interval}: {e}")
                if cached_df is not None:
                    logger.warning("使用过期缓存继续运行")
                    return cached_df
                raise

        return cached_df

    def _download_from_yahoo(self, symbol: str, interval: str,
                             days: int) -> pd.DataFrame:
        """从Yahoo Finance下载"""
        yahoo_interval = "1h" if interval == "4h" else interval
        end = datetime.now()
        start = end - timedelta(days=days)

        # Yahoo对不同interval有历史深度限制
        max_days = {
            "1m": 7, "5m": 60, "15m": 60, "30m": 60,
            "1h": 730, "4h": 730, "1d": 365 * 20,
        }
        actual_days = min(days, max_days.get(interval, 365 * 20))
        actual_start = end - timedelta(days=actual_days)

        logger.info(f"下载 {symbol} {interval} | "
                    f"{actual_start.date()} ~ {end.date()}")

        ticker = yf.Ticker(symbol)
        df = pd.DataFrame()

        # Yahoo 对小时/分钟线经常更稳定地支持 period 模式。
        # 先尝试 start/end，失败或空数据时再回退到 period。
        try:
            df = ticker.history(
                start=actual_start, end=end,
                interval=yahoo_interval, auto_adjust=True
            )
        except Exception as e:
            logger.warning(f"Yahoo start/end下载失败，改用period回退: {e}")

        if df is None or df.empty:
            period_days = max(1, actual_days)
            try:
                df = ticker.history(
                    period=f"{period_days}d",
                    interval=yahoo_interval,
                    auto_adjust=True,
                    prepost=False,
                )
            except Exception as e:
                logger.error(f"Yahoo period下载失败: {e}")
                df = pd.DataFrame()

        if df.empty:
            raise ValueError(f"Yahoo返回空数据: {symbol} {interval}")

        # 标准化列名（保留Open/High/Low/Close/Volume）
        df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
        df = df.dropna()

        if df.index.tz is not None:
            df.index = df.index.tz_convert(NY_TZ).tz_localize(None)

        if interval == "4h":
            df = self._aggregate_hourly_to_4h(df)

        return df

    def _aggregate_hourly_to_4h(self, df: pd.DataFrame) -> pd.DataFrame:
        """把1h数据按交易日内顺序聚合成4h策略bar。"""
        if df.empty:
            return df

        df = df.sort_index().copy()
        trading_day = pd.Index(df.index.date, name="trading_day")
        bar_seq = pd.Series(range(len(df)), index=df.index)
        intra_day_seq = bar_seq.groupby(trading_day).cumcount()
        group_id = intra_day_seq // 4

        aggregated = (
            df.groupby([trading_day, group_id], sort=True)
            .agg({
                "Open": "first",
                "High": "max",
                "Low": "min",
                "Close": "last",
                "Volume": "sum",
            })
            .reset_index(level=1, drop=True)
        )

        group_end_time = (
            pd.Series(df.index, index=df.index)
            .groupby([trading_day, group_id], sort=True)
            .last()
        )
        aggregated.index = pd.DatetimeIndex(group_end_time.values)
        return aggregated.sort_index()

    # ────────────────────────────
    #  实用方法
    # ────────────────────────────

    def get_latest_price(self, symbol: str) -> float:
        """按当前策略周期缓存获取最新价格。"""
        lookback_days = 14 if config.STRATEGY_INTERVAL == "4h" else (7 if not config.STRATEGY_INTERVAL.endswith("d") else 30)
        df = self.get_data(symbol, config.STRATEGY_INTERVAL, lookback_days, use_cache=True)
        if df.empty:
            return None
        return float(df["Close"].iloc[-1])

    def clear_cache(self, symbol: str = None):
        """清除缓存"""
        if symbol:
            for f in self.cache_dir.glob(f"{symbol}_*.parquet"):
                f.unlink()
                logger.info(f"删除缓存 {f.name}")
        else:
            for f in self.cache_dir.glob("*.parquet"):
                f.unlink()


# ────────────────────────────
#  独立运行测试
# ────────────────────────────
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")

    dp = DataProvider()
    symbol = sys.argv[1] if len(sys.argv) > 1 else config.SYMBOL

    print(f"\n=== 测试 {symbol} 日线 (5年) ===")
    df_daily = dp.get_daily(symbol, years=5)
    print(f"数据: {len(df_daily)} 条 | "
          f"{df_daily.index[0].date()} ~ {df_daily.index[-1].date()}")
    print(f"价格范围: ${df_daily['Close'].min():.2f} ~ ${df_daily['Close'].max():.2f}")

    print(f"\n=== 测试 {symbol} 1h线 (60天) ===")
    df_hourly = dp.get_hourly(symbol, days=60)
    print(f"数据: {len(df_hourly)} 条 | "
          f"{df_hourly.index[0]} ~ {df_hourly.index[-1]}")
    print(f"\n最近5条:")
    print(df_hourly.tail())
