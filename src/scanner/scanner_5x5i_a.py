# ===============================================================
# 🚀 scanner_5x5i_a.py – 5x5i-A Proof Scanner (modular, real Decision Layer)
# ===============================================================
# - Holt Klines (1h/4h/1d)
# - Add Features (EMA/RSI/MACD/Vol flags)
# - Bewertet mit evaluate_5x5iA()
# - ✅ NEU: Loggt JEDE Bewertung (auch NONE) als JSONL (FULL + optional FOCUS + Daily roll)
# - Confirmed: 1h + 4h gleiche Richtung UND beide score_overall >= 4
# - Schreibt signals_confirmed.csv (append-only)
# ===============================================================

import time
from datetime import datetime

import pytz

from config.assets import ASSETS
from config.settings import SETTINGS
from src.common.log import info, warn
from src.common.csvio import read_csv_rows
from src.scanner.binance import fetch_klines

# ✅ Features (EMA/RSI/MACD/Vol flags)
from src.scanner.indicators import add_features

from src.scanner.decision_layer_5x5iA import evaluate_5x5iA
from src.scanner.fib import fib_0236_level
from src.scanner.signal_writer import make_signal_id, write_signal

# ✅ NEU: Vollstaendiges Eval-Logging (JSONL)
from src.scanner.scanner_eval_logger import append_eval_jsonl


TZ = pytz.timezone("Europe/Zurich")


def already_have_signal(signal_id: str, confirmed_path: str) -> bool:
    rows = read_csv_rows(confirmed_path)
    if not rows:
        return False
    return any(r.get("signal_id") == signal_id for r in rows[-500:])


