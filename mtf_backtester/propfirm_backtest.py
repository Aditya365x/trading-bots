"""
Thorough multi-timeframe backtest of PropFirmHyper-RobustRiskEngine.py on all
cached BTC/USDT history.

What it does
------------
- Uses the engine's OWN indicators (200 EMA, ADX, MACD) and its OWN signal matrix
  (uptrend + ADX>21 + MACD cross), hard stop, trailing stop, 1%-risk sizing and
  3.5% daily-loss circuit breaker.
- Fixes the two things that make the live engine un-backtestable:
    * daily breaker keyed to each CANDLE's UTC date (not datetime.now()),
    * fractional position sizing (the live int(floor(...)) rounds BTC to 0 units).
- Adds realistic costs (commission+slippage per side) so results aren't fantasy.
- No lookahead: a signal on the close of bar i is executed at the OPEN of bar i+1;
  stops/trailing are checked intrabar with each later bar's high/low.

Run:
    python propfirm_backtest.py                # all timeframes in config
    python propfirm_backtest.py 1h 4h
"""
from __future__ import annotations
import sys
import numpy as np
import pandas as pd

import config
import data_loader

# import the engine from the hyphenated filename
import importlib.util
_spec = importlib.util.spec_from_file_location(
    "propfirm_engine", str(config.ROOT / "PropFirmHyper-RobustRiskEngine.py"))
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
PropFirmRiskEngine = _mod.PropFirmRiskEngine

# ---- backtest economics ----
COMMISSION = 0.0006          # 0.06% per side (fee + slippage). Applied entry & exit.

_BARS_PER_YEAR = {"1m": 525_600, "5m": 105_120, "15m": 35_040, "30m": 17_520,
                  "1h": 8_760, "2h": 4_380, "4h": 2_190, "1d": 365}
_BARS_PER_DAY = {"1m": 1440, "5m": 288, "15m": 96, "30m": 48,
                 "1h": 24, "2h": 12, "4h": 6, "1d": 1}


# =============================================================================
# BACKTEST LOOP
# =============================================================================
def backtest(df: pd.DataFrame, timeframe: str, initial_capital: float = 10_000.0):
    eng = PropFirmRiskEngine(initial_capital=initial_capital)
    df = eng.calculate_indicators(df.copy())

    o = df["open"].values
    h = df["high"].values
    l = df["low"].values
    c = df["close"].values
    ema = df["ema_200"].values
    adx = df["adx"].values
    macd = df["macd"].values
    sig = df["signal"].values
    dates = df.index.normalize()                      # candle UTC day
    n = len(df)

    HS = eng.HARD_STOP_PCT
    TA = eng.TRAIL_ACTIVATE_PCT
    TO = eng.TRAIL_OFFSET_PCT
    ADXT = eng.ADX_THRESHOLD
    risk_pct = eng.risk_per_trade_pct
    daily_limit = eng.max_daily_loss_pct

    equity = initial_capital                          # realized cash
    pos = None                                        # open position dict or None
    trades = []
    equity_curve = np.empty(n)
    bars_in_market = 0

    day = None
    day_start_equity = equity
    day_locked = False

    def close_pos(t, price, reason):
        nonlocal equity, pos
        d = pos["dir"]
        gross = d * (price - pos["entry"]) * pos["qty"]
        exit_fee = price * pos["qty"] * COMMISSION
        pnl = gross - pos["entry_fee"] - exit_fee
        equity += pnl
        risk_cash = pos["entry"] * HS * pos["qty"]
        trades.append({
            "entry_time": df.index[pos["bar_in"]], "exit_time": df.index[t],
            "dir": "long" if d == 1 else "short", "entry": pos["entry"],
            "exit": price, "qty": pos["qty"], "bars_held": t - pos["bar_in"],
            "pnl": pnl, "R": pnl / risk_cash if risk_cash else 0.0,
            "return_pct": d * (price / pos["entry"] - 1) * 100, "reason": reason,
            "equity": equity,
        })
        pos = None

    for t in range(n):
        # new UTC day -> reset daily loss budget
        if dates[t] != day:
            day = dates[t]
            day_start_equity = equity
            day_locked = False

        # ---- 1) entry at THIS bar's open, using the signal from bar t-1 (closed) ----
        if pos is None and not day_locked and t >= 1:
            if not (np.isnan(ema[t-1]) or np.isnan(adx[t-1]) or np.isnan(macd[t-1])):
                price0 = c[t-1]
                trending = adx[t-1] > ADXT
                up = price0 > ema[t-1]
                down = price0 < ema[t-1]
                x_up = macd[t-2] <= sig[t-2] and macd[t-1] > sig[t-1] if t >= 2 else False
                x_dn = macd[t-2] >= sig[t-2] and macd[t-1] < sig[t-1] if t >= 2 else False
                direction = 1 if (up and trending and x_up) else (-1 if (down and trending and x_dn) else 0)
                if direction != 0:
                    entry = o[t]
                    stop_dist_cash = entry * HS
                    qty = (equity * risk_pct) / stop_dist_cash      # fractional, 1% risk
                    if qty > 0:
                        pos = {
                            "dir": direction, "entry": entry, "qty": qty, "bar_in": t,
                            "stop": entry * (1 - direction * HS),
                            "trail_act": entry * (1 + direction * TA),
                            "trail_off": entry * TO, "activated": False,
                            "entry_fee": entry * qty * COMMISSION,
                        }

        # ---- 2) manage the open position over THIS bar (intrabar stop/trail) ----
        if pos is not None:
            d = pos["dir"]
            if d == 1:
                if l[t] <= pos["stop"]:
                    close_pos(t, pos["stop"], "trail" if pos["activated"] else "stop")
                else:
                    if not pos["activated"] and h[t] >= pos["trail_act"]:
                        pos["activated"] = True
                    if pos["activated"]:
                        pos["stop"] = max(pos["stop"], h[t] - pos["trail_off"])
            else:
                if h[t] >= pos["stop"]:
                    close_pos(t, pos["stop"], "trail" if pos["activated"] else "stop")
                else:
                    if not pos["activated"] and l[t] <= pos["trail_act"]:
                        pos["activated"] = True
                    if pos["activated"]:
                        pos["stop"] = min(pos["stop"], l[t] + pos["trail_off"])

        # ---- 3) daily circuit breaker on mark-to-market equity ----
        mtm = equity + (pos["dir"] * (c[t] - pos["entry"]) * pos["qty"] if pos else 0.0)
        if not day_locked and (day_start_equity - mtm) >= day_start_equity * daily_limit:
            day_locked = True
            if pos is not None:
                close_pos(t, c[t], "daily_breaker")

        if pos is not None:
            bars_in_market += 1
        equity_curve[t] = equity + (pos["dir"] * (c[t] - pos["entry"]) * pos["qty"] if pos else 0.0)

    if pos is not None:
        close_pos(n - 1, c[-1], "eod")
        equity_curve[-1] = equity

    return (pd.DataFrame(trades), pd.Series(equity_curve, index=df.index),
            bars_in_market, n)


