"""
STEP 2 — the testnet trading bot.

Pipeline each closed 5m candle:
  Delta candles -> strategy_2 signal -> reconcile with the live Delta position
  -> place / close / update orders on Delta TESTNET.

The stop-loss is a REAL resting order on the exchange (not virtual), so a bot
crash or missed cycle still leaves you protected.

SAFETY:
  - DRY-RUN by default. It prints intended orders but sends nothing.
  - Add --live to actually place TESTNET orders (still fake money).
  - Run test_connection.py first.

  & "d:\\stock Analyser\\.venv\\Scripts\\python.exe" bot.py            # dry-run, one cycle
  & "d:\\stock Analyser\\.venv\\Scripts\\python.exe" bot.py --loop     # dry-run, live loop
  & "d:\\stock Analyser\\.venv\\Scripts\\python.exe" bot.py --live --loop   # real testnet orders
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

# reuse the SAME strategy logic we validated (strategy_2.py lives one dir up)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import strategy_2 as strat                       # noqa: E402

from delta_broker import DeltaBroker             # noqa: E402

TF_SECONDS = 300
SETTLE_BUFFER = 8


def extract_usd_balance(bal) -> float:
    """get_balances(asset_id) returns the single-asset wallet dict."""
    if bal is None:
        return 0.0
    try:
        if isinstance(bal, dict):
            return float(bal.get("available_balance") or bal.get("balance") or 0)
        if isinstance(bal, list):                  # fallback if a list is returned
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


def desired_from_strategy(df: pd.DataFrame, cfg) -> dict | None:
    """Run the validated strategy over the series; return the position it WANTS now."""
    bt = strat.Backtester(df, cfg)
    bt.run()
    p = bt.position
    if p is None:
        return None
    return {"dir": int(p["dir"]), "entry": float(p["entry"]),
            "stop": float(p["stop"]), "target": float(p["target"])}


def size_contracts(balance: float, risk_pct: float, stop_dist: float,
                   contract_value: float, price: float, max_lev: float) -> int:
    if stop_dist <= 0 or contract_value <= 0 or price <= 0:
        return 0
    risk_cash = balance * risk_pct
    # $ risk per contract = price move to stop * base-units-per-contract
    risk_per_contract = stop_dist * contract_value
    n = risk_cash / risk_per_contract if risk_per_contract > 0 else 0
    # leverage cap: notional = n * contract_value * price <= balance * max_lev
    max_n = (balance * max_lev) / (contract_value * price) if contract_value * price > 0 else 0
    return max(int(math.floor(min(n, max_n))), 0)


def run_cycle(broker: DeltaBroker, symbol: str, cfg, risk_pct: float, max_lev: float):
    prod = broker.get_product(symbol)
    pid = int(prod["id"])
    cval = float(prod.get("contract_value") or 0.001)

    rows = broker.get_candles(symbol, "5m", 500)
    if len(rows) < 100:
        print(f"   not enough candles ({len(rows)}) — skipping")
        return
    df = candles_to_df(rows)
    desired = desired_from_strategy(df, cfg)

    pos = broker.get_position(pid)
    actual_size = int(pos.get("size", 0)) if pos else 0      # signed contracts
    actual_dir = 0 if actual_size == 0 else (1 if actual_size > 0 else -1)

    price = df["close"].iloc[-1]
    now = dt.datetime.utcnow().strftime("%H:%M:%S")
    want = "FLAT" if not desired else ("LONG" if desired["dir"] == 1 else "SHORT")
    have = "FLAT" if actual_dir == 0 else ("LONG" if actual_dir == 1 else "SHORT")
    print(f"[{now}Z] {symbol} px={price:.1f} | strategy wants {want} | account is {have} ({actual_size})")

    # ----- reconcile -----
    if desired is None:
        if actual_dir != 0:
            print("   -> closing position (strategy flat)")
            broker.cancel_all(pid)
            broker.close_position(pid, pos)
        return

    if actual_dir == desired["dir"]:
        # already aligned — (v1) leave the resting stop as-is.
        # TODO: trailing = cancel/replace stop when desired['stop'] moves.
        return

    if actual_dir != 0 and actual_dir != desired["dir"]:
        print("   -> flipping: closing opposite position first")
        broker.cancel_all(pid)
        broker.close_position(pid, pos)
        pos, actual_dir = None, 0

    # open a new position in the desired direction
    asset_id = int((prod.get("settling_asset") or {}).get("id", 3))
    bal = extract_usd_balance(broker.get_balance(asset_id))
    stop_dist = abs(desired["entry"] - desired["stop"])
    size = size_contracts(bal, risk_pct, stop_dist, cval, price, max_lev)
    print(f"   -> OPEN {want}: balance={bal:.2f} stop_dist={stop_dist:.1f} size={size} contracts")
    if size <= 0:
        print("   size computed as 0 — check balance / stop distance / contract_value")
        return
    side = "buy" if desired["dir"] == 1 else "sell"
    broker.market_order(pid, size, side)
    # protective resting stop on the exchange (opposite side)
    stop_side = "sell" if desired["dir"] == 1 else "buy"
    broker.stop_order(pid, size, stop_side, round(desired["stop"], 1))


def seconds_to_next_close() -> float:
    now = time.time()
    return (now // TF_SECONDS + 1) * TF_SECONDS - now + SETTLE_BUFFER


def main():
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="actually place TESTNET orders (default: dry-run)")
    ap.add_argument("--loop", action="store_true", help="run continuously, aligned to 5m closes")
    args = ap.parse_args()

    base = os.getenv("DELTA_BASE_URL", "")
    key = os.getenv("DELTA_API_KEY", "")
    secret = os.getenv("DELTA_API_SECRET", "")
    symbol = os.getenv("DELTA_SYMBOL", "BTCUSD")
    risk_pct = float(os.getenv("RISK_PER_TRADE_PCT", "1.0")) / 100.0
    max_lev = float(os.getenv("MAX_LEVERAGE", "5"))

    if not base or not key or key == "your_testnet_api_key_here":
        raise SystemExit("Edit .env first (base URL + testnet API key/secret). Run test_connection.py.")

    dry = not args.live
    broker = DeltaBroker(base, key, secret, dry_run=dry)
    # settings mirror strategy_2_delta_main.pine (your source-of-truth strategy)
    cfg = strat.Config(
        min_confidence=0,        # raise once you've found the edge threshold on testnet
        hard_stop_pct=0.02,      # 2% hard max-loss cap, same as the Pine
        reward_multiple=2.0,
        risk_per_trade_pct=risk_pct,
        max_leverage=max_lev,
    )

    mode = "DRY-RUN (no orders sent)" if dry else "*** LIVE TESTNET ORDERS ***"
    print(f"=== Delta demo bot | {symbol} 5m | {mode} ===")
    if not dry:
        print("Sending real testnet orders. Ctrl+C to stop.\n")

    if not args.loop:
        run_cycle(broker, symbol, cfg, risk_pct, max_lev)
        return

    while True:
        try:
            run_cycle(broker, symbol, cfg, risk_pct, max_lev)
        except Exception as e:
            print(f"   [error] {type(e).__name__}: {e}")
        time.sleep(max(5, seconds_to_next_close()))


if __name__ == "__main__":
    main()
