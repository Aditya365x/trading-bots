# Delta Exchange Testnet Bot (demo)

Runs the **strategy_2** logic and executes on your Delta **testnet** (demo) account
via API keys. Fake money. The stop-loss is a **real resting order on the exchange**.

## Architecture
```
Delta candles ─► strategy_2 signal ─► reconcile vs live position ─► orders on Delta testnet
```
- `delta_broker.py` — all Delta API calls (market data + auth + orders), dry-run guarded.
- `bot.py` — the loop: signal → reconcile → trade. Dry-run by default.
- `test_connection.py` — read-only checks. **Run this first.**

## Setup (one time)
```powershell
cd "d:\stock Analyser\backtest_adi\tradingApiDeltaDemo"

# 1) install deps INTO THE PROJECT VENV
& "d:\stock Analyser\.venv\Scripts\python.exe" -m pip install -r requirements.txt

# 2) create your secrets file from the template
cp config.example.env .env
#   then edit .env: paste your TESTNET api key + secret, confirm base URL + symbol
```
🔒 `.env` is gitignored. Never paste keys into chat or commit them.

## Run order (the ladder)
```powershell
# STEP 1 — verify connection (NO orders)
& "d:\stock Analyser\.venv\Scripts\python.exe" test_connection.py

# STEP 2 — dry-run one cycle (prints intended orders, sends nothing)
& "d:\stock Analyser\.venv\Scripts\python.exe" bot.py

# STEP 3 — dry-run live loop (watch it across many candles)
& "d:\stock Analyser\.venv\Scripts\python.exe" bot.py --loop

# STEP 4 — REAL testnet orders (fake money) once dry-run looks right
& "d:\stock Analyser\.venv\Scripts\python.exe" bot.py --live --loop
```

## Things to VERIFY against your testnet account / Delta docs
These are marked in the code; testnet responses will tell us the right values:
- `DELTA_BASE_URL` host for India testnet
- exact `DELTA_SYMBOL` and its `product_id` / `contract_value`
- `place_order` / stop-order parameter names in `delta-rest-client`
- shape of the balances + position responses (`extract_usd_balance`, `actual_size`)

## Not done yet (intentionally, for v1)
- **Stop trailing / breakeven** live (currently the resting stop is set once at entry).
  TODO is marked in `bot.py`. We'll add cancel/replace once basic entry+stop works.
- Reconnect/idempotency hardening for 24/7 running.

## Honest reminders
- Testnet fills ≠ live fills. Even when this works, paper/testnet overstates reality.
- The strategy was ~breakeven in backtest. Use testnet to find a confidence filter /
  settings with a real edge **before** thinking about real money.
