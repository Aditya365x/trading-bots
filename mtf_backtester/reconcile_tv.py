"""
Reconcile the Python backtest with the TradingView Strategy Tester result.
Pulls the SAME window TV used (Jan 1 2025 -> now) and re-runs the PropFirm engine
across more timeframes (incl 2h/3h) so we compare apples to apples.
"""
from __future__ import annotations
import pandas as pd

import config
import data_loader
from propfirm_backtest import backtest, metrics, print_report

START = "2025-01-01"
TFS = ["1h", "2h", "4h", "6h"]      # Binance has no 3h interval

rows = []
for tf in TFS:
    raw = data_loader.download_ohlcv(config.SYMBOL, tf, days=540)   # fresh, long pull
    raw = raw[raw.index >= START]
    df = raw.rename(columns=str.lower)[["open", "high", "low", "close"]]
    trades, equity, bim, n = backtest(df, tf)
    m = metrics(trades, equity, tf, 10_000.0, bim, n)
    m["start"] = str(raw.index[0].date())
    m["end"] = str(raw.index[-1].date())
    print_report(m)
    rows.append(m)

table = pd.DataFrame(rows)
cols = ["timeframe", "start", "end", "trades", "win_rate_pct", "profit_factor",
        "return_pct", "max_drawdown_pct", "sharpe", "avg_bars_held"]
cols = [c for c in cols if c in table.columns]
print("\n===== PYTHON (no-lookahead, 0.06%/side) on TV's window =====")
print(table[cols].to_string(index=False))
print("\nTradingView reported: 115 trades | win 56.5% | PF 1.571 | "
      "ret +33.9% | DD 4.89% | Sharpe 0.753 | avg 9 bars")
