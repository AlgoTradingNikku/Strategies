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
    atr = true_range.ewm(span=period, adjust=False).mean()
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
        c = closes[i]
        l = loss_val[i]
        
        # Determine strict stop
        if c > prev_stop:
            curr_stop = c - l
            if curr_stop < prev_stop:
                 curr_stop = prev_stop
        else:
            curr_stop = c + l
            if curr_stop > prev_stop:
                curr_stop = prev_stop
                
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
