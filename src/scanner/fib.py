import pandas as pd

def fib_0236_level(df_4h: pd.DataFrame) -> float:
    """
    Woche-1 stabile, deterministische Fib-Berechnung:
    - Lookback: letzte 60 4h Candles
    - Swing: low=min(low), high=max(high) im Lookback
    - Fib 0.236 vom Range
    """
    if df_4h is None or df_4h.empty:
        return float("nan")
    look = df_4h.tail(60)
    lo = float(look["low"].min())
    hi = float(look["high"].max())
    # 0.236 Retracement vom High aus gesehen (Up-Range)
    return hi - 0.236 * (hi - lo)
