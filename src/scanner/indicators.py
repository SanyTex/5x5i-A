# ===============================================================
# 📈 indicators.py – Feature Builder für 5x5i-A
# ===============================================================
# Liefert add_features(df) mit:
# - ema_7/25/50/99/200
# - rsi_6, rsi_14 (Wilder)
# - macd, macd_signal, macd_hist, macd_cross (7/25/9)
# - volume_ma, high_volume, volume_followthrough
# ===============================================================

from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def rsi_wilder(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.bfill()


def macd(close: pd.Series, fast: int = 7, slow: int = 25, signal: int = 9):
    fast_ema = ema(close, fast)
    slow_ema = ema(close, slow)
    macd_line = fast_ema - slow_ema
    signal_line = ema(macd_line, signal)
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    required = {"close", "volume"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"add_features: missing columns {sorted(missing)}")

    df = df.copy()

    # EMAs
    df["ema_7"] = ema(df["close"], 7)
    df["ema_25"] = ema(df["close"], 25)
    df["ema_50"] = ema(df["close"], 50)
    df["ema_99"] = ema(df["close"], 99)
    df["ema_200"] = ema(df["close"], 200)

    # RSI
    df["rsi_6"] = rsi_wilder(df["close"], 6)
    df["rsi_14"] = rsi_wilder(df["close"], 14)

    # MACD (7/25/9)
    m, s, h = macd(df["close"], 7, 25, 9)
    df["macd"] = m
    df["macd_signal"] = s
    df["macd_hist"] = h
    df["macd_cross"] = (df["macd"] > df["macd_signal"]).astype(int)

    # Volume Flags
    df["volume_ma"] = df["volume"].rolling(14, min_periods=1).mean()
    df["high_volume"] = df["volume"] > 1.05 * df["volume_ma"]
    df["volume_followthrough"] = df["volume"] >= df["volume"].shift(1)

    return df
