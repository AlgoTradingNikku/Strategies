import pandas as pd
import numpy as np

def convert_to_heikin_ashi(df: pd.DataFrame) -> pd.DataFrame:
    """
    Converts standard OHLC data to Heikin-Ashi candles.
    HA_Close = (Open + High + Low + Close) / 4
    HA_Open  = (Prev_HA_Open + Prev_HA_Close) / 2
    HA_High  = Max(High, HA_Open, HA_Close)
    HA_Low   = Min(Low, HA_Open, HA_Close)
    """
    if df.empty:
        return df
        
    ha_df = df.copy()
    
    # 1. HA_Close is simple average of OHLC
    ha_df['close'] = (df['open'] + df['high'] + df['low'] + df['close']) / 4
    
    # 2. HA_Open is recursive. We must iterate or use a specialized approach.
    ha_opens = [0.0] * len(df)
    ha_opens[0] = (df['open'].iloc[0] + df['close'].iloc[0]) / 2
    
    closes = ha_df['close'].values
    for i in range(1, len(df)):
        ha_opens[i] = (ha_opens[i-1] + closes[i-1]) / 2
        
    ha_df['open'] = ha_opens
    
    # 3. HA_High and HA_Low
    ha_df['high'] = ha_df[['high', 'open', 'close']].max(axis=1)
    ha_df['low'] = ha_df[['low', 'open', 'close']].min(axis=1)
    
    return ha_df

def calculate_ema(df: pd.DataFrame, period: int = 9, source: str = 'close') -> pd.DataFrame:
    """Calculates Exponential Moving Average."""
    df[f'ema_{period}'] = df[source].ewm(span=period, adjust=False).mean()
    return df