def run_once() -> None:
    for symbol in ASSETS:
        df1h = fetch_klines(symbol, SETTINGS.TF_1H, SETTINGS.CANDLE_LIMIT)
        df4h = fetch_klines(symbol, SETTINGS.TF_4H, SETTINGS.CANDLE_LIMIT)
        df1d = fetch_klines(symbol, SETTINGS.TF_1D, SETTINGS.CANDLE_LIMIT)

        if df1h is None or df4h is None or df1d is None:
            warn(f"{symbol}: fehlende Daten")
            continue

        # Features berechnen
        df1h_f = add_features(df1h)
        df4h_f = add_features(df4h)
        df1d_f = add_features(df1d)

        # -----------------------------------------------------------
        # Echte 5x5i-A Decision Layer Evaluation
        # -----------------------------------------------------------
        s1 = evaluate_5x5iA(df1h_f, timeframe="1h")
        s4 = evaluate_5x5iA(df4h_f, timeframe="4h")
        sd = evaluate_5x5iA(df1d_f, timeframe="1d")

        # -----------------------------------------------------------
        # ✅ NEU: Jede Bewertung loggen (FULL) + gefiltert loggen (FOCUS) + Daily roll
        # (direkt nach den Evaluations, bevor Confirm-Rule greift)
        # -----------------------------------------------------------
        try:
            run_ts_utc = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

            append_eval_jsonl(
                base_dir=SETTINGS.SCANNER_EVAL_DIR,
                run_ts_utc=run_ts_utc,
                bot_tag=SETTINGS.BOT_TAG,
                symbol=symbol,
                timeframe="1h",
                decision=s1,
                last_row=df1h_f.iloc[-1],
                scan_run_id=None,
                roll_daily=SETTINGS.SCANNER_EVAL_ROLL_DAILY,
                full_prefix=SETTINGS.SCANNER_EVAL_FULL_PREFIX,
                focus_prefix=SETTINGS.SCANNER_EVAL_FOCUS_PREFIX,
                focus_min_score=SETTINGS.SCANNER_EVAL_FOCUS_MIN_SCORE,
                focus_only_signals=SETTINGS.SCANNER_EVAL_FOCUS_ONLY_SIGNALS,
                gzip_yesterday=SETTINGS.SCANNER_EVAL_GZIP_YESTERDAY,
            )

            append_eval_jsonl(
                base_dir=SETTINGS.SCANNER_EVAL_DIR,
                run_ts_utc=run_ts_utc,
                bot_tag=SETTINGS.BOT_TAG,
                symbol=symbol,
                timeframe="4h",
                decision=s4,
                last_row=df4h_f.iloc[-1],
                scan_run_id=None,
                roll_daily=SETTINGS.SCANNER_EVAL_ROLL_DAILY,
                full_prefix=SETTINGS.SCANNER_EVAL_FULL_PREFIX,
                focus_prefix=SETTINGS.SCANNER_EVAL_FOCUS_PREFIX,
                focus_min_score=SETTINGS.SCANNER_EVAL_FOCUS_MIN_SCORE,
                focus_only_signals=SETTINGS.SCANNER_EVAL_FOCUS_ONLY_SIGNALS,
                gzip_yesterday=SETTINGS.SCANNER_EVAL_GZIP_YESTERDAY,
            )

            append_eval_jsonl(
                base_dir=SETTINGS.SCANNER_EVAL_DIR,
                run_ts_utc=run_ts_utc,
                bot_tag=SETTINGS.BOT_TAG,
                symbol=symbol,
                timeframe="1d",
                decision=sd,
                last_row=df1d_f.iloc[-1],
                scan_run_id=None,
                roll_daily=SETTINGS.SCANNER_EVAL_ROLL_DAILY,
                full_prefix=SETTINGS.SCANNER_EVAL_FULL_PREFIX,
                focus_prefix=SETTINGS.SCANNER_EVAL_FOCUS_PREFIX,
                focus_min_score=SETTINGS.SCANNER_EVAL_FOCUS_MIN_SCORE,
                focus_only_signals=SETTINGS.SCANNER_EVAL_FOCUS_ONLY_SIGNALS,
                gzip_yesterday=SETTINGS.SCANNER_EVAL_GZIP_YESTERDAY,
            )

        except Exception as e:
            # Logging darf den Scanner nie killen
            warn(f"{symbol}: eval-log error: {e}")

        # -----------------------------------------------------------
        # Confirmed Rule (Proof): 1h + 4h gleiche Richtung UND beide >= 4
        # -----------------------------------------------------------
        sig1 = s1.get("signal", "NONE")
        sig4 = s4.get("signal", "NONE")

        score1 = int(s1.get("details", {}).get("score_overall", 0))
        score4 = int(s4.get("details", {}).get("score_overall", 0))
        scored = int(sd.get("details", {}).get("score_overall", 0))

        if sig1 in ("LONG", "SHORT") and sig4 == sig1 and score1 >= 4 and score4 >= 4:
            ts_signal = datetime.now(TZ).isoformat(timespec="seconds")

            last_1h = df1h_f.iloc[-1]
            last_4h = df4h_f.iloc[-1]

            ema25_4h = float(last_4h["ema_25"])
            fib0236 = float(fib_0236_level(df4h))  # raw df4h für Fib-Lookback
            entry_ref = float(last_1h["close"])

            signal_id = make_signal_id(ts_signal, symbol, sig1)
            if already_have_signal(signal_id, SETTINGS.SIGNALS_CONFIRMED):
                continue

            row = {
                "ts_signal": ts_signal,
                "symbol": symbol,
                "direction": sig1,
                "timeframe_trigger": "1h+4h",
                "score_1h": score1,
                "score_4h": score4,
                "score_1d": scored,
                "entry_ref_price": entry_ref,
                "ema25_4h": ema25_4h,
                "fib_0236": fib0236,
                "bot_tag": SETTINGS.BOT_TAG,
                "signal_id": signal_id,
                "reason_1h": s1.get("reason", ""),
                "reason_4h": s4.get("reason", ""),
                "macd_phase_1h": s1.get("details", {}).get("macd_phase", ""),
                "macd_phase_4h": s4.get("details", {}).get("macd_phase", ""),
            }

            write_signal(SETTINGS.SIGNALS_CONFIRMED, row)
            info(f"✅ CONFIRMED {symbol} {sig1} (1h={score1},4h={score4}) -> {signal_id}")


def main() -> None:
    info("🚀 Scanner 5x5i-A gestartet (Proof Mode)")
    while True:
        try:
            run_once()
        except Exception as e:
            warn(f"Scanner loop error: {e}")
        time.sleep(SETTINGS.SCAN_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
