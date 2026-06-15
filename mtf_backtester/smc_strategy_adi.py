"""
=============================================================================
ICT / Smart-Money-Concepts mechanical strategy + risk management
For crypto perpetuals / spot (BTC, ETH, SOL)
=============================================================================

HONEST DISCLAIMER (read this):
  This is NOT a money-printer and is NOT "proven to work". It is a mechanical,
  backtestable implementation of an SMC-style edge (market-structure shift +
  Fair-Value-Gap retrace) wrapped in a *rigorous* risk-management layer.

  The ENTRY logic is a hypothesis you must validate on your own out-of-sample
  data. The RISK logic is the part that actually protects you: fixed-fractional
  position sizing, hard stops, breakeven + ATR trailing, and account-level
  circuit breakers (max daily loss, max drawdown, consecutive-loss lockout).

  Backtest results overstate live performance because of fees, slippage,
  funding, latency, and overfitting. Always paper-trade before risking capital.
  This is not financial advice.

Strategy in one paragraph:
  1. Track market structure via confirmed swing highs/lows.
  2. A close beyond the last opposing swing = Break of Structure (BOS) /
     Change of Character (CHoCH) -> sets directional bias.
  3. Detect 3-candle Fair Value Gaps (price imbalances) in the bias direction.
  4. Enter when price retraces *into* a fresh FVG (discount in an uptrend /
     premium in a downtrend).
  5. Stop beyond the FVG (ATR-buffered). Target = R-multiple. Move to breakeven
     at +1R, then ATR-trail.

Run:  python smc_strategy.py            # runs on synthetic data, no deps beyond numpy/pandas
      python smc_strategy.py --live BTC/USDT 4h   # needs:  pip install ccxt
=============================================================================
"""

from __future__ import annotations
import argparse
from dataclasses import dataclass, field
from typing import Optional, List, Dict
import numpy as np
import pandas as pd


# =============================================================================
# CONFIG — every knob you'd tune lives here
# =============================================================================
@dataclass
class Config:
    # ---- account ----
    initial_equity: float = 10_000.0

    # ---- structure / signal ----
    swing_left: int = 3            # bars to the left for a swing pivot
    swing_right: int = 3           # bars to the right (signal confirmed `right` bars late -> no lookahead)
    fvg_max_age: int = 30          # discard an FVG zone if not filled within N bars
    require_bias_alignment: bool = True  # only take FVGs in the BOS direction

    # ---- risk per trade ----
    risk_per_trade_pct: float = 0.01      # 1% of equity risked per trade
    max_leverage: float = 5.0             # HARD cap: notional <= equity * max_leverage
    min_stop_atr: float = 0.5             # stop must be >= this * ATR away (prevents qty blowups)
    atr_period: int = 14
    atr_stop_buffer: float = 0.5          # stop placed FVG_edge - buffer*ATR
    reward_multiple: float = 2.0          # take-profit at 2R
    move_to_breakeven_at_R: float = 1.0   # lock breakeven once +1R reached
    use_atr_trailing: bool = True
    atr_trail_mult: float = 2.0           # trail by 2*ATR once in profit

    # ---- costs (be realistic) ----
    fee_pct: float = 0.0005               # 5 bps taker per side
    slippage_pct: float = 0.0003          # 3 bps assumed slippage per side

    # ---- account-level circuit breakers (the "wrong direction" protection) ----
    max_daily_loss_pct: float = 0.03      # stop trading for the day after -3%
    max_drawdown_pct: float = 0.20        # halt the whole system after -20% from peak
    max_consecutive_losses: int = 4       # lockout for `lockout_bars` after N losses in a row
    lockout_bars: int = 12
    max_concurrent_positions: int = 1     # single-symbol engine here; keep at 1

    bars_per_day: int = 6                 # used to bucket the daily-loss limit (e.g. 6 = 4h bars)


