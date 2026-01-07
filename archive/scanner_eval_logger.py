# ===============================================================
# 🧾 scanner_eval_logger.py – Vollstaendiges Scanner-Eval-Logging (JSONL)
# ===============================================================
# - loggt JEDE Bewertung (auch NONE)
# - pro Symbol + Timeframe (1h/4h/1d)
# - speichert Kriterien-Flags, Scores, Reason, Snapshot-Indikatoren
# - append-only + filelock
# ===============================================================

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, Optional

from filelock import FileLock


def _now_utc_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _safe_float(x, default=None):
    try:
        return float(x)
    except Exception:
        return default


def _safe_int(x, default=None):
    try:
        return int(x)
    except Exception:
        return default


def _series_get(last_row, key: str, default=None):
    # pandas Series: last_row.get(key) funktioniert meistens, aber sicher ist sicher
    try:
        if hasattr(last_row, "get"):
            v = last_row.get(key, default)
        else:
            v = getattr(last_row, key, default)
        return v
    except Exception:
        return default


def _extract_snapshot(last_row) -> Dict[str, Any]:
    """
    Minimaler, aber autopsie-tauglicher Snapshot.
    (Du kannst spaeter weitere Keys adden ohne Schema-Probleme.)
    """
    return {
        "close": _safe_float(_series_get(last_row, "close")),
        "volume": _safe_float(_series_get(last_row, "volume")),

        # RSI
        "rsi_6": _safe_float(_series_get(last_row, "rsi_6")),
        "rsi_14": _safe_float(_series_get(last_row, "rsi_14")),

        # EMAs
        "ema_7": _safe_float(_series_get(last_row, "ema_7")),
        "ema_25": _safe_float(_series_get(last_row, "ema_25")),
        "ema_50": _safe_float(_series_get(last_row, "ema_50")),
        "ema_99": _safe_float(_series_get(last_row, "ema_99")),
        "ema_200": _safe_float(_series_get(last_row, "ema_200")),

        # MACD
        "macd": _safe_float(_series_get(last_row, "macd")),
        "macd_signal": _safe_float(_series_get(last_row, "macd_signal")),
        "macd_hist": _safe_float(_series_get(last_row, "macd_hist")),
        "macd_cross": _safe_int(_series_get(last_row, "macd_cross")),

        # Volume flags
        "volume_ma": _safe_float(_series_get(last_row, "volume_ma")),
        "high_volume": bool(_series_get(last_row, "high_volume", False)),
        "volume_followthrough": bool(_series_get(last_row, "volume_followthrough", False)),
    }


def _criteria_lists(details: Dict[str, Any]) -> Dict[str, Any]:
    # Nur boolsche Kriterien als passed/failed
    bool_items = {k: v for k, v in details.items() if isinstance(v, bool)}
    passed = [k for k, v in bool_items.items() if v is True]
    failed = [k for k, v in bool_items.items() if v is False]
    return {
        "criteria_passed": passed,
        "criteria_failed": failed,
        "criteria_bool_total": len(passed) + len(failed),
    }


def _missing_labels(direction: str, details: Dict[str, Any]) -> Dict[str, Any]:
    """
    Mapping identisch zur Telegram-Logik, aber maschinenlesbar.
    """
    direction = (direction or "NONE").upper()
    if direction == "LONG":
        mapping = {
            "struktur_long": "EMA-Stack",
            "momentum_long": "Momentum",
            "macd_ok": "MACD Trend",
            "volumen_ok": "Volumen",
            "keine_ueberdehnung": "Ueberdehnung",
        }
    elif direction == "SHORT":
        mapping = {
            "struktur_short": "EMA-Stack",
            "momentum_short": "Momentum",
            # Bei SHORT ist im Score "not macd_ok" relevant,
            # trotzdem loggen wir macd_ok als Kriterium und labeln es logisch:
            "macd_ok": "MACD Trend (short kontra)",  # spaeter in Analyse sauber auswertbar
            "volumen_ok": "Volumen",
            "keine_ueberdehnung": "Ueberdehnung",
        }
    else:
        mapping = {}

    missing = []
    for key, label in mapping.items():
        # wenn key False -> fehlt
        try:
            if details.get(key) is False:
                missing.append(label)
        except Exception:
            pass

    return {
        "missing_labels": missing,
        "missing_count": len(missing),
    }


def append_eval_jsonl(
    path: str,
    *,
    run_ts_utc: str,
    bot_tag: str,
    symbol: str,
    timeframe: str,
    decision: Dict[str, Any],
    last_row,
    scan_run_id: Optional[str] = None,
) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lock_path = path + ".lock"

    sig = (decision or {}).get("signal", "NONE")
    reason = (decision or {}).get("reason", "")
    details = (decision or {}).get("details", {}) or {}

    scores = {
        "score_long": _safe_int(details.get("score_long"), 0),
        "score_short": _safe_int(details.get("score_short"), 0),
        "score_overall": _safe_int(details.get("score_overall"), 0),
        "max_score": _safe_int(details.get("max_score"), 5),
        "system_mode": details.get("system_mode", ""),
        "macd_phase": details.get("macd_phase", ""),
        "phase_flag": details.get("phase_flag", ""),
    }

    record = {
        "type": "scanner_eval",
        "ts_run_utc": run_ts_utc,           # Zeitpunkt des gesamten Scanner-Loops
        "ts_eval_utc": _now_utc_iso(),      # Zeitpunkt dieser konkreten Bewertung
        "scan_run_id": scan_run_id,         # optional (uuid), wenn du spaeter willst
        "bot_tag": bot_tag,
        "symbol": symbol,
        "timeframe": timeframe,

        "signal": sig,
        "reason": reason,

        "scores": scores,
        "details": details,                # RAW: wirklich alles aus Decision Layer

        "criteria": _criteria_lists(details),
        "missing": _missing_labels(sig, details),

        "snapshot": _extract_snapshot(last_row),
    }

    try:
        with FileLock(lock_path):
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"⚠️ Eval-Logger Fehler: {e}", flush=True)
