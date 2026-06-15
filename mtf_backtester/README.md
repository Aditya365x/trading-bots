# MTF Backtester (ccxt + backtesting.py)

A modular, multi-timeframe crypto backtesting engine. Separate from `backtest_adi/`
(the custom ICT engine). Built to download data, run a multi-timeframe strategy,
report risk-adjusted performance, and sweep timeframes to find where the edge lives.

## Layout

| File | Role |
|------|------|
| `config.py` | All tunables: symbol, exchange, timeframes, HTF rule, fees, cash. |
| `data_loader.py` | ccxt downloader — paginated OHLCV + CSV cache in `data/`. |
| `indicators.py` | RSI / SMA / EMA / ATR + **MTF resample & no-lookahead HTF→LTF align**. |
| `strategy.py` | `backtesting.py` Strategy — HTF trend filter + LTF RSI trigger (replace with your Pine logic). |
| `performance.py` | Win Rate, Profit Factor, annualized Sharpe, Max Drawdown. |
| `run.py` | One timeframe end-to-end: load → features → backtest → report → optional plot. |
| `optimize_timeframe.py` | Run every timeframe, rank by Sharpe, save CSV. |

## Setup

Uses the project venv at `d:\stock Analyser\.venv` (never the C: drive / system Python):

```powershell
& "d:\stock Analyser\.venv\Scripts\python.exe" -m pip install -r requirements.txt
```

## Usage (run from this folder with the venv interpreter)

```powershell
# 1. download + cache data for every timeframe in config
& "d:\stock Analyser\.venv\Scripts\python.exe" data_loader.py

# 2. backtest a single timeframe (+ interactive HTML plot in results/)
& "d:\stock Analyser\.venv\Scripts\python.exe" run.py 1h --plot

# 3. sweep all timeframes and rank by risk-adjusted return
& "d:\stock Analyser\.venv\Scripts\python.exe" optimize_timeframe.py
```

Data is cached to `data/` on first download, so steps 2–3 are instant and offline.
Pass `refresh=True` to `load_ohlcv()` (or delete the CSV) to re-pull.

## How the multi-timeframe alignment avoids lookahead

This is the part most homemade backtesters get wrong. In `indicators.py`:

1. `resample_ohlcv` aggregates the LTF bars up to the HTF (e.g. 5m → 4h).
2. `align_htf_to_ltf` **shifts the HTF series by one bar** before forward-filling
   onto the LTF index. So every LTF bar only ever sees the *last fully-closed* HTF
   value — never the HTF bar still forming. Without that shift the backtest peeks
   into the future and prints fantasy returns.

## Plugging in your Pine Script strategy

Keep the structure of `strategy.py` and rewrite two methods:

- `init`: declare the indicator columns you need (precompute extra ones in
  `indicators.add_mtf_features` so they get the no-lookahead alignment for free).
- `next`: your entry/exit rules, called once per bar.

Sizing is risk-based (`risk_pct` of equity per trade) via `_size()`.

## Important caveats

- **Price scaling**: backtesting.py only trades whole units, so a $10k account
  can't buy 1 BTC (~$100k). `run.py` scales every price column by a constant
  (scaled price ≈ 1) so whole-unit rounding is negligible. Every reported metric
  is scale-invariant, so this changes nothing about the results.
- **The included strategy is a generic example, not an edge.** It currently loses
  on BTC — that's expected. The framework is the deliverable; the alpha is yours.
- A higher Sharpe on a finer timeframe is often just more trades crossing more
  fees/spread. Keep `COMMISSION` realistic and confirm any ranking **out of
  sample** before trusting it.
