"""
Timeframe sweep: run the same strategy across several LTFs and rank by
risk-adjusted performance (Sharpe), so you can see which timeframe the edge
actually lives on.

    python optimize_timeframe.py                 # uses config.TIMEFRAMES
    python optimize_timeframe.py 1m 5m 15m 1h

Note: a higher Sharpe on a finer timeframe often just means more trades crossing
more spread/fees. The COMMISSION in config is what keeps this honest — keep it
realistic. Treat the ranking as a hypothesis to confirm out-of-sample.
"""
from __future__ import annotations
import sys
import pandas as pd

import config
from run import run_timeframe


def sweep(timeframes: list[str]) -> pd.DataFrame:
    rows = []
    for tf in timeframes:
        try:
            rows.append(run_timeframe(tf, plot=False))
        except Exception as e:                       # keep going if one TF fails
            print(f"  [{tf}] skipped: {e}")
    if not rows:
        raise RuntimeError("No timeframe produced results.")
    table = pd.DataFrame(rows).sort_values("sharpe", ascending=False)
    return table.reset_index(drop=True)


if __name__ == "__main__":
    tfs = sys.argv[1:] or config.TIMEFRAMES
    table = sweep(tfs)
    print("\n================ TIMEFRAME RANKING (by Sharpe) ================")
    print(table.to_string(index=False))
    out = config.RESULTS_DIR / "timeframe_ranking.csv"
    table.to_csv(out, index=False)
    print(f"\nsaved -> {out}")
    best = table.iloc[0]
    print(f"\nBest risk-adjusted: {best['timeframe']}  "
          f"(Sharpe {best['sharpe']}, PF {best['profit_factor']}, "
          f"DD {best['max_drawdown']}, {int(best['trades'])} trades)")
