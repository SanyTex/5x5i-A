import time
from datetime import datetime
import pytz

from config.assets import ASSETS
from config.settings import SETTINGS
from src.common.log import info, warn
from src.common.timeutils import utc_now_iso
from src.common.csvio import read_csv_rows
from src.scanner.binance import fetch_klines
from src.scanner.decision_5x5i_a import add_features 
from src.scanner.decision_layer_5x5iA import evaluate_5x5iA
from src.scanner.fib import fib_0236_level
from src.scanner.signal_writer import make_signal_id, write_signal

TZ = pytz.timezone("Europe/Zurich")

def already_have_signal(signal_id: str, confirmed_path: str) -> bool:
    rows = read_csv_rows(confirmed_path)
    if not rows:
        return False
    return any(r.get("signal_id") == signal_id for r in rows[-500:])  # nur tail check fuer speed

def run_once():
    for symbol in ASSETS:
        df1h = fetch_klines(symbol, SETTINGS.TF_1H, SETTINGS.CANDLE_LIMIT)
        df4h = fetch_klines(symbol, SETTINGS.TF_4H, SETTINGS.CANDLE_LIMIT)
        df1d = fetch_klines(symbol, SETTINGS.TF_1D, SETTINGS.CANDLE_LIMIT)

        if df1h is None or df4h is None or df1d is None:
            warn(f"{symbol}: fehlende Daten")
            continue

        df1h_f = add_features(df1h)
        df4h_f = add_features(df4h)
        df1d_f = add_features(df1d)

        s1 = evaluate_5x5iA(df1h_f, timeframe="1h")
        s4 = evaluate_5x5iA(df4h_f, timeframe="4h")
        sd = evaluate_5x5iA(df1d_f, timeframe="1d")
        # confirmed rule fuer Proof: 1h + 4h gleiche Richtung und beide >=4
        if (s1["signal"] in ("LONG","SHORT")
            and s4["signal"] == s1["signal"]
            and s1["score_long"] >= 4 if s1["signal"] == "LONG" else s1["score_short"] >= 4
        ):
            pass  # wird unten sauber berechnet
        # sauberer check:
        sig1 = s1["signal"]
        sig4 = s4["signal"]
        score1 = max(s1["score_long"], s1["score_short"])
        score4 = max(s4["score_long"], s4["score_short"])

        if sig1 in ("LONG","SHORT") and sig4 == sig1 and score1 >= 4 and score4 >= 4:
            now_local = datetime.now(TZ).isoformat(timespec="seconds")
            ts_signal = now_local  # local timestamp (zurich)

            last_1h = df1h_f.iloc[-1]
            last_4h = df4h_f.iloc[-1]

            ema25_4h = float(last_4h["ema_25"])
            fib0236 = float(fib_0236_level(df4h))

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
                "score_1d": max(sd["score_long"], sd["score_short"]),
                "entry_ref_price": entry_ref,
                "ema25_4h": ema25_4h,
                "fib_0236": fib0236,
                "bot_tag": SETTINGS.BOT_TAG,
                "signal_id": signal_id,
            }
            write_signal(SETTINGS.SIGNALS_CONFIRMED, row)
            info(f"✅ CONFIRMED {symbol} {sig1} (1h={score1},4h={score4}) -> {signal_id}")

def main():
    info("🚀 Scanner 5x5i-A gestartet (Proof Mode)")
    while True:
        try:
            run_once()
        except Exception as e:
            warn(f"Scanner loop error: {e}")
        time.sleep(SETTINGS.SCAN_INTERVAL_SECONDS)

if __name__ == "__main__":
    main()
