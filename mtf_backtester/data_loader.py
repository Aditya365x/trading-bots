"""
Data ingestion: download historical OHLCV from a ccxt exchange (default Binance),
paginating past the per-request candle cap, and cache to CSV so repeat runs are
instant and offline-safe.

Usage:
    python data_loader.py                      # download every TF in config
    python data_loader.py 5m 1h                # download just these
    from data_loader import load_ohlcv         # get a clean DataFrame in code
"""
from __future__ import annotations
import sys
import time
from pathlib import Path

import pandas as pd
import ccxt

import config


def _exchange():
    ex = getattr(ccxt, config.EXCHANGE)({"enableRateLimit": True})
    ex.load_markets()
    return ex


def _cache_path(symbol: str, timeframe: str) -> Path:
    safe = symbol.replace("/", "").replace(":", "")
    return config.DATA_DIR / f"{config.EXCHANGE}_{safe}_{timeframe}.csv"


def download_ohlcv(symbol: str, timeframe: str, days: int,
                   exchange=None) -> pd.DataFrame:
    """Fetch `days` of OHLCV by walking forward from `since` in pages.

    Binance returns at most ~1000 candles per call, so we loop, advancing the
    `since` cursor to one bar past the last candle received until we reach now.
    Returns a UTC-indexed DataFrame with columns Open/High/Low/Close/Volume
    (capitalized for backtesting.py compatibility).
    """
    ex = exchange or _exchange()
    tf_ms = ex.parse_timeframe(timeframe) * 1000          # bar length in ms
    since = ex.milliseconds() - days * 24 * 60 * 60 * 1000
    limit = 1000
    all_rows: list[list] = []

    while True:
        batch = ex.fetch_ohlcv(symbol, timeframe=timeframe, since=since, limit=limit)
        if not batch:
            break
        all_rows += batch
        last_ts = batch[-1][0]
        next_since = last_ts + tf_ms
        if next_since <= since or last_ts >= ex.milliseconds() - tf_ms:
            break                                          # caught up to now
        since = next_since
        print(f"  {symbol} {timeframe}: {len(all_rows):>6} bars "
              f"(up to {pd.to_datetime(last_ts, unit='ms')})", end="\r")
        time.sleep(ex.rateLimit / 1000)                    # be polite to the API

    print()
    if not all_rows:
        raise RuntimeError(f"No data returned for {symbol} {timeframe}")

    df = pd.DataFrame(all_rows, columns=["ts", "Open", "High", "Low", "Close", "Volume"])
    df = df.drop_duplicates(subset="ts").sort_values("ts")
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df = df.set_index("ts")
    # drop the still-forming last bar so indicators don't see a partial candle
    return df.iloc[:-1]


def load_ohlcv(symbol: str = config.SYMBOL, timeframe: str = "5m",
               days: int = config.HISTORY_DAYS, refresh: bool = False) -> pd.DataFrame:
    """Return OHLCV, downloading + caching to CSV on first call.

    refresh=True forces a fresh download even if a cache file exists.
    """
    path = _cache_path(symbol, timeframe)
    if path.exists() and not refresh:
        df = pd.read_csv(path, index_col="ts", parse_dates=["ts"])
        return df
    df = download_ohlcv(symbol, timeframe, days)
    df.to_csv(path)
    print(f"  saved {len(df)} bars -> {path.name}")
    return df


def main(timeframes: list[str]):
    ex = _exchange()
    print(f"Downloading {config.SYMBOL} from {config.EXCHANGE} "
          f"({config.HISTORY_DAYS}d): {', '.join(timeframes)}")
    for tf in timeframes:
        df = download_ohlcv(config.SYMBOL, tf, config.HISTORY_DAYS, exchange=ex)
        path = _cache_path(config.SYMBOL, tf)
        df.to_csv(path)
        print(f"  {tf}: {len(df)} bars  {df.index[0]} -> {df.index[-1]}  "
              f"saved {path.name}")


if __name__ == "__main__":
    tfs = sys.argv[1:] or config.TIMEFRAMES
    main(tfs)
