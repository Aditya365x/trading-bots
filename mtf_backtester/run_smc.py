"""
Backtest the SMC strategy (smc_strategy_adi.py) on the real cached OHLCV data,
swept across timeframes and ranked.

This runs the strategy's OWN event-driven Backtester (faithful to your exact
logic) — it does NOT re-implement it in backtesting.py — and just feeds it the
data downloaded by data_loader.py.

    python run_smc.py              # sweep config.TIMEFRAMES
    python run_smc.py 1h 4h        # specific timeframes
"""
from __future__ import annotations
import sys
import pandas as pd

import config
import data_loader
from smc_strategy_adi import Config, Backtester

# 5m-bars-per-day equivalents, so the daily-loss circuit breaker buckets a real
# trading day on each timeframe (default in the file is 6 = 4h).
_BARS_PER_DAY = {"1m": 1440, "5m": 288, "15m": 96, "30m": 48,
                 "1h": 24, "2h": 12, "4h": 6, "1d": 1}


def _to_lower_ohlc(df: pd.DataFrame) -> pd.DataFrame:
    """data_loader gives Open/High/Low/Close (+ ts index); the SMC engine wants
    lowercase open/high/low/close with a plain RangeIndex."""
    out = df.rename(columns=str.lower).reset_index(drop=True)
    return out[["open", "high", "low", "close"]]


def run_timeframe(timeframe: str) -> dict | None:
    df = _to_lower_ohlc(
        data_loader.load_ohlcv(config.SYMBOL, timeframe, config.HISTORY_DAYS))
    cfg = Config(bars_per_day=_BARS_PER_DAY.get(timeframe, 6))
    report = Backtester(df, cfg).run()

    print(f"\n  === {timeframe}  (bars={len(df)}) ===")
    for k, v in report.items():
        print(f"  {k:>22}: {v}")

    if "trades" not in report:        # no-trades case
        return {"timeframe": timeframe, "trades": 0, "win_rate": 0.0,
                "profit_factor": 0.0, "sharpe_naive": 0.0,
                "max_drawdown_pct": 0.0, "total_return_pct": 0.0}
    return {
        "timeframe": timeframe,
        "trades": report["trades"],
        "win_rate": report["win_rate"],
        "profit_factor": report["profit_factor"],
        "sharpe_naive": report["sharpe_naive"],
        "max_drawdown_pct": report["max_drawdown_pct"],
        "total_return_pct": report["total_return_pct"],
    }


def sweep(timeframes: list[str]) -> pd.DataFrame:
    rows = []
    for tf in timeframes:
        try:
            r = run_timeframe(tf)
            if r:
                rows.append(r)
        except Exception as e:
            print(f"  [{tf}] skipped: {e}")
    if not rows:
        raise RuntimeError("No timeframe produced results.")
    return pd.DataFrame(rows).sort_values(
        "total_return_pct", ascending=False).reset_index(drop=True)


if __name__ == "__main__":
    tfs = sys.argv[1:] or config.TIMEFRAMES
    table = sweep(tfs)
    print("\n============ SMC TIMEFRAME RANKING (by total return) ============")
    print(table.to_string(index=False))
    out = config.RESULTS_DIR / "smc_timeframe_ranking.csv"
    table.to_csv(out, index=False)
    print(f"\nsaved -> {out}")
    best = table.iloc[0]
    print(f"\nBest: {best['timeframe']}  "
          f"(return {best['total_return_pct']}%, PF {best['profit_factor']}, "
          f"DD {best['max_drawdown_pct']}%, {int(best['trades'])} trades)")
    print("\nNote: sharpe_naive uses a fixed sqrt(252) annualization (from the SMC "
          "file), so it is NOT comparable across timeframes — rank on return / PF / DD.")
