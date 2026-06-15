"""
Performance reporting, computed independently from the trade list / equity curve
so you understand each number (rather than just trusting the library).

backtesting.py already reports most of these; we recompute to (a) document the
formulas and (b) keep a single comparable schema across timeframes.
"""
from __future__ import annotations
import numpy as np
import pandas as pd


def win_rate(trades: pd.DataFrame) -> float:
    if trades.empty:
        return 0.0
    return float((trades["PnL"] > 0).mean())


def profit_factor(trades: pd.DataFrame) -> float:
    if trades.empty:
        return 0.0
    gross_win = trades.loc[trades["PnL"] > 0, "PnL"].sum()
    gross_loss = trades.loc[trades["PnL"] < 0, "PnL"].sum()
    if gross_loss == 0:
        return float("inf") if gross_win > 0 else 0.0
    return float(gross_win / abs(gross_loss))


def sharpe_ratio(equity: pd.Series, periods_per_year: float) -> float:
    """Annualized Sharpe from the per-bar equity curve (risk-free = 0)."""
    rets = equity.pct_change().dropna()
    if rets.std() == 0 or rets.empty:
        return 0.0
    return float(rets.mean() / rets.std() * np.sqrt(periods_per_year))


def max_drawdown(equity: pd.Series) -> float:
    """Worst peak-to-trough drop as a fraction (e.g. -0.23 = -23%)."""
    if equity.empty:
        return 0.0
    dd = equity / equity.cummax() - 1.0
    return float(dd.min())


# bars per year for the annualization factor, keyed by ccxt timeframe
_BARS_PER_YEAR = {
    "1m": 525_600, "5m": 105_120, "15m": 35_040, "30m": 17_520,
    "1h": 8_760, "2h": 4_380, "4h": 2_190, "1d": 365,
}


def summarize(stats, timeframe: str) -> dict:
    """Build a comparable metrics dict from a backtesting.py `stats` object.

    `stats._equity_curve` and `stats._trades` are attached by backtesting.py.
    """
    equity = stats._equity_curve["Equity"]
    trades = stats._trades
    ppy = _BARS_PER_YEAR.get(timeframe, 8_760)
    return {
        "timeframe": timeframe,
        "trades": int(len(trades)),
        "win_rate": round(win_rate(trades), 3),
        "profit_factor": round(profit_factor(trades), 2),
        "sharpe": round(sharpe_ratio(equity, ppy), 2),
        "max_drawdown": round(max_drawdown(equity), 3),
        "return_pct": round(float(stats["Return [%]"]), 2),
    }


def print_summary(metrics: dict):
    print(f"\n  === {metrics['timeframe']} ===")
    for k in ("trades", "win_rate", "profit_factor", "sharpe",
              "max_drawdown", "return_pct"):
        print(f"  {k:>15}: {metrics[k]}")