def calculate_rsi(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """Calculates Relative Strength Index."""
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()

    rs = gain / loss
    df[f'rsi_{period}'] = 100 - (100 / (1 + rs))
    
    # Wilder's Smoothing (More accurate than simple rolling mean)
    # Re-calculating using EWM for better accuracy if needed, 
    # but standard RSI often uses Wilder. 
    # For simplicity and speed for V1, standard rolling is often 'close enough' 
    # but let's do it right with EWM (Wilder's) approximation:
    # gain = delta.where(delta > 0, 0).ewm(alpha=1/period, adjust=False).mean()
    # loss = -delta.where(delta < 0, 0).ewm(alpha=1/period, adjust=False).mean()
    
    return df

def calculate_stochrsi(df: pd.DataFrame, period: int = 14, k: int = 3, d: int = 3) -> pd.DataFrame:
    """Calculates Stochastic RSI."""
    # Ensure RSI exists
    rsi_col = f'rsi_{period}'
    if rsi_col not in df.columns:
        df = calculate_rsi(df, period)
    
    rsi = df[rsi_col]
    min_rsi = rsi.rolling(window=period).min()
    max_rsi = rsi.rolling(window=period).max()
    
    stoch = ((rsi - min_rsi) / (max_rsi - min_rsi)) * 100
    
    df['stochrsi_k'] = stoch.rolling(window=k).mean()
    df['stochrsi_d'] = df['stochrsi_k'].rolling(window=d).mean()
    return df

def calculate_utbot(df: pd.DataFrame, key: float = 2.0, period: int = 10) -> pd.DataFrame:
    """
    Calculates UT Bot Trailing Stop (QuantNomad style).
    Logic: Uses ATR to calculate a trailing stop value.
    """
    # 1. Calculate ATR
    high_low = df['high'] - df['low']
    high_close = np.abs(df['high'] - df['close'].shift())
    low_close = np.abs(df['low'] - df['close'].shift())
    
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = np.max(ranges, axis=1)
    atr = true_range.ewm(alpha=1/period, adjust=False).mean()
    df['atr'] = atr
    
    # 2. Calculate Trailing Stop
    x_atr_trailing_stop = pd.Series(index=df.index, dtype='float64')
    
    # Initial calculation
    loss = key * atr
    
    # Vectorized loop is hard for trailing logic, using standard iteration for clarity in V1
    # Optimization: Numba can be used later if slow.
    
    traj = [0.0] * len(df)
    
    # Pre-compute values for speed
    closes = df['close'].values
    loss_val = loss.values
    
    # Variable to hold previous stop
    prev_stop = 0.0
    
    for i in range(1, len(df)):
        c = closes[i]          # src
        prev_c = closes[i-1]   # src[1]
        l = loss_val[i]        # nLoss
        
        # Pine Script Logic:
        # if (src > prev_stop and src[1] > prev_stop) -> max(prev_stop, src - nLoss)
        # else if (src < prev_stop and src[1] < prev_stop) -> min(prev_stop, src + nLoss)
        # else if (src > prev_stop) -> src - nLoss
        # else -> src + nLoss
        
        if c > prev_stop and prev_c > prev_stop:
            curr_stop = max(prev_stop, c - l)
        elif c < prev_stop and prev_c < prev_stop:
            curr_stop = min(prev_stop, c + l)
        elif c > prev_stop:
            curr_stop = c - l
        else:
            curr_stop = c + l
                
        # Update
        traj[i] = curr_stop
        prev_stop = curr_stop
        
    df['utbot_stop'] = traj
    
    # 3. Generate Signals
    # Buy: Price crosses ABOVE stop
    # Sell: Price crosses BELOW stop
    
    df['utbot_signal'] = 0
    df.loc[df['close'] > df['utbot_stop'], 'utbot_signal'] = 1  # Buy Zone
    df.loc[df['close'] < df['utbot_stop'], 'utbot_signal'] = -1 # Sell Zone
    
    return df

def calculate_supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0) -> pd.DataFrame:
    """
    Calculates SuperTrend indicator.
    Returns:
        - supertrend_signal: 1 for Buy, -1 for Sell
        - supertrend_line: The indicator line value
    """
    # Calculate ATR
    high_low = df['high'] - df['low']
    high_close = np.abs(df['high'] - df['close'].shift())
    low_close = np.abs(df['low'] - df['close'].shift())
    
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = np.max(ranges, axis=1)
    atr = true_range.ewm(alpha=1/period, adjust=False).mean()
    
    # Calculate Basic Upper and Lower Bands
    hl_avg = (df['high'] + df['low']) / 2
    basic_upper = hl_avg + (multiplier * atr)
    basic_lower = hl_avg - (multiplier * atr)
    
    # Calculate Final Bands with trailing logic
    final_upper = basic_upper.copy()
    final_lower = basic_lower.copy()
    
    for i in range(1, len(df)):
        # Upper Band: if basic_upper < final_upper[-1] or close[-1] > final_upper[-1], use basic_upper, else use final_upper[-1]
        if basic_upper.iloc[i] < final_upper.iloc[i-1] or df['close'].iloc[i-1] > final_upper.iloc[i-1]:
            final_upper.iloc[i] = basic_upper.iloc[i]
        else:
            final_upper.iloc[i] = final_upper.iloc[i-1]
            
        # Lower Band: if basic_lower > final_lower[-1] or close[-1] < final_lower[-1], use basic_lower, else use final_lower[-1]
        if basic_lower.iloc[i] > final_lower.iloc[i-1] or df['close'].iloc[i-1] < final_lower.iloc[i-1]:
            final_lower.iloc[i] = basic_lower.iloc[i]
        else:
            final_lower.iloc[i] = final_lower.iloc[i-1]
    
    # Determine SuperTrend Line and Signal
    supertrend = pd.Series(index=df.index, dtype='float64')
    signal = pd.Series(index=df.index, dtype='int64')
    
    supertrend.iloc[0] = final_upper.iloc[0]
    signal.iloc[0] = -1
    
    for i in range(1, len(df)):
        if df['close'].iloc[i] <= final_upper.iloc[i]:
            supertrend.iloc[i] = final_upper.iloc[i]
            signal.iloc[i] = -1  # Sell
        else:
            supertrend.iloc[i] = final_lower.iloc[i]
            signal.iloc[i] = 1   # Buy
            
    df['supertrend_line'] = supertrend
    df['supertrend_signal'] = signal
    
    return df

def detect_crossover(df: pd.DataFrame, col1: str, col2: str) -> bool:
    """
    Checks if col1 Just Crossed Over col2 in the last candle.
    Returns True if: Previous(A < B) AND Current(A > B)
    """
    if len(df) < 2:
        return False
        
    prev_1 = df[col1].iloc[-2]
    curr_1 = df[col1].iloc[-1]
    
    prev_2 = df[col2].iloc[-2]
    curr_2 = df[col2].iloc[-1]
    
    return (prev_1 <= prev_2) and (curr_1 > curr_2)

def detect_crossunder(df: pd.DataFrame, col1: str, col2: str) -> bool:
    """Checks if col1 Just Crossed Under col2."""
    if len(df) < 2:
        return False
        
    prev_1 = df[col1].iloc[-2]
    curr_1 = df[col1].iloc[-1]
    
    prev_2 = df[col2].iloc[-2]
    curr_2 = df[col2].iloc[-1]
    
    return (prev_1 >= prev_2) and (curr_1 < curr_2)