# =============================================================================
# METRICS  — "all the metrics"
# =============================================================================
def metrics(trades: pd.DataFrame, equity: pd.Series, timeframe: str,
            initial_capital: float, bars_in_market: int, total_bars: int) -> dict:
    ppy = _BARS_PER_YEAR.get(timeframe, 8_760)
    bpd = _BARS_PER_DAY.get(timeframe, 24)
    years = total_bars / ppy
    final_eq = float(equity.iloc[-1])
    net = final_eq - initial_capital

    # drawdown
    roll_max = equity.cummax()
    dd = equity / roll_max - 1.0
    max_dd = float(dd.min())
    # max DD duration (bars under water)
    under = equity < roll_max
    dur = maxdur = 0
    for u in under.values:
        dur = dur + 1 if u else 0
        maxdur = max(maxdur, dur)

    # per-bar return ratios
    rets = equity.pct_change().dropna()
    sharpe = float(rets.mean() / rets.std() * np.sqrt(ppy)) if rets.std() > 0 else 0.0
    downside = rets[rets < 0]
    sortino = float(rets.mean() / downside.std() * np.sqrt(ppy)) if len(downside) and downside.std() > 0 else 0.0
    cagr = (final_eq / initial_capital) ** (1 / years) - 1 if years > 0 and final_eq > 0 else 0.0
    calmar = float(cagr / abs(max_dd)) if max_dd < 0 else 0.0

    out = {
        "timeframe": timeframe,
        "bars": total_bars,
        "years_of_data": round(years, 2),
        "initial_capital": initial_capital,
        "final_equity": round(final_eq, 2),
        "net_profit": round(net, 2),
        "return_pct": round(net / initial_capital * 100, 2),
        "cagr_pct": round(cagr * 100, 2),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "max_dd_duration_days": round(maxdur / bpd, 1),
        "sharpe": round(sharpe, 2),
        "sortino": round(sortino, 2),
        "calmar": round(calmar, 2),
        "exposure_pct": round(bars_in_market / total_bars * 100, 1),
    }

    if trades.empty:
        out["trades"] = 0
        out["note"] = "No trades."
        return out

    wins = trades[trades.pnl > 0]
    losses = trades[trades.pnl <= 0]
    longs = trades[trades.dir == "long"]
    shorts = trades[trades.dir == "short"]
    gross_win = wins.pnl.sum()
    gross_loss = losses.pnl.sum()

    # streaks
    win_flags = (trades.pnl > 0).values
    cw = cl = mcw = mcl = 0
    for w in win_flags:
        if w:
            cw += 1; cl = 0
        else:
            cl += 1; cw = 0
        mcw = max(mcw, cw); mcl = max(mcl, cl)

    out.update({
        "trades": len(trades),
        "longs": len(longs),
        "shorts": len(shorts),
        "win_rate_pct": round(len(wins) / len(trades) * 100, 1),
        "win_rate_long_pct": round((longs.pnl > 0).mean() * 100, 1) if len(longs) else 0.0,
        "win_rate_short_pct": round((shorts.pnl > 0).mean() * 100, 1) if len(shorts) else 0.0,
        "profit_factor": round(gross_win / abs(gross_loss), 2) if gross_loss != 0 else float("inf"),
        "expectancy_usd": round(trades.pnl.mean(), 2),
        "expectancy_R": round(trades.R.mean(), 3),
        "avg_win_usd": round(wins.pnl.mean(), 2) if len(wins) else 0.0,
        "avg_loss_usd": round(losses.pnl.mean(), 2) if len(losses) else 0.0,
        "payoff_ratio": round(wins.pnl.mean() / abs(losses.pnl.mean()), 2) if len(wins) and len(losses) and losses.pnl.mean() != 0 else 0.0,
        "largest_win_usd": round(trades.pnl.max(), 2),
        "largest_loss_usd": round(trades.pnl.min(), 2),
        "gross_profit_usd": round(gross_win, 2),
        "gross_loss_usd": round(gross_loss, 2),
        "max_consec_wins": int(mcw),
        "max_consec_losses": int(mcl),
        "avg_bars_held": round(trades.bars_held.mean(), 1),
        "exits_stop": int((trades.reason == "stop").sum()),
        "exits_trail": int((trades.reason == "trail").sum()),
        "exits_daily_breaker": int((trades.reason == "daily_breaker").sum()),
    })
    return out


