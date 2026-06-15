"""
Central configuration for the MTF backtester.
Tune everything here; the other modules import from this file.
"""
from __future__ import annotations
from pathlib import Path

# ---- paths ----
ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
RESULTS_DIR = ROOT / "results"
DATA_DIR.mkdir(exist_ok=True)
RESULTS_DIR.mkdir(exist_ok=True)

# ---- market / data ----
EXCHANGE = "binance"          # any ccxt exchange id; "binance" public OHLCV needs no keys
SYMBOL = "BTC/USDT"
# Timeframes to download / test. ccxt timeframe strings.
TIMEFRAMES = ["5m", "15m", "1h", "4h"]
# How far back to pull, in days. Binance caps ~1000 candles/request; we paginate.
HISTORY_DAYS = 365

# ---- higher-timeframe (HTF) bias settings ----
# The HTF used for the trend filter, resampled from whatever LTF we are testing.
HTF_RULE = "4h"               # pandas resample rule for the bias timeframe (lowercase unit, modern pandas)
HTF_MA_PERIOD = 50            # MA period on the HTF for trend direction

# ---- lower-timeframe (LTF) signal settings ----
RSI_PERIOD = 14
RSI_OVERSOLD = 35
RSI_OVERBOUGHT = 65
ATR_PERIOD = 14

# ---- backtest economics ----
INITIAL_CASH = 10_000
COMMISSION = 0.0006           # 0.06% per side (taker-ish, covers fee+slippage)
