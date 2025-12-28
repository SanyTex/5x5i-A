import numpy as np
import pandas as pd
from src.scanner.indicators import ema, rsi_wilder, macd

def add_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ema_7"] = ema(df["close"], 7)
    df["ema_25"] = ema(df["close"], 25)
    df["ema_50"] = ema(df["close"], 50)
    df["ema_99"] = ema(df["close"], 99)
    df["ema_200"] = ema(df["close"], 200)

    df["rsi_6"] = rsi_wilder(df["close"], 6)
    df["rsi_14"] = rsi_wilder(df["close"], 14)

    m, s, h = macd(df["close"], 7, 25, 9)
    df["macd"] = m
    df["macd_signal"] = s
    df["macd_hist"] = h
    df["macd_cross"] = (df["macd"] > df["macd_signal"]).astype(int)

    df["volume_ma"] = df["volume"].rolling(14, min_periods=1).mean()
    df["high_volume"] = df["volume"] > 1.05 * df["volume_ma"]
    df["volume_followthrough"] = df["volume"] >= df["volume"].shift(1)

    return df

def score_5x5i_A(df: pd.DataFrame) -> dict:
    if df is None or len(df) < 50:
        return {"signal":"NONE","score_long":0,"score_short":0,"details":{}}

    last = df.iloc[-1]
    prev = df.iloc[-2]
    d = {}

    # Strukturtrend (A = voller Stack)
    d["struktur_long"] = (50 < last["rsi_14"] < 75) and (
        last["ema_7"] > last["ema_25"] > last["ema_50"] > last["ema_99"] > last["ema_200"]
    )
    d["struktur_short"] = (30 < last["rsi_14"] < 50) and (
        last["ema_7"] < last["ema_25"] < last["ema_50"] < last["ema_99"] < last["ema_200"]
    )

    # Momentum (RSI Doppelregel + EMA7/25)
    d["momentum_long"] = (last["macd_cross"] == 1) and (50 < last["rsi_14"] < 75) and (30 < last["rsi_6"] < 85) and (last["ema_7"] > last["ema_25"])
    d["momentum_short"] = (last["macd_cross"] == 0) and (30 < last["rsi_14"] < 50) and (15 < last["rsi_6"] < 70) and (last["ema_7"] < last["ema_25"])

    # MACD Trend + Momentum
    d["macd_ok"] = last["macd"] > last["macd_signal"]
    d["macd_momentum"] = last["macd_hist"] > prev["macd_hist"]

    # Volumen
    d["volumen_ok"] = bool(last["high_volume"] or last["volume_followthrough"])

    # Ueberdehnung
    d["keine_ueberdehnung"] = (30 < last["rsi_14"] < 75) and (15 < last["rsi_6"] < 85)

    score_long = sum([d["struktur_long"], d["momentum_long"], d["macd_ok"], d["volumen_ok"], d["keine_ueberdehnung"]])
    score_short = sum([d["struktur_short"], d["momentum_short"], (not d["macd_ok"]), d["volumen_ok"], d["keine_ueberdehnung"]])

    sig = "NONE"
    if score_long >= 4:
        sig = "LONG"
    elif score_short >= 4:
        sig = "SHORT"

    d["score_long"] = int(score_long)
    d["score_short"] = int(score_short)
    return {"signal": sig, "score_long": int(score_long), "score_short": int(score_short), "details": d}
