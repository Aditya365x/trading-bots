"""
A multi-timeframe strategy for backtesting.py.

This is a clean, mechanical EXAMPLE you can swap for your Pine logic:
  TREND FILTER (HTF) : only long while the higher timeframe is bullish
                       (HTF close above its HTF moving average), only short while
                       bearish.
  TRIGGER    (LTF)   : enter on an RSI reversal in the direction of the HTF trend
                       (RSI crossing up out of oversold for longs, down out of
                       overbought for shorts).
  EXIT               : ATR stop + fixed R-multiple target, or trend flip.

All HTF values are pre-aligned with no lookahead in indicators.add_mtf_features,
so inside the Strategy we just read self.data columns bar by bar.

To port your Pine Script: keep this file's structure and rewrite `init` (declare
your indicators) and `next` (your entry/exit rules).
"""
from __future__ import annotations
from backtesting import Strategy
from backtesting.lib import crossover

import config


class MTFTrendRSI(Strategy):
    # exposed as optimizable params for backtesting.py
    rsi_oversold = config.RSI_OVERSOLD
    rsi_overbought = config.RSI_OVERBOUGHT
    atr_mult = 2.0          # stop distance in ATRs
    reward = 2.0            # target = reward * stop distance
    risk_pct = 0.02         # fraction of equity risked per trade

    def init(self):
        # columns precomputed in indicators.add_mtf_features
        self.rsi = self.I(lambda: self.data.RSI, name="RSI", overlay=False)
        self.htf_close = self.data.HTF_Close
        self.htf_ma = self.data.HTF_MA
        self.atr = self.data.ATR

    def next(self):
        price = self.data.Close[-1]
        a = self.atr[-1]
        if a != a or a <= 0:        # NaN guard during warmup
            return

        htf_bull = self.htf_close[-1] > self.htf_ma[-1]
        htf_bear = self.htf_close[-1] < self.htf_ma[-1]

        # ---- manage / flip: exit if HTF trend flips against the position ----
        if self.position.is_long and htf_bear:
            self.position.close()
        elif self.position.is_short and htf_bull:
            self.position.close()

        if self.position:
            return                  # one position at a time; stops/TP set on entry

        # ---- entries: RSI reversal aligned with HTF trend ----
        long_trigger = crossover(self.rsi, self.rsi_oversold)
        short_trigger = crossover(self.rsi_overbought, self.rsi)

        if htf_bull and long_trigger:
            stop = price - self.atr_mult * a
            tp = price + self.reward * self.atr_mult * a
            self.buy(size=self._size(price, price - stop), sl=stop, tp=tp)
        elif htf_bear and short_trigger:
            stop = price + self.atr_mult * a
            tp = price - self.reward * self.atr_mult * a
            self.sell(size=self._size(price, stop - price), sl=stop, tp=tp)

    def _size(self, price, stop_dist):
        """Risk-based size as a FRACTION of equity (0<f<1), capped below 1 so we
        never use leverage. units = risk_cash / stop_dist; fraction = units*price/equity.
        FractionalBacktest lets these be non-whole, so small accounts can trade BTC."""
        if stop_dist <= 0:
            return 0.0
        frac = self.risk_pct * price / stop_dist
        return min(frac, 0.99)
