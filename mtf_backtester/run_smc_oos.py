"""
Out-of-sample validation for the SMC strategy.

Splits each timeframe's data chronologically into TRAIN (in-sample) and TEST
(out-of-sample), runs the SMC engine independently on each, and prints the two
side by side. The point: an edge that only shows up in-sample is curve-fit. We
want PF / win-rate / expectancy to SURVIVE on the held-out tail.

Nothing is tuned here — the same frozen Config runs on both halves. That is the
honest test.

    python run_smc_oos.py                 # 1h and 4h, 70/30 split
    python run_smc_oos.py 1h 4h --split 0.7
"""
from __future__ import annotations
import sys
import pandas as pd

import config
import data_loader
from smc_strategy_adi import Config, Backtester
from run_smc import _to_lower_ohlc, _BARS_PER_DAY

_METRICS = ["trades", "win_rate", "profit_factor", "expectancy_R",
            "max_drawdown_pct", "total_return_pct"]


def _run(df: pd.DataFrame, timeframe: str) -> dict:
    cfg = Config(bars_per_day=_BARS_PER_DAY.get(timeframe, 6))
    rep = Backtester(df.reset_index(drop=True), cfg).run()
    # fill missing keys (e.g. a no-trade segment) with 0 so the table is uniform
    return {m: rep.get(m, 0) for m in _METRICS}


def evaluate(timeframe: str, split: float):
    df = _to_lower_ohlc(
        data_loader.load_ohlcv(config.SYMBOL, timeframe, config.HISTORY_DAYS))
    cut = int(len(df) * split)
    train, test = df.iloc[:cut], df.iloc[cut:]

    is_res = _run(train, timeframe)
    oos_res = _run(test, timeframe)

    print(f"\n================  {timeframe}  ================")
    print(f"  train: {len(train)} bars | test: {len(test)} bars "
          f"({split:.0%}/{1-split:.0%} split)")
    print(f"  {'metric':>18} | {'IN-SAMPLE':>12} | {'OUT-OF-SAMPLE':>14}")
    print("  " + "-" * 50)
    for m in _METRICS:
        print(f"  {m:>18} | {str(is_res[m]):>12} | {str(oos_res[m]):>14}")

    verdict = _verdict(is_res, oos_res)
    print(f"  -> {verdict}")
    return {"timeframe": timeframe, **{f"is_{k}": v for k, v in is_res.items()},
            **{f"oos_{k}": v for k, v in oos_res.items()}, "verdict": verdict}


def _verdict(is_res: dict, oos_res: dict) -> str:
    """Cheap heuristic: does the edge survive out-of-sample?"""
    if oos_res["trades"] < 10:
        return "INCONCLUSIVE - too few out-of-sample trades to judge."
    pf = oos_res["profit_factor"]
    if pf == float("inf"):
        pf = 99
    if pf >= 1.3 and oos_res["total_return_pct"] > 0:
        return "HOLDS - edge persists out-of-sample (still validate with paper trading)."
    if pf >= 1.0:
        return "WEAK - marginally positive OOS; not convincing."
    return "FAILS - edge does NOT survive out-of-sample (likely curve-fit)."


def main(timeframes: list[str], split: float):
    rows = [evaluate(tf, split) for tf in timeframes]
    out = config.RESULTS_DIR / "smc_oos_comparison.csv"
    pd.DataFrame(rows).to_csv(out, index=False)
    print(f"\nsaved -> {out}")


if __name__ == "__main__":
    args = sys.argv[1:]
    split = 0.7
    if "--split" in args:
        i = args.index("--split")
        split = float(args[i + 1])
        args = args[:i] + args[i + 2:]
    tfs = args or ["1h", "4h"]
    main(tfs, split)
