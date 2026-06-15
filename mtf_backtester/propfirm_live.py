"""
Live-signal adapter for the PropFirm engine.

Returns the position the strategy WANTS to hold right now, derived from the SAME
no-lookahead logic used in propfirm_backtest.py (signal on the last closed bar,
1.5% hard stop, 1.5%-activation / 0.5%-offset trailing) -- NOT the raw engine's
evaluate_signals (which uses datetime.now() and int(floor()) sizing).

    from propfirm_live import desired_position
    d = desired_position(df)         # df has lowercase open/high/low/close
    # d is None (flat) or {"dir": 1/-1, "entry": float, "stop": float}
"""
from __future__ import annotations
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

# load the engine from the hyphenated filename
_spec = importlib.util.spec_from_file_location(
    "propfirm_engine", str(Path(__file__).with_name("PropFirmHyper-RobustRiskEngine.py")))
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
PropFirmRiskEngine = _mod.PropFirmRiskEngine


def desired_position(df: pd.DataFrame) -> dict | None:
    """Walk the candle window bar-by-bar (no lookahead) and return the open
    position as of the last CLOSED candle, with the trailing stop already applied.
    Returns None if the strategy would currently be flat."""
    eng = PropFirmRiskEngine()
    d = eng.calculate_indicators(df.copy())

    o, h, l, c = (d[x].values for x in ("open", "high", "low", "close"))
    ema, adx, macd, sig = d["ema_200"].values, d["adx"].values, d["macd"].values, d["signal"].values
    n = len(d)
    HS, TA, TO, ADXT = eng.HARD_STOP_PCT, eng.TRAIL_ACTIVATE_PCT, eng.TRAIL_OFFSET_PCT, eng.ADX_THRESHOLD

    pos = None
    for t in range(n):
        # entry at this bar's open, from the signal on bar t-1 (closed)
        if pos is None and t >= 2 and not (np.isnan(ema[t-1]) or np.isnan(adx[t-1]) or np.isnan(macd[t-1])):
            trending = adx[t-1] > ADXT
            up, down = c[t-1] > ema[t-1], c[t-1] < ema[t-1]
            x_up = macd[t-2] <= sig[t-2] and macd[t-1] > sig[t-1]
            x_dn = macd[t-2] >= sig[t-2] and macd[t-1] < sig[t-1]
            direction = 1 if (up and trending and x_up) else (-1 if (down and trending and x_dn) else 0)
            if direction != 0:
                entry = o[t]
                pos = {"dir": direction, "entry": entry, "activated": False,
                       "stop": entry * (1 - direction * HS),
                       "trail_act": entry * (1 + direction * TA),
                       "trail_off": entry * TO}
        # manage existing position intrabar
        if pos is not None:
            dd = pos["dir"]
            if dd == 1:
                if l[t] <= pos["stop"]:
                    pos = None
                else:
                    if not pos["activated"] and h[t] >= pos["trail_act"]:
                        pos["activated"] = True
                    if pos["activated"]:
                        pos["stop"] = max(pos["stop"], h[t] - pos["trail_off"])
            else:
                if h[t] >= pos["stop"]:
                    pos = None
                else:
                    if not pos["activated"] and l[t] <= pos["trail_act"]:
                        pos["activated"] = True
                    if pos["activated"]:
                        pos["stop"] = min(pos["stop"], l[t] + pos["trail_off"])

    if pos is None:
        return None
    return {"dir": int(pos["dir"]), "entry": float(pos["entry"]), "stop": float(pos["stop"])}
