"""
PropFirm bot  ->  Delta TESTNET, ETHUSD, 2h.

Pipeline each closed 2h candle:
  Delta candles -> propfirm_live.desired_position (validated no-lookahead logic:
  EMA200 + ADX>21 + MACD cross, 1.5% hard stop, 1.5%/0.5% trailing) -> reconcile
  with the live Delta position -> place / close / trail orders on Delta.

SAFETY: DRY-RUN by default (prints intended orders, sends nothing).
  python bot_propfirm.py                 # dry-run, one cycle
  python bot_propfirm.py --loop          # dry-run, aligned to 2h closes
  python bot_propfirm.py --live --loop   # real TESTNET orders (fake money)

Run test_connection.py first. Loads .env.propfirm (its own testnet API key).
"""
from __future__ import annotations
import argparse
import datetime as dt
import os
import sys
import time

from dotenv import load_dotenv

# adapter lives in mtf_backtester (two dirs up, then over)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "mtf_backtester")))
import propfirm_live                                  # noqa: E402

from delta_broker import DeltaBroker                  # noqa: E402
import live_core as lc                                # noqa: E402

RESOLUTION = "2h"
TF_SECONDS = 7200


def run_cycle(broker, symbol, risk_pct, max_lev, state):
    prod = broker.get_product(symbol)
    pid = int(prod["id"])
    rows = broker.get_candles(symbol, RESOLUTION, 500)
    if len(rows) < 250:                               # EMA200 needs history
        print(f"   not enough candles ({len(rows)}) — skipping")
        return
    df = lc.candles_to_df(rows)
    desired = propfirm_live.desired_position(df)

    pos = broker.get_position(pid)
    actual = int(pos.get("size", 0)) if pos else 0
    price = float(df["close"].iloc[-1])
    now = dt.datetime.utcnow().strftime("%H:%M:%S")
    want = "FLAT" if not desired else ("LONG" if desired["dir"] == 1 else "SHORT")
    have = "FLAT" if actual == 0 else ("LONG" if actual > 0 else "SHORT")
    print(f"[{now}Z] PROPFIRM {symbol} {RESOLUTION} px={price:.2f} | wants {want} | account {have} ({actual})")

    asset_id = int((prod.get("settling_asset") or {}).get("id", 3))
    bal = lc.extract_usd_balance(broker.get_balance(asset_id))
    lc.reconcile(broker, prod, desired, bal, risk_pct, max_lev, price, state)


def main():
    here = os.path.dirname(__file__)
    load_dotenv(os.path.join(here, ".env.propfirm"))  # bot-specific key
    load_dotenv(os.path.join(here, ".env"))           # fallback / shared

    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="place real TESTNET orders (default: dry-run)")
    ap.add_argument("--loop", action="store_true", help="run continuously, aligned to 2h closes")
    args = ap.parse_args()

    base = os.getenv("DELTA_BASE_URL", "")
    key = os.getenv("DELTA_API_KEY", "")
    secret = os.getenv("DELTA_API_SECRET", "")
    symbol = os.getenv("DELTA_SYMBOL", "ETHUSD")
    risk_pct = float(os.getenv("RISK_PER_TRADE_PCT", "1.0")) / 100.0
    max_lev = float(os.getenv("MAX_LEVERAGE", "5"))
    if not base or not key or key == "your_testnet_api_key_here":
        raise SystemExit("Edit .env.propfirm first (testnet base URL + API key/secret). Run test_connection.py.")

    dry = not args.live
    broker = DeltaBroker(base, key, secret, dry_run=dry)
    state = {"stop": None}

    mode = "DRY-RUN (no orders sent)" if dry else "*** LIVE TESTNET ORDERS ***"
    print(f"=== PropFirm bot | {symbol} {RESOLUTION} | {mode} ===")

    if not args.loop:
        run_cycle(broker, symbol, risk_pct, max_lev, state)
        return
    while True:
        try:
            run_cycle(broker, symbol, risk_pct, max_lev, state)
        except Exception as e:
            print(f"   [error] {type(e).__name__}: {e}")
        time.sleep(max(5, lc.seconds_to_next_close(TF_SECONDS)))


if __name__ == "__main__":
    main()
