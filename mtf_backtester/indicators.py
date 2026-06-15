"""
Indicators + the multi-timeframe machinery.

The important bit is `align_htf_to_ltf`: when you compute an indicator on a higher
timeframe and map it back onto lower-timeframe bars, you MUST only use HTF bars
that have already CLOSED, otherwise the backtest sees the future. We do that by
shifting the HTF series by one bar before forward-filling onto the LTF index.
"""
from __future__ import annotations
import numpy as np
import pandas as pd


# --------------------------------------------------------------------------- #
# plain indicators (operate on a Close Series unless noted)
# --------------------------------------------------------------------------- #
def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    h, l, c = df["High"], df["Low"], df["Close"]
    prev = c.shift(1)
    tr = pd.concat([h - l, (h - prev).abs(), (l - prev).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()


# --------------------------------------------------------------------------- #
# multi-timeframe
# --------------------------------------------------------------------------- #
def resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Aggregate an OHLCV DataFrame up to a higher timeframe (e.g. '4H').

    label/closed='left' so each HTF bar is timestamped at its OPEN — that lets us
    reason cleanly about which bars have closed when we align back down.
    """
    agg = {"Open": "first", "High": "max", "Low": "min",
           "Close": "last", "Volume": "sum"}
    cols = {k: v for k, v in agg.items() if k in df.columns}
    out = df.resample(rule, label="left", closed="left").agg(cols)
    return out.dropna(how="any")


def align_htf_to_ltf(htf_series: pd.Series, ltf_index: pd.Index) -> pd.Series:
    """Map a higher-timeframe series onto lower-timeframe bars WITHOUT lookahead.

    Steps:
      1. shift the HTF series by 1 -> at any HTF bar we expose the PREVIOUS
         (fully closed) HTF value, never the bar still forming.
      2. reindex onto the LTF index with forward-fill -> every LTF bar carries the
         most recent closed-HTF value.
    """
    closed = htf_series.shift(1)
    return closed.reindex(ltf_index, method="ffill")


def add_mtf_features(ltf: pd.DataFrame, htf_rule: str, htf_ma_period: int,
                     rsi_period: int, atr_period: int) -> pd.DataFrame:
    """Return a copy of the LTF frame with LTF + aligned-HTF indicator columns.

    Columns added:
      RSI         : RSI on the LTF close (the entry trigger)
      ATR         : ATR on the LTF (for stops / sizing)
      HTF_Close   : last closed HTF close, aligned to each LTF bar
      HTF_MA      : MA of the HTF close, aligned (the trend filter)
    """
    out = ltf.copy()
    out["RSI"] = rsi(out["Close"], rsi_period)
    out["ATR"] = atr(out, atr_period)

    htf = resample_ohlcv(ltf, htf_rule)
    htf_ma = sma(htf["Close"], htf_ma_period)
    out["HTF_Close"] = align_htf_to_ltf(htf["Close"], out.index)
    out["HTF_MA"] = align_htf_to_ltf(htf_ma, out.index)
    return out