# =============================================================================
# INDICATORS
# =============================================================================
def atr(df: pd.DataFrame, period: int) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    prev_c = c.shift(1)
    tr = pd.concat([h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def is_swing_high(highs: np.ndarray, i: int, left: int, right: int) -> bool:
    win = highs[i - left : i + right + 1]
    return highs[i] == win.max() and (win == highs[i]).sum() == 1


def is_swing_low(lows: np.ndarray, i: int, left: int, right: int) -> bool:
    win = lows[i - left : i + right + 1]
    return lows[i] == win.min() and (win == lows[i]).sum() == 1


# =============================================================================
# RISK MANAGER — the core of your "what if it goes wrong" plan
# =============================================================================
@dataclass
class RiskManager:
    cfg: Config
    equity: float = field(init=False)
    peak_equity: float = field(init=False)
    consecutive_losses: int = 0
    lockout_until_bar: int = -1
    day_start_equity: float = field(init=False)
    current_day: int = -1
    halted: bool = False           # hard halt (max drawdown breached)

    def __post_init__(self):
        self.equity = self.cfg.initial_equity
        self.peak_equity = self.cfg.initial_equity
        self.day_start_equity = self.cfg.initial_equity

    def new_bar(self, bar_index: int):
        day = bar_index // self.cfg.bars_per_day
        if day != self.current_day:
            self.current_day = day
            self.day_start_equity = self.equity  # reset daily loss budget

    def can_open(self, bar_index: int) -> bool:
        if self.halted:
            return False
        # drawdown halt
        if self.equity <= self.peak_equity * (1 - self.cfg.max_drawdown_pct):
            self.halted = True
            return False
        # daily loss limit
        if self.equity <= self.day_start_equity * (1 - self.cfg.max_daily_loss_pct):
            return False
        # consecutive-loss lockout
        if bar_index < self.lockout_until_bar:
            return False
        return True

    def position_size(self, entry: float, stop: float) -> float:
        """Fixed-fractional sizing: risk a constant % of equity per trade,
        then HARD-CAP by max leverage so a tight stop can never blow up notional.
        Returns position size in units of the asset (qty)."""
        risk_cash = self.equity * self.cfg.risk_per_trade_pct
        per_unit_risk = abs(entry - stop)
        if per_unit_risk <= 0 or entry <= 0:
            return 0.0
        qty = risk_cash / per_unit_risk
        # leverage cap: notional = entry * qty must not exceed equity * max_leverage
        max_qty = (self.equity * self.cfg.max_leverage) / entry
        return min(qty, max_qty)

    def register_trade(self, pnl: float, bar_index: int):
        self.equity += pnl
        self.peak_equity = max(self.peak_equity, self.equity)
        if pnl < 0:
            self.consecutive_losses += 1
            if self.consecutive_losses >= self.cfg.max_consecutive_losses:
                self.lockout_until_bar = bar_index + self.cfg.lockout_bars
                self.consecutive_losses = 0  # reset after triggering lockout
        else:
            self.consecutive_losses = 0


# =============================================================================
# BACKTESTER — bar-by-bar, no lookahead
# =============================================================================
class Backtester:
    def __init__(self, df: pd.DataFrame, cfg: Config):
        self.df = df.reset_index(drop=True)
        self.cfg = cfg
        self.rm = RiskManager(cfg)
        self.df["atr"] = atr(self.df, cfg.atr_period)

        self.bias = 0                     # +1 bullish, -1 bearish
        self.last_swing_high: Optional[float] = None
        self.last_swing_low: Optional[float] = None
        self.fvgs: List[Dict] = []        # active gap zones: {dir, top, bottom, born}
        self.position: Optional[Dict] = None
        self.trades: List[Dict] = []
        self.equity_curve: List[float] = []

    # ---- structure ----
    def _update_swings(self, i: int):
        r = self.cfg.swing_right
        j = i - r                          # the pivot we can *now* confirm
        if j < self.cfg.swing_left:
            return
        highs = self.df["high"].values
        lows = self.df["low"].values
        if is_swing_high(highs, j, self.cfg.swing_left, r):
            self.last_swing_high = highs[j]
        if is_swing_low(lows, j, self.cfg.swing_left, r):
            self.last_swing_low = lows[j]

    def _update_bias(self, i: int):
        close = self.df["close"].iloc[i]
        if self.last_swing_high is not None and close > self.last_swing_high:
            self.bias = 1                  # BOS / CHoCH up
            self.last_swing_high = None    # consume it
        if self.last_swing_low is not None and close < self.last_swing_low:
            self.bias = -1
            self.last_swing_low = None

    def _detect_fvg(self, i: int):
        if i < 2:
            return
        h0, l0 = self.df["high"].iloc[i - 2], self.df["low"].iloc[i - 2]
        h2, l2 = self.df["high"].iloc[i], self.df["low"].iloc[i]
        # bullish FVG: gap between candle[i-2].high and candle[i].low
        if l2 > h0:
            if not self.cfg.require_bias_alignment or self.bias == 1:
                self.fvgs.append({"dir": 1, "top": l2, "bottom": h0, "born": i})
        # bearish FVG: gap between candle[i].high and candle[i-2].low
        if h2 < l0:
            if not self.cfg.require_bias_alignment or self.bias == -1:
                self.fvgs.append({"dir": -1, "top": l0, "bottom": h2, "born": i})

    def _prune_fvgs(self, i: int):
        self.fvgs = [z for z in self.fvgs if i - z["born"] <= self.cfg.fvg_max_age]

    # ---- entries ----
    def _try_entry(self, i: int):
        if self.position is not None:
            return
        if not self.rm.can_open(i):
            return
        bar = self.df.iloc[i]
        a = bar["atr"]
        if np.isnan(a) or a <= 0:
            return
        for z in list(self.fvgs):
            if z["dir"] != self.bias:
                continue
            # LONG: bias up, price dips into the gap (bar low touches gap top)
            if z["dir"] == 1 and bar["low"] <= z["top"]:
                entry = min(bar["open"], z["top"])
                stop = z["bottom"] - self.cfg.atr_stop_buffer * a
                stop = min(stop, entry - self.cfg.min_stop_atr * a)  # floor distance
                if stop >= entry:
                    continue
                self._open(i, +1, entry, stop, a)
                self.fvgs.remove(z)
                return
            # SHORT: bias down, price pops into the gap
            if z["dir"] == -1 and bar["high"] >= z["bottom"]:
                entry = max(bar["open"], z["bottom"])
                stop = z["top"] + self.cfg.atr_stop_buffer * a
                stop = max(stop, entry + self.cfg.min_stop_atr * a)  # floor distance
                if stop <= entry:
                    continue
                self._open(i, -1, entry, stop, a)
                self.fvgs.remove(z)
                return

    def _open(self, i: int, direction: int, entry: float, stop: float, a: float):
        qty = self.rm.position_size(entry, stop)
        if qty <= 0:
            return
        risk = abs(entry - stop)
        target = entry + direction * self.cfg.reward_multiple * risk
        fee = entry * qty * (self.cfg.fee_pct + self.cfg.slippage_pct)
        self.position = {
            "dir": direction, "entry": entry, "stop": stop, "init_stop": stop,
            "target": target, "qty": qty, "risk": risk, "atr": a,
            "bar_in": i, "entry_fee": fee, "be_moved": False,
        }

    # ---- trade management (intrabar stop/target + trailing) ----
    def _manage(self, i: int):
        if self.position is None:
            return
        p = self.position
        bar = self.df.iloc[i]
        d = p["dir"]

        # 1) hard stop first (assume worst case: stop hit before target if both in range)
        stop_hit = bar["low"] <= p["stop"] if d == 1 else bar["high"] >= p["stop"]
        if stop_hit:
            self._close(i, p["stop"], "stop")
            return

        # 2) take profit
        tp_hit = bar["high"] >= p["target"] if d == 1 else bar["low"] <= p["target"]
        if tp_hit:
            self._close(i, p["target"], "target")
            return

        # 3) breakeven move at +R
        unrealised_R = (d * (bar["close"] - p["entry"])) / p["risk"]
        if not p["be_moved"] and unrealised_R >= self.cfg.move_to_breakeven_at_R:
            p["stop"] = p["entry"] if d == 1 else p["entry"]
            p["be_moved"] = True

        # 4) ATR trailing once in profit
        if self.cfg.use_atr_trailing and unrealised_R >= self.cfg.move_to_breakeven_at_R:
            trail = bar["close"] - d * self.cfg.atr_trail_mult * p["atr"]
            if d == 1:
                p["stop"] = max(p["stop"], trail)
            else:
                p["stop"] = min(p["stop"], trail)

    def _close(self, i: int, price: float, reason: str):
        p = self.position
        d = p["dir"]
        gross = d * (price - p["entry"]) * p["qty"]
        exit_fee = price * p["qty"] * (self.cfg.fee_pct + self.cfg.slippage_pct)
        pnl = gross - p["entry_fee"] - exit_fee
        self.rm.register_trade(pnl, i)
        self.trades.append({
            "bar_in": p["bar_in"], "bar_out": i, "dir": d,
            "entry": p["entry"], "exit": price, "qty": p["qty"],
            "pnl": pnl, "R": pnl / (p["risk"] * p["qty"]) if p["qty"] else 0,
            "reason": reason, "equity": self.rm.equity,
        })
        self.position = None

    # ---- main loop ----
    def run(self):
        for i in range(len(self.df)):
            self.rm.new_bar(i)
            self._manage(i)           # manage open trade with this bar
            self._update_swings(i)
            self._update_bias(i)
            self._detect_fvg(i)
            self._prune_fvgs(i)
            self._try_entry(i)
            self.equity_curve.append(self.rm.equity)
        # close any open position at last close
        if self.position is not None:
            self._close(len(self.df) - 1, self.df["close"].iloc[-1], "eod")
        return self._report()

    # ---- metrics ----
    def _report(self) -> Dict:
        eq = pd.Series(self.equity_curve)
        trades = pd.DataFrame(self.trades)
        out = {"final_equity": float(self.rm.equity), "halted": self.rm.halted}
        if trades.empty:
            out["note"] = "No trades triggered. Loosen filters or feed more data."
            return out

        wins = trades[trades.pnl > 0]
        losses = trades[trades.pnl <= 0]
        roll_max = eq.cummax()
        dd = (eq - roll_max) / roll_max
        rets = eq.pct_change().dropna()

        out.update({
            "trades": len(trades),
            "win_rate": round(len(wins) / len(trades), 3),
            "total_return_pct": round((self.rm.equity / self.cfg.initial_equity - 1) * 100, 2),
            "profit_factor": round(wins.pnl.sum() / abs(losses.pnl.sum()), 2) if not losses.empty and losses.pnl.sum() != 0 else float("inf"),
            "expectancy_R": round(trades.R.mean(), 3),
            "avg_win_R": round(wins.R.mean(), 2) if not wins.empty else 0,
            "avg_loss_R": round(losses.R.mean(), 2) if not losses.empty else 0,
            "max_drawdown_pct": round(dd.min() * 100, 2),
            "sharpe_naive": round(rets.mean() / rets.std() * np.sqrt(252), 2) if rets.std() > 0 else 0,
        })
        return out


# =============================================================================
# DATA
# =============================================================================
def synthetic_data(n: int = 3000, seed: int = 7) -> pd.DataFrame:
    """Trending GBM with regime shifts so structure/FVG logic has something to chew on."""
    rng = np.random.default_rng(seed)
    price = 30_000.0
    rows = []
    drift = 0.0
    for k in range(n):
        if k % 250 == 0:
            drift = rng.normal(0, 0.0008)          # new regime
        ret = drift + rng.normal(0, 0.012)
        o = price
        c = price * (1 + ret)
        hi = max(o, c) * (1 + abs(rng.normal(0, 0.004)))
        lo = min(o, c) * (1 - abs(rng.normal(0, 0.004)))
        rows.append((o, hi, lo, c))
        price = c
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"])
    return df


def live_data(symbol: str, timeframe: str, limit: int = 1500) -> pd.DataFrame:
    """Fetch OHLCV via ccxt.  pip install ccxt"""
    import ccxt
    ex = ccxt.binance()
    raw = ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "volume"])
    return df


# =============================================================================
# CLI
# =============================================================================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", nargs=2, metavar=("SYMBOL", "TF"),
                    help="e.g. --live BTC/USDT 4h  (needs ccxt)")
    args = ap.parse_args()

    cfg = Config()
    if args.live:
        df = live_data(args.live[0], args.live[1])
        label = f"{args.live[0]} {args.live[1]} (LIVE)"
    else:
        df = synthetic_data()
        label = "SYNTHETIC"

    bt = Backtester(df, cfg)
    report = bt.run()

    print(f"\n=== SMC backtest: {label} | bars={len(df)} ===")
    for k, v in report.items():
        print(f"  {k:>22}: {v}")
    print("\nReminder: synthetic numbers mean nothing. Run --live on BTC/ETH/SOL,")
    print("then split your data and confirm the edge holds OUT OF SAMPLE.\n")


if __name__ == "__main__":
    main()