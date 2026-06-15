"""
ONE-OFF test trade — exercises the bot's REAL order path on Delta TESTNET so you
can watch a full round-trip (open -> stop -> close) on the Delta site.

Tiny size (default 1 contract = 0.001 BTC). Fake money. It OPENS then CLOSES,
leaving you flat. This is a manual demo, NOT the strategy.

  & "d:\\stock Analyser\\.venv\\Scripts\\python.exe" test_trade.py
"""
import os
import time
from dotenv import load_dotenv

from delta_broker import DeltaBroker

load_dotenv()

SIZE = 1          # contracts (0.001 BTC each) — keep tiny

broker = DeltaBroker(
    os.getenv("DELTA_BASE_URL", ""),
    os.getenv("DELTA_API_KEY", ""),
    os.getenv("DELTA_API_SECRET", ""),
    dry_run=False,                      # REAL testnet orders
)
symbol = os.getenv("DELTA_SYMBOL", "BTCUSD")
prod = broker.get_product(symbol)
pid = int(prod["id"])

price = broker.get_candles(symbol, "5m", 2)[-1]["close"]
print(f"Price ~ {price} | placing TEST market BUY {SIZE} contract(s) on {symbol} (testnet)...")

# 1) open a long
r = broker.market_order(pid, SIZE, "buy")
print("  entry response:", r)
time.sleep(2)

# 2) show the position
pos = broker.get_position(pid)
print("  position now :", pos)

# 3) place a protective stop ~1% below (just to show a resting stop on the exchange)
stop_px = round(price * 0.99, 1)
print(f"  placing protective STOP sell {SIZE} @ {stop_px} ...")
s = broker.stop_order(pid, SIZE, "sell", stop_px)
print("  stop response:", s)
time.sleep(1)
try:
    print("  open orders  :", broker.client.get_live_orders())
except Exception as e:
    print("  open orders  : (fetch failed)", e)

# 4) clean up: cancel the stop and close the position so you end FLAT
print("  cleaning up: cancelling orders + closing position...")
broker.cancel_all(pid)
time.sleep(1)
pos = broker.get_position(pid)
if pos and int(pos.get("size", 0)) != 0:
    broker.close_position(pid, pos)
time.sleep(2)

print("  final position:", broker.get_position(pid))
print("Done. Check Delta -> Positions / Order History / Fills to see the round-trip.")
