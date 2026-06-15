"""
STEP 1 — run this FIRST. Read-only. Places NO orders.

Verifies:
  - .env loads your testnet credentials
  - base_url is reachable
  - the symbol resolves to a product (id + contract value)
  - candles come back
  - your API key/secret authenticate (balance fetch)

  & "d:\\stock Analyser\\.venv\\Scripts\\python.exe" test_connection.py
"""
import os
from dotenv import load_dotenv

from delta_broker import DeltaBroker

load_dotenv()

BASE = os.getenv("DELTA_BASE_URL", "")
KEY = os.getenv("DELTA_API_KEY", "")
SECRET = os.getenv("DELTA_API_SECRET", "")
SYMBOL = os.getenv("DELTA_SYMBOL", "BTCUSD")

print(f"Base URL : {BASE}")
print(f"Symbol   : {SYMBOL}")
print(f"Key set  : {'yes' if KEY and KEY != 'your_testnet_api_key_here' else 'NO — edit .env'}")
print("-" * 60)

if not BASE or not KEY or KEY == "your_testnet_api_key_here":
    raise SystemExit("Edit .env with your testnet base URL + API key/secret first.")

broker = DeltaBroker(BASE, KEY, SECRET, dry_run=True)

# 1) product
print("1) Resolving product...")
p = broker.get_product(SYMBOL)
print(f"   product_id      = {p.get('id')}")
print(f"   contract_value  = {p.get('contract_value')}  (base units per contract)")
print(f"   tick_size       = {p.get('tick_size')}")

# 2) candles
print("2) Fetching candles...")
c = broker.get_candles(SYMBOL, "5m", 50)
print(f"   got {len(c)} candles; last close = {c[-1]['close'] if c else 'none'}")

# 3) auth / balance
print("3) Authenticating (balance)...")
asset_id = int((p.get("settling_asset") or {}).get("id", 0))
print(f"   settling asset id = {asset_id}")
bal = broker.get_balance(asset_id)
if bal is None:
    print("   [FAIL] balance call failed - check API key/secret + permissions (must be TESTNET keys).")
else:
    print(f"   balance OK: {bal}")

print("-" * 60)
print("If all three succeeded, you're ready for bot.py (still dry-run by default).")
