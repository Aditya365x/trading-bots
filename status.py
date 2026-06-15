"""
Account snapshot — read-only. Places NO orders. Run anytime to see where you stand.

  & "d:\\stock Analyser\\.venv\\Scripts\\python.exe" status.py
"""
import os
from dotenv import load_dotenv

from delta_broker import DeltaBroker

load_dotenv()

broker = DeltaBroker(
    os.getenv("DELTA_BASE_URL", ""),
    os.getenv("DELTA_API_KEY", ""),
    os.getenv("DELTA_API_SECRET", ""),
    dry_run=True,                      # read-only; no orders ever sent from here
)
symbol = os.getenv("DELTA_SYMBOL", "BTCUSD")

prod = broker.get_product(symbol)
pid = int(prod["id"])
aid = int((prod.get("settling_asset") or {}).get("id", 3))

print("=" * 56)
print(f" DELTA TESTNET SNAPSHOT — {symbol}")
print("=" * 56)

# --- price ---
candles = broker.get_candles(symbol, "5m", 2)
price = candles[-1]["close"] if candles else "n/a"
print(f" Last price        : {price}")

# --- balance ---
bal = broker.get_balance(aid) or {}
print(f" Balance (USD)     : {bal.get('balance')}")
print(f" Available (USD)   : {bal.get('available_balance')}")
print(f" Position margin   : {bal.get('position_margin')}")
print(f" Order margin      : {bal.get('order_margin')}")

# --- position ---
pos = broker.get_position(pid) or {}
size = int(pos.get("size", 0) or 0)
if size == 0:
    print(" Position          : FLAT")
else:
    side = "LONG" if size > 0 else "SHORT"
    print(f" Position          : {side} {abs(size)} contracts @ {pos.get('entry_price')}")
    upnl = pos.get("unrealized_pnl") or pos.get("unrealized_cashflow")
    if upnl is not None:
        print(f" Unrealized PnL    : {upnl}")
    print(f" Liquidation price : {pos.get('liquidation_price')}")

# --- open orders ---
try:
    orders = broker.client.get_live_orders() or []
    print(f" Open orders       : {len(orders)}")
    for o in orders:
        print(f"   - {o.get('side')} {o.get('size')} "
              f"{o.get('order_type')} stop={o.get('stop_price')} limit={o.get('limit_price')} "
              f"state={o.get('state')}")
except Exception as e:
    print(f" Open orders       : (could not fetch: {type(e).__name__})")

print("=" * 56)
