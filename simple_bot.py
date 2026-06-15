"""
DEAD-SIMPLE mechanical bot (no strategy, no leverage). Delta TESTNET.

Rules:
  - every 2 minutes: open a LONG (market), sized so notional <= balance (NO leverage)
  - close as soon as the position is in PROFIT (net of fees)
  - close if the position loses more than $5
  - one position at a time

Live STATUS BAR updates in place; OPEN/CLOSE events stay as history lines.

  & "d:\\stock Analyser\\.venv\\Scripts\\python.exe" simple_bot.py
"""
from __future__ import annotations
import math
import os
import sys
import time
import datetime as dt
from dotenv import load_dotenv

from delta_broker import DeltaBroker

# ---- rules ----
ENTRY_INTERVAL = 120     # seconds between entries (2 min)
MAX_LOSS_USD = 5.0       # hard stop in $
LEVERAGE = 1.0           # 1.0 = no leverage (notional <= balance)
POLL_SECONDS = 5         # status refresh / check interval
SIDE = "buy"             # long


def bar(text: str):
    sys.stdout.write("\r" + text.ljust(118))
    sys.stdout.flush()


def event(text: str):
    sys.stdout.write("\r" + " " * 118 + "\r")   # clear the bar line
    print(text)


def latest_price(broker, symbol):
    rows = broker.get_candles(symbol, "1m", 2)
    return float(rows[-1]["close"])


def main():
    load_dotenv()
    broker = DeltaBroker(os.getenv("DELTA_BASE_URL", ""), os.getenv("DELTA_API_KEY", ""),
                         os.getenv("DELTA_API_SECRET", ""), dry_run=False)
    symbol = os.getenv("DELTA_SYMBOL", "BTCUSD")
    prod = broker.get_product(symbol)
    pid = int(prod["id"])
    cval = float(prod.get("contract_value") or 0.001)
    asset_id = int((prod.get("settling_asset") or {}).get("id", 3))

    print(f"=== SIMPLE bot | {symbol} | every {ENTRY_INTERVAL}s | profit=exit | -${MAX_LOSS_USD}=exit | NO leverage ===")
    print("  (Ctrl+C to stop)\n")

    last_entry = 0.0
    realized = 0.0
    trades = 0

    while True:
        try:
            price = latest_price(broker, symbol)
            pos = broker.get_position(pid)
            size = int(pos.get("size", 0)) if pos else 0
            now = dt.datetime.utcnow().strftime("%H:%M:%S")

            if size != 0:
                entry = float(pos.get("entry_price") or price)
                pnl = size * cval * (price - entry)
                fee_buf = abs(size) * cval * price * 0.001
                if pnl > fee_buf:
                    broker.close_position(pid, pos)
                    realized += pnl; trades += 1
                    event(f"[{now}Z] PROFIT  +${pnl:.3f} @ {price:.1f}  | session ${realized:+.2f} / {trades} trades")
                elif pnl <= -MAX_LOSS_USD:
                    broker.close_position(pid, pos)
                    realized += pnl; trades += 1
                    event(f"[{now}Z] STOP    ${pnl:.2f} @ {price:.1f}  | session ${realized:+.2f} / {trades} trades")
                else:
                    bar(f"[{now}Z] LONG {size} @ {entry:.1f} | px {price:.1f} | pnl ${pnl:+.3f} "
                        f"(need >${fee_buf:.2f}) | session ${realized:+.2f}/{trades}tr")
            else:
                wait = ENTRY_INTERVAL - (time.time() - last_entry)
                if wait <= 0:
                    bal = 0.0
                    b = broker.get_balance(asset_id)
                    if b:
                        bal = float(b.get("available_balance") or b.get("balance") or 0)
                    size_n = max(int(math.floor(bal * LEVERAGE / (price * cval))), 1)
                    broker.market_order(pid, size_n, SIDE)
                    last_entry = time.time()
                    event(f"[{now}Z] OPEN {SIDE} {size_n} @ ~{price:.1f}  | bal ${bal:.2f} (no leverage)")
                else:
                    bar(f"[{now}Z] FLAT | next entry in {int(wait)}s | px {price:.1f} | session ${realized:+.2f}/{trades}tr")
        except Exception as e:
            event(f"   [error] {type(e).__name__}: {e}")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
