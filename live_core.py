"""
Shared core for the strategy bots (bot_smc.py, bot_propfirm.py).

Generalizes the helpers from bot.py and adds live trailing-stop sync: when the
account is already in the position the strategy wants, and the strategy's stop has
moved in our favour (breakeven / ATR-trail / PropFirm trail), we cancel the resting
stop and re-place it at the new level. That closes the `# TODO: trailing` gap in
the original bot.py.

The protective stop is always a REAL resting order on Delta, so a bot crash still
leaves the position protected.
"""
from __future__ import annotations
import math
import time

import pandas as pd


def candles_to_df(rows) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    for c in ("open", "high", "low", "close"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df[["open", "high", "low", "close"]].dropna().reset_index(drop=True)


def extract_usd_balance(bal) -> float:
    if bal is None:
        return 0.0
    try:
        if isinstance(bal, dict):
            return float(bal.get("available_balance") or bal.get("balance") or 0)
        if isinstance(bal, list):
            for a in bal:
                return float(a.get("available_balance") or a.get("balance") or 0)
    except Exception as e:
        print(f"[balance parse] {e}")
    return 0.0


def size_contracts(balance: float, risk_pct: float, stop_dist: float,
                   contract_value: float, price: float, max_lev: float) -> int:
    """Whole-contract sizing: risk `risk_pct` of balance over the stop distance,
    capped by `max_lev` notional. Mirrors bot.py:size_contracts."""
    if stop_dist <= 0 or contract_value <= 0 or price <= 0:
        return 0
    risk_cash = balance * risk_pct
    risk_per_contract = stop_dist * contract_value
    n = risk_cash / risk_per_contract if risk_per_contract > 0 else 0
    max_n = (balance * max_lev) / (contract_value * price) if contract_value * price > 0 else 0
    return max(int(math.floor(min(n, max_n))), 0)


def seconds_to_next_close(tf_seconds: int, settle_buffer: int = 8) -> float:
    """Seconds until the next candle close of `tf_seconds`, plus a settle buffer
    so Delta has published the closed candle before we act."""
    now = time.time()
    return (now // tf_seconds + 1) * tf_seconds - now + settle_buffer


def _round_to_tick(price: float, tick: float) -> float:
    if tick and tick > 0:
        return round(round(price / tick) * tick, 10)
    return round(price, 1)


def reconcile(broker, prod: dict, desired: dict | None, balance: float,
              risk_pct: float, max_lev: float, price: float, state: dict):
    """Make the Delta position match `desired` ({dir, entry, stop} or None).

    `state` is a per-bot dict persisting across cycles; we track the last resting
    stop in state["stop"] so trailing only re-places when it actually moves.
    """
    pid = int(prod["id"])
    cval = float(prod.get("contract_value") or 0.001)
    tick = float(prod.get("tick_size") or 0.1)

    pos = broker.get_position(pid)
    actual_size = int(pos.get("size", 0)) if pos else 0
    actual_dir = 0 if actual_size == 0 else (1 if actual_size > 0 else -1)

    # ---- strategy wants FLAT ----
    if desired is None:
        if actual_dir != 0:
            print("   -> closing position (strategy flat)")
            broker.cancel_all(pid)
            broker.close_position(pid, pos)
        state["stop"] = None
        return

    want_dir = desired["dir"]
    new_stop = _round_to_tick(desired["stop"], tick)
    stop_side = "sell" if want_dir == 1 else "buy"

    # ---- already aligned: sync the trailing stop if it moved in our favour ----
    if actual_dir == want_dir:
        old = state.get("stop")
        moved = (old is None or
                 (want_dir == 1 and new_stop > old + tick / 2) or
                 (want_dir == -1 and new_stop < old - tick / 2))
        if moved:
            print(f"   -> trailing stop {old} -> {new_stop}")
            broker.cancel_all(pid)
            broker.stop_order(pid, abs(actual_size), stop_side, new_stop)
            state["stop"] = new_stop
        return

    # ---- wrong direction: flip (close opposite first) ----
    if actual_dir != 0:
        print("   -> flipping: closing opposite position first")
        broker.cancel_all(pid)
        broker.close_position(pid, pos)
        state["stop"] = None

    # ---- open the desired position + protective resting stop ----
    stop_dist = abs(desired["entry"] - desired["stop"])
    size = size_contracts(balance, risk_pct, stop_dist, cval, price, max_lev)
    print(f"   -> OPEN {'LONG' if want_dir == 1 else 'SHORT'}: bal={balance:.2f} "
          f"stop_dist={stop_dist:.1f} size={size} contracts")
    if size <= 0:
        print("   size computed as 0 — check balance / stop distance / contract_value")
        return
    broker.market_order(pid, size, "buy" if want_dir == 1 else "sell")
    broker.stop_order(pid, size, stop_side, new_stop)
    state["stop"] = new_stop
