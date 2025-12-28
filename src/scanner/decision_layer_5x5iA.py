# ===============================================================
# 🧩 decision_layer_5x5iA.py – 5×5i-A Bewertungslogik (modular)
# ===============================================================
# Basierend auf:
# - originaler evaluate_decision() aus data_engine_intelligence_5x5i_A_v5.py
# - modularem Aufbau von decision_layer_5x5i.py
#
# Enthält:
# 1) Strukturtrend (System A, voller EMA-Stack bis EMA200)
# 2) Momentum (RSI-Doppelregel mit dynamischer RSI6-Entwicklung – Option 1)
# 3) MACD-Trend + MACD-Beschleunigung
# 4) MACD-Phase (early_up / late_down)
# 5) Volumen
# 6) Überdehnung
# 7) Score-System (5 Kriterien)
# ===============================================================

import numpy as np

# System Mode = A (Struktur-Stack bis EMA200)
SYSTEM_MODE = "A"


def evaluate_5x5iA(df, timeframe: str = "1h"):
    """
    Haupt-Entscheidung für 5×5i-A.
    Nimmt einen fertigen DataFrame aus dem data_provider entgegen.
    """
    if df is None or len(df) < 6:
        return {"signal": "NONE", "reason": "Zu wenig Daten", "details": {}}

    last = df.iloc[-1]
    prev = df.iloc[-2]
    d = {}

    # Sicherstellen, dass benötigte Felder existieren
    # (falls Indikatoren im DataFrame noch gross geschrieben sind)
    for col_old, col_new in [
        ("EMA7", "ema_7"),
        ("EMA25", "ema_25"),
        ("EMA50", "ema_50"),
        ("EMA99", "ema_99"),
        ("EMA200", "ema_200"),
        ("RSI6", "rsi_6"),
        ("RSI14", "rsi_14"),
    ]:
        if col_old in df.columns and col_new not in df.columns:
            df[col_new] = df[col_old]
            last = df.iloc[-1]
            prev = df.iloc[-2]

    # Convenience-Variablen
    rsi14 = float(last["rsi_14"])
    rsi6 = float(last["rsi_6"])
    rsi6_prev = float(prev.get("rsi_6", rsi6))  # Fallback: gleich, falls nicht vorhanden

    ema7 = float(last["ema_7"])
    ema25 = float(last["ema_25"])
    ema50 = float(last["ema_50"])
    ema99 = float(last.get("ema_99", ema50))
    ema200 = float(last.get("ema_200", ema99))

    # ===============================================================
    # 1️⃣ STRUKTURTREND (System A = voller EMA-Stack)
    # ===============================================================
    long_cond = (
        50 < rsi14 < 75
        and ema7 > ema25 > ema50 > ema99 > ema200
    )
    short_cond = (
        30 < rsi14 < 50
        and ema7 < ema25 < ema50 < ema99 < ema200
    )

    d["struktur_long"] = long_cond
    d["struktur_short"] = short_cond

    # ===============================================================
    # 2️⃣ MOMENTUM (RSI 6/14 + MACD Cross + EMA 7/25, Option 1 Logik)
    # ===============================================================
    macd_cross = int(last["macd_cross"])

    # LONG-Momentum:
    # - MACD Cross nach oben
    # - Strukturfilter: RSI14 > 50, EMA7 > EMA25
    # - RSI6 im "gesunden" Bereich (30–85)
    # - UND: RSI6 steigt (rsi6 > rsi6_prev) ODER ist bereits deutlich > 40
    d["momentum_long"] = (
        (macd_cross == 1)
        and (50 < rsi14 < 75)
        and (30 < rsi6 < 85)
        and (ema7 > ema25)
        and (rsi6 > rsi6_prev or rsi6 > 40)
    )

    # SHORT-Momentum:
    # - MACD Cross nach unten
    # - Strukturfilter: RSI14 < 50, EMA7 < EMA25
    # - RSI6 im "gesunden" Bereich (15–70)
    # - UND: RSI6 faellt (rsi6 < rsi6_prev) ODER ist bereits deutlich < 60
    d["momentum_short"] = (
        (macd_cross == 0)
        and (30 < rsi14 < 50)
        and (15 < rsi6 < 70)
        and (ema7 < ema25)
        and (rsi6 < rsi6_prev or rsi6 < 60)
    )

    # ===============================================================
    # 3️⃣ MACD-TREND & MOMENTUM
    # ===============================================================
    d["macd"] = float(last["macd"])
    d["macd_signal"] = float(last["macd_signal"])
    d["macd_hist"] = float(last["macd_hist"])

    d["macd_ok"] = d["macd"] > d["macd_signal"]
    d["macd_momentum"] = d["macd_hist"] > float(prev["macd_hist"])

    # ===============================================================
    # 4️⃣ INTELLIGENCE LAYER – MACD-Phase
    # ===============================================================
    hist_seq = df["macd_hist"].tail(5)
    macd_phase = "neutral"

    if hist_seq.is_monotonic_increasing and d["macd_hist"] > 0:
        macd_phase = "early_up"
    elif hist_seq.is_monotonic_decreasing and d["macd_hist"] > 0:
        macd_phase = "late_up"
    elif hist_seq.is_monotonic_decreasing and d["macd_hist"] < 0:
        macd_phase = "late_down"
    elif hist_seq.is_monotonic_increasing and d["macd_hist"] < 0:
        macd_phase = "early_down"

    d["macd_phase"] = macd_phase

    # Momentum-Abschwaechung / Aktivierung durch Phase
    if macd_phase.startswith("late"):
        d["momentum_long"] = False
        d["momentum_short"] = False
        d["phase_flag"] = "⚠️ Momentum schwächt sich ab"
    elif macd_phase.startswith("early"):
        d["phase_flag"] = "⚡ Neues Momentum aktiv"
    else:
        d["phase_flag"] = ""

    # ===============================================================
    # 5️⃣ VOLUMEN
    # ===============================================================
    d["volumen_ok"] = bool(
        last.get("high_volume") or last.get("volume_followthrough")
    )

    # ===============================================================
    # 6️⃣ ÜBERDEHNUNG
    # ===============================================================
    d["keine_ueberdehnung"] = (
        (30 < rsi14 < 75)
        and (15 < rsi6 < 85)
    )

    # ===============================================================
    # 7️⃣ SCORE-BERECHNUNG (5 Kriterien)
    # ===============================================================
    score_long = sum([
        d["struktur_long"],
        d["momentum_long"],
        d["macd_ok"],
        d["volumen_ok"],
        d["keine_ueberdehnung"],
    ])

    score_short = sum([
        d["struktur_short"],
        d["momentum_short"],
        not d["macd_ok"],
        d["volumen_ok"],
        d["keine_ueberdehnung"],
    ])

    # Hoch-TF RSI6 Begrenzung
    if timeframe in ("4h", "1d") and rsi6 > 75:
        score_long = min(score_long, 3)
        score_short = min(score_short, 3)

    # ===============================================================
    # Logging / Zusatzinformationen
    # ===============================================================
    c1, c2 = float(prev["volume"]), float(last["volume"])
    d["vol_ratio_candle"] = round(c2 / c1, 2) if c1 > 0 else 0.0

    avg_vol = df["volume"].rolling(20).mean().iloc[-1]
    d["vol_ratio_sma20"] = round(c2 / avg_vol, 2) if avg_vol > 0 else 0.0

    # RSI-Werte auch ins Detail schreiben (hilfreich fuer Logger/Debug)
    d["rsi_14"] = rsi14
    d["rsi_6"] = rsi6

    # ===============================================================
    # Finale Entscheidung
    # ===============================================================
    if score_long >= 4:
        sig = "LONG"
        reason = f"{score_long}/5 Kriterien erfüllt"
    elif score_short >= 4:
        sig = "SHORT"
        reason = f"{score_short}/5 Kriterien erfüllt"
    else:
        sig = "NONE"
        reason = f"Nur {max(score_long, score_short)}/5 Kriterien erfüllt"

    d["score_long"] = score_long
    d["score_short"] = score_short
    d["score_overall"] = max(score_long, score_short)
    d["system_mode"] = SYSTEM_MODE
    d["max_score"] = 5

    return {"signal": sig, "reason": reason, "details": d}
