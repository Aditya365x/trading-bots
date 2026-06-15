"""
End-to-end single-timeframe run: load data -> add MTF features -> backtest ->
report -> optional plot.

    python run.py            # uses config defaults (first TF in config.TIMEFRAMES)
    python run.py 15m        # run one specific timeframe
    python run.py 15m --plot
"""
from __future__ import annotations
import sys

from backtesting import Backtest

import config
import data_loader
import indicators
import performance
from strategy import MTFTrendRSI


# price-dimensioned columns that must be scaled together; RSI (0-100) and Volume
# are intentionally left untouched.
_PRICE_COLS = ["Open", "High", "Low", "Close", "ATR", "HTF_Close", "HTF_MA"]


def _scale_prices(feat):
    feat = feat.copy()
    scale = 1.0 / feat["Close"].median()      # bring scaled price to ~1
    for col in _PRICE_COLS:
        if col in feat.columns:
            feat[col] = feat[col] * scale
    return feat


def run_timeframe(timeframe: str, plot: bool = False):
    df = data_loader.load_ohlcv(config.SYMBOL, timeframe, config.HISTORY_DAYS)
    feat = indicators.add_mtf_features(
        df, config.HTF_RULE, config.HTF_MA_PERIOD,
        config.RSI_PERIOD, config.ATR_PERIOD,
    ).dropna()

    # backtesting.py only trades WHOLE units, so a $10k account can't buy 1 BTC
    # (~$100k) -> every order is canceled and you get 0 trades. We scale every
    # price-like column by a constant so the scaled price is ~1; whole-unit
    # rounding then becomes negligible. All reported metrics (return %, Sharpe,
    # drawdown, win rate, profit factor) are scale-invariant, so this is safe.
    feat = _scale_prices(feat)

    bt = Backtest(feat, MTFTrendRSI, cash=config.INITIAL_CASH,
                  commission=config.COMMISSION, trade_on_close=False,
                  exclusive_orders=True)
    stats = bt.run()
    metrics = performance.summarize(stats, timeframe)
    performance.print_summary(metrics)

    if plot:
        out = config.RESULTS_DIR / f"bt_{timeframe}.html"
        bt.plot(filename=str(out), open_browser=False)
        print(f"  plot -> {out}")
    return metrics


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    tf = args[0] if args else config.TIMEFRAMES[0]
    run_timeframe(tf, plot="--plot" in sys.argv)
