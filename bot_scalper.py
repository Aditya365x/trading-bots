"""
LIVE bot running the 1-MINUTE SCALPER (scalper_1m.py) on Delta testnet.

⚠️ The scalper LOST in backtest even with zero fees (no edge) and is brutal with
real fees. This is a fake-money forward-test only. Do NOT run with real funds.

Same execution plumbing as bot.py, but: 1m candles, 1m loop, scalper signals.
Run only ONE bot per account — stop bot.py (baseline) before running this.

  & "d:\\stock Analyser\\.venv\\Scripts\\python.exe" bot_scalper.py --live --loop
"""
from __future__ import annotations

import argparse
import math
import os
import sys
import time
import datetime as dt

import pandas as pd
from dotenv import load_dotenv

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import scalper_1m as strat                       # noqa: E402  (the 1m scalper)

from delta_broker import DeltaBroker             # noqa: E402

TF = "1m"
TF_SECONDS = 60
SETTLE_BUFFER = 4


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


def candles_to_df(rows) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    for c in ("open", "high", "low", "close"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df[["open", "high", "low", "close"]].dropna().reset_index(drop=True)


def desired_from_strategy(df: pd.DataFrame, cfg):
    bt = strat.Backtester(df, cfg)
    bt.run()
    p = bt.position
    if p is None:
        return None
    return {"dir": int(p["dir"]), "entry": float(p["entry"]),
            "stop": float(p["stop"]), "target": float(p["target"])}


def size_contracts(balance, risk_pct, stop_dist, contract_value, price, max_lev) -> int:
    if stop_dist <= 0 or contract_value <= 0 or price <= 0:
        return 0
    risk_cash = balance * risk_pct
    risk_per_contract = stop_dist * contract_value
    n = risk_cash / risk_per_contract if risk_per_contract > 0 else 0
    max_n = (balance * max_lev) / (contract_value * price) if contract_value * price > 0 else 0
    return max(int(math.floor(min(n, max_n))), 0)


def run_cycle(broker, symbol, cfg, risk_pct, max_lev):
    prod = broker.get_product(symbol)
    pid = int(prod["id"])
    cval = float(prod.get("contract_value") or 0.001)

    rows = broker.get_candles(symbol, TF, 500)
    if len(rows) < 100:
        print(f"   not enough candles ({len(rows)})")
        return
    df = candles_to_df(rows)
    desired = desired_from_strategy(df, cfg)

    pos = broker.get_position(pid)
    actual = int(pos.get("size", 0)) if pos else 0
    adir = 0 if actual == 0 else (1 if actual > 0 else -1)

    price = df["close"].iloc[-1]
    now = dt.datetime.utcnow().strftime("%H:%M:%S")
    want = "FLAT" if not desired else ("LONG" if desired["dir"] == 1 else "SHORT")
    have = "FLAT" if adir == 0 else ("LONG" if adir == 1 else "SHORT")
    print(f"[{now}Z] {symbol} {TF} px={price:.1f} | scalper wants {want} | account {have} ({actual})")

    if desired is None:
        if adir != 0:
            print("   -> closing (scalper flat)")
            broker.cancel_all(pid)
            broker.close_position(pid, pos)
        return
    if adir == desired["dir"]:
        return
    if adir != 0 and adir != desired["dir"]:
        print("   -> flipping")
        broker.cancel_all(pid)
        broker.close_position(pid, pos)

    asset_id = int((prod.get("settling_asset") or {}).get("id", 3))
    bal = extract_usd_balance(broker.get_balance(asset_id))
    stop_dist = abs(desired["entry"] - desired["stop"])
    size = size_contracts(bal, risk_pct, stop_dist, cval, price, max_lev)
    print(f"   -> OPEN {want}: bal={bal:.2f} stop_dist={stop_dist:.1f} size={size}")
    if size <= 0:
        return
    side = "buy" if desired["dir"] == 1 else "sell"
    broker.market_order(pid, size, side)
    broker.stop_order(pid, size, "sell" if desired["dir"] == 1 else "buy", round(desired["stop"], 1))


def seconds_to_next_close():
    now = time.time()
    return (now // TF_SECONDS + 1) * TF_SECONDS - now + SETTLE_BUFFER


def main():
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--loop", action="store_true")
    args = ap.parse_args()

    base = os.getenv("DELTA_BASE_URL", "")
    key = os.getenv("DELTA_API_KEY", "")
    secret = os.getenv("DELTA_API_SECRET", "")
    symbol = os.getenv("DELTA_SYMBOL", "BTCUSD")
    risk_pct = float(os.getenv("RISK_PER_TRADE_PCT", "0.5")) / 100.0
    max_lev = float(os.getenv("MAX_LEVERAGE", "5"))

    if not base or not key or key == "your_testnet_api_key_here":
        raise SystemExit("Edit .env first.")

    dry = not args.live
    broker = DeltaBroker(base, key, secret, dry_run=dry)
    # LOOSENED so it actually fires in quiet chop (you wanted to see trades).
    # rsi 45/55 + cooldown 1 => triggers often. NOTE: still negative-edge, expect losses.
    cfg = strat.Config(risk_per_trade_pct=risk_pct, max_leverage=max_lev,
                       rsi_os=45, rsi_ob=55, cooldown_bars=1)

    mode = "DRY-RUN" if dry else "*** LIVE TESTNET ***"
    print(f"=== 1m SCALPER bot | {symbol} {TF} | {mode} ===")
    print("WARNING: scalper had NO backtest edge; expect testnet losses. Fake money only.\n")

    if not args.loop:
        run_cycle(broker, symbol, cfg, risk_pct, max_lev)
        return
    while True:
        try:
            run_cycle(broker, symbol, cfg, risk_pct, max_lev)
        except Exception as e:
            print(f"   [error] {type(e).__name__}: {e}")
        time.sleep(max(3, seconds_to_next_close()))


if __name__ == "__main__":
    main()