def print_report(m: dict):
    print(f"\n{'='*60}")
    print(f"  PropFirm engine | {m['timeframe']} | {m['bars']} bars "
          f"({m['years_of_data']}y)")
    print('='*60)
    groups = [
        ("RETURNS", ["final_equity", "net_profit", "return_pct", "cagr_pct"]),
        ("RISK", ["max_drawdown_pct", "max_dd_duration_days", "sharpe",
                  "sortino", "calmar", "exposure_pct"]),
        ("TRADES", ["trades", "longs", "shorts", "win_rate_pct",
                    "win_rate_long_pct", "win_rate_short_pct", "profit_factor",
                    "expectancy_usd", "expectancy_R", "payoff_ratio"]),
        ("WIN/LOSS", ["avg_win_usd", "avg_loss_usd", "largest_win_usd",
                      "largest_loss_usd", "gross_profit_usd", "gross_loss_usd",
                      "max_consec_wins", "max_consec_losses", "avg_bars_held"]),
        ("EXITS", ["exits_stop", "exits_trail", "exits_daily_breaker"]),
    ]
    for title, keys in groups:
        print(f"\n  -- {title} --")
        for k in keys:
            if k in m:
                print(f"  {k:>22}: {m[k]}")
    if "note" in m:
        print(f"  {'note':>22}: {m['note']}")


# =============================================================================
def main(timeframes):
    summary = []
    for tf in timeframes:
        raw = data_loader.load_ohlcv(config.SYMBOL, tf, config.HISTORY_DAYS)
        df = raw.rename(columns=str.lower)[["open", "high", "low", "close"]]
        trades, equity, bim, n = backtest(df, tf)
        m = metrics(trades, equity, tf, 10_000.0, bim, n)
        print_report(m)
        trades.to_csv(config.RESULTS_DIR / f"propfirm_trades_{tf}.csv", index=False)
        summary.append(m)

    table = pd.DataFrame(summary)
    cols = ["timeframe", "trades", "win_rate_pct", "profit_factor", "return_pct",
            "cagr_pct", "max_drawdown_pct", "sharpe", "sortino", "calmar",
            "expectancy_R", "exposure_pct"]
    cols = [c for c in cols if c in table.columns]
    print(f"\n\n{'#'*70}\n  CROSS-TIMEFRAME SUMMARY (commission {COMMISSION*100:.2f}%/side)\n{'#'*70}")
    print(table[cols].to_string(index=False))
    out = config.RESULTS_DIR / "propfirm_summary.csv"
    table.to_csv(out, index=False)
    print(f"\nsaved summary -> {out}")
    print("per-timeframe trade logs -> results/propfirm_trades_<tf>.csv")


if __name__ == "__main__":
    main(sys.argv[1:] or config.TIMEFRAMES)
