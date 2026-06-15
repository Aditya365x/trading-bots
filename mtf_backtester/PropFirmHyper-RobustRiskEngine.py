import math
import datetime as dt
import pandas as pd
import numpy as np


class PropFirmRiskEngine:
    def __init__(self, initial_capital=10000.0, max_daily_loss_pct=0.035, risk_per_trade_pct=0.01):
        # Account configurations
        self.initial_capital = initial_capital
        self.max_daily_loss_pct = max_daily_loss_pct  # Safe 3.5% buffer
        self.risk_per_trade_pct = risk_per_trade_pct  # 1% risk per trade
        
        # Risk Tracking States
        self.current_day = None
        self.daily_start_equity = initial_capital
        self.circuit_breaker_tripped = False
        
        # Strategy Constants (Matching your TradingView parameters)
        self.EMA_LENGTH = 200
        self.ADX_THRESHOLD = 21
        self.HARD_STOP_PCT = 0.015       # 1.5% initial stop loss
        self.TRAIL_ACTIVATE_PCT = 0.015   # 1.5% profit to trigger trailing
        self.TRAIL_OFFSET_PCT = 0.005     # 0.5% trailing distance

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculates 200 EMA, ADX, and MACD on incoming data."""
        # 1. 200 EMA
        df['ema_200'] = df['close'].ewm(span=self.EMA_LENGTH, adjust=False).mean()
        
        # 2. ADX (Average Directional Index) Calculation
        df['tr'] = np.maximum(df['high'] - df['low'], 
                     np.maximum(abs(df['high'] - df['close'].shift(1)), 
                                abs(df['low'] - df['close'].shift(1))))
        df['plus_dm'] = np.where((df['high'] - df['high'].shift(1)) > (df['low'].shift(1) - df['low']), 
                                 np.maximum(df['high'] - df['high'].shift(1), 0), 0)
        df['minus_dm'] = np.where((df['low'].shift(1) - df['low']) > (df['high'] - df['high'].shift(1)), 
                                  np.maximum(df['low'].shift(1) - df['low'], 0), 0)
        
        tr_smoothed = df['tr'].ewm(alpha=1/14, adjust=False).mean()
        plus_di = 100 * (df['plus_dm'].ewm(alpha=1/14, adjust=False).mean() / tr_smoothed)
        minus_di = 100 * (df['minus_dm'].ewm(alpha=1/14, adjust=False).mean() / tr_smoothed)
        
        df['dx'] = 100 * (abs(plus_di - minus_di) / (plus_di + minus_di + 1e-8))
        df['adx'] = df['dx'].ewm(alpha=1/14, adjust=False).mean()
        
        # 3. MACD Calculation (12, 26, 9)
        exp1 = df['close'].ewm(span=12, adjust=False).mean()
        exp2 = df['close'].ewm(span=26, adjust=False).mean()
        df['macd'] = exp1 - exp2
        df['signal'] = df['macd'].ewm(span=9, adjust=False).mean()
        
        return df

    def check_daily_circuit_breaker(self, current_equity: float):
        """Monitors and locks execution if daily drawdown parameters are breached."""
        today_utc = dt.datetime.now(dt.timezone.utc).date()
        
        # Session reset logic
        if self.current_day != today_utc:
            self.current_day = today_utc
            self.daily_start_equity = current_equity
            self.circuit_breaker_tripped = False
            print(f"[RESET] New trading session. Daily baseline equity set to: ${self.daily_start_equity:.2f}")

        # Drawdown validation
        current_daily_loss = self.daily_start_equity - current_equity
        max_loss_allowed = self.daily_start_equity * self.max_daily_loss_pct
        
        if current_daily_loss >= max_loss_allowed:
            self.circuit_breaker_tripped = True
            print(f"[CIRCUIT BREAKER] CRITICAL: Daily loss limit breached! Locked out for the day.")
        
        return self.circuit_breaker_tripped

    def calculate_position_size(self, current_equity: float, current_price: float) -> int:
        """Dynamic contract sizing.

        Limits risk exposure to exactly 1% of total account value per trade.
        """
        stop_loss_distance_cash = current_price * self.HARD_STOP_PCT
        cash_to_risk = current_equity * self.risk_per_trade_pct
        
        # Contract calculation (assumes 1 raw contract unit. Tweak if using micro-lots)
        raw_size = cash_to_risk / stop_loss_distance_cash
        return max(int(math.floor(raw_size)), 0)

    def evaluate_signals(self, df: pd.DataFrame, current_equity: float, current_position: int) -> dict:
        """Processes logic matrix and checks current state to generate execution payloads."""
        # Step 1: Force system-wide check of account health
        if self.check_daily_circuit_breaker(current_equity):
            return {"action": "EMERGENCY_CLOSE_ALL", "reason": "Daily limit breached"}

        if len(df) < 2:
            return {"action": "HOLD", "reason": "Awaiting market history"}

        # Extract the finalized closed candle indices
        last_row = df.iloc[-1]
        prev_row = df.iloc[-2]
        
        current_price = last_row['close']
        
        # Matrix state filters
        is_trending = last_row['adx'] > self.ADX_THRESHOLD
        in_uptrend = current_price > last_row['ema_200']
        in_downtrend = current_price < last_row['ema_200']
        
        # MACD crossover mechanics
        macd_crossover = (prev_row['macd'] <= prev_row['signal']) and (last_row['macd'] > last_row['signal'])
        macd_crossunder = (prev_row['macd'] >= prev_row['signal']) and (last_row['macd'] < last_row['signal'])

        # Signal aggregation
        buy_signal = in_uptrend and is_trending and macd_crossover
        sell_signal = in_downtrend and is_trending and macd_crossunder

        # Execution Routing
        if current_position == 0:
            if buy_signal:
                size = self.calculate_position_size(current_equity, current_price)
                return {
                    "action": "OPEN_LONG", 
                    "size": size, 
                    "entry_price": current_price,
                    "stop_loss": current_price * (1 - self.HARD_STOP_PCT),
                    "trail_activation": current_price * (1 + self.TRAIL_ACTIVATE_PCT),
                    "trail_offset": current_price * self.TRAIL_OFFSET_PCT
                }
            elif sell_signal:
                size = self.calculate_position_size(current_equity, current_price)
                return {
                    "action": "OPEN_SHORT", 
                    "size": size, 
                    "entry_price": current_price,
                    "stop_loss": current_price * (1 + self.HARD_STOP_PCT),
                    "trail_activation": current_price * (1 - self.TRAIL_ACTIVATE_PCT),
                    "trail_offset": current_price * self.TRAIL_OFFSET_PCT
                }
        
        return {"action": "HOLD", "reason": "Conditions neutral or position already filled"}