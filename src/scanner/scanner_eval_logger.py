# ===============================================================
# 🧾 scanner_eval_logger.py – Daily-rolled JSONL Forensik Logger
# ===============================================================
# Features:
# - Option A: Daily roll: *_YYYY-MM-DD.jsonl
# - Option C: Split FULL + FOCUS
# - Option B: Focus Filter (min_score / only_signals)
# - Optional: gzip yesterday file automatically
# - Thread-safe via FileLock
# - Adds missing_labels also for NONE (best-side inference)
# ===============================================================

from __future__ import annotations

import os
import json
import gzip
from datetime import datetime, timedelta, date
from typing import Any, Dict, Optional, Tuple, List

from filelock import FileLock


# ----------------------------
# Helpers: safe JSON
# ----------------------------
def _safe_float(x, default=None):
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


def _safe_bool(x) -> Optional[bool]:
    if isinstance(x, bool):
        return x
    if x in (0, 1):
        return bool(x)
    return None


def _iso_utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _date_str_utc(ts_utc: Optional[str] = None) -> str:
    # ts_utc: "2026-01-07T13:47:08Z"
    if ts_utc:
        try:
            dt = datetime.fromisoformat(ts_utc.replace("Z", "+00:00"))
            return dt.date().isoformat()
        except Exception:
            pass
    return datetime.utcnow().date().isoformat()


def _gzip_file_if_exists(path: str) -> None:
    # path = "...jsonl" -> "...jsonl.gz"
    if not os.path.exists(path):
        return
    gz_path = path + ".gz"
    if os.path.exists(gz_path):
        return  # already compressed
    # Do not gzip empty files
    if os.path.getsize(path) == 0:
        return

    # compress
    with open(path, "rb") as f_in, gzip.open(gz_path, "wb") as f_out:
        f_out.writelines(f_in)

    # remove original after successful gzip
    try:
        os.remove(path)
    except Exception:
        pass


def _append_jsonl(path: str, obj: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lock_path = path + ".lock"
    line = json.dumps(obj, ensure_ascii=False)

    with FileLock(lock_path):
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")


# ----------------------------
# Criteria mapping for missing_labels
# ----------------------------
_LONG_LABELS = {
    "struktur_long": "EMA-Stack",
    "momentum_long": "Momentum",
    "macd_ok": "MACD Trend",
    "volumen_ok": "Volumen",
    "keine_ueberdehnung": "Überdehnung",
}

_SHORT_LABELS = {
    "struktur_short": "EMA-Stack",
    "momentum_short": "Momentum",
    # short score uses "not macd_ok" as criterion, so we label it as:
    "macd_ok": "MACD Trend (short kontra)",  # when macd_ok True, short criterion fails
    "volumen_ok": "Volumen",
    "keine_ueberdehnung": "Überdehnung",
}


def _infer_best_side(scores: Dict[str, Any]) -> str:
    sl = int(scores.get("score_long", 0) or 0)
    ss = int(scores.get("score_short", 0) or 0)
    if sl > ss:
        return "long"
    if ss > sl:
        return "short"
    return "none"


def _compute_missing_labels(details: Dict[str, Any], scores: Dict[str, Any]) -> Tuple[List[str], int]:
    """
    Returns (missing_labels, missing_count) using:
    - if signal is LONG -> long labels based on false keys
    - if signal is SHORT -> short labels
    - if signal is NONE -> infer best_side from score_long vs score_short and compute labels accordingly
    """
    signal = (scores.get("signal") or "").upper()
    best_side = _infer_best_side(scores)

    if signal == "LONG":
        mapping = _LONG_LABELS
        # missing where key False
        missing = [lab for k, lab in mapping.items() if details.get(k) is False]
        return missing, len(missing)

    if signal == "SHORT":
        mapping = _SHORT_LABELS
        missing = []
        # EMA-Stack / Momentum / Volumen / Überdehnung
        for k, lab in mapping.items():
            if k == "macd_ok":
                # short criterion expects NOT macd_ok => missing when macd_ok is True
                if details.get("macd_ok") is True:
                    missing.append(lab)
            else:
                if details.get(k) is False:
                    missing.append(lab)
        return missing, len(missing)

    # NONE case -> best side
    if best_side == "long":
        mapping = _LONG_LABELS
        missing = [lab for k, lab in mapping.items() if details.get(k) is False]
        return missing, len(missing)

    if best_side == "short":
        mapping = _SHORT_LABELS
        missing = []
        for k, lab in mapping.items():
            if k == "macd_ok":
                if details.get("macd_ok") is True:
                    missing.append(lab)
            else:
                if details.get(k) is False:
                    missing.append(lab)
        return missing, len(missing)

    return [], 0


# ----------------------------
# Snapshot extractor (keep it stable)
# ----------------------------
def _snapshot_from_last_row(last_row) -> Dict[str, Any]:
    # last_row may be pandas Series
    def g(key, default=None):
        try:
            if hasattr(last_row, "get"):
                return last_row.get(key, default)
            return getattr(last_row, key, default)
        except Exception:
            return default

    snap = {
        "close": _safe_float(g("close")),
        "volume": _safe_float(g("volume")),

        "rsi_6": _safe_float(g("rsi_6")),
        "rsi_14": _safe_float(g("rsi_14")),

        "ema_7": _safe_float(g("ema_7")),
        "ema_25": _safe_float(g("ema_25")),
        "ema_50": _safe_float(g("ema_50")),
        "ema_99": _safe_float(g("ema_99")),
        "ema_200": _safe_float(g("ema_200")),

        "macd": _safe_float(g("macd")),
        "macd_signal": _safe_float(g("macd_signal")),
        "macd_hist": _safe_float(g("macd_hist")),
        "macd_cross": int(g("macd_cross", 0) or 0),

        "volume_ma": _safe_float(g("volume_ma")),
        "high_volume": bool(g("high_volume", False)),
        "volume_followthrough": bool(g("volume_followthrough", False)),
    }
    return snap


# ----------------------------
# Public API
# ----------------------------
def append_eval_jsonl(
    base_dir: str,
    run_ts_utc: str,
    bot_tag: str,
    symbol: str,
    timeframe: str,
    decision: Dict[str, Any],
    last_row,
    scan_run_id: Optional[str],
    roll_daily: bool = True,
    full_prefix: str = "scanner_eval_full",
    focus_prefix: str = "scanner_eval_focus",
    focus_min_score: int = 3,
    focus_only_signals: bool = False,
    gzip_yesterday: bool = True,
) -> None:
    """
    Writes two logs:
      - FULL: everything
      - FOCUS: filtered (Option B) but still daily rolled (Option C)
    Daily roll is automatic (Option A).
    """

    ts_eval_utc = _iso_utc_now()

    details = (decision or {}).get("details", {}) or {}
    signal = (decision or {}).get("signal", "NONE")
    reason = (decision or {}).get("reason", "")

    score_long = int(details.get("score_long", 0) or 0)
    score_short = int(details.get("score_short", 0) or 0)
    score_overall = int(details.get("score_overall", max(score_long, score_short)) or 0)
    max_score = int(details.get("max_score", 5) or 5)

    system_mode = details.get("system_mode", "")
    macd_phase = details.get("macd_phase", "")
    phase_flag = details.get("phase_flag", "")

    scores_obj = {
        "score_long": score_long,
        "score_short": score_short,
        "score_overall": score_overall,
        "max_score": max_score,
        "system_mode": system_mode,
        "macd_phase": macd_phase,
        "phase_flag": phase_flag,
        "signal": str(signal),
    }

    # Criteria bools (only True/False)
    bool_true = [k for k, v in details.items() if v is True]
    bool_false = [k for k, v in details.items() if v is False]
    criteria_obj = {
        "criteria_passed": bool_true,
        "criteria_failed": bool_false,
        "criteria_bool_total": len(bool_true) + len(bool_false),
    }

    missing_labels, missing_count = _compute_missing_labels(details, scores_obj)
    missing_obj = {
        "missing_labels": missing_labels,
        "missing_count": missing_count,
    }

    payload = {
        "type": "scanner_eval",
        "ts_run_utc": run_ts_utc,
        "ts_eval_utc": ts_eval_utc,
        "scan_run_id": scan_run_id,
        "bot_tag": bot_tag,
        "symbol": symbol,
        "timeframe": timeframe,
        "signal": str(signal),
        "reason": str(reason),
        "scores": {
            "score_long": score_long,
            "score_short": score_short,
            "score_overall": score_overall,
            "max_score": max_score,
            "system_mode": system_mode,
            "macd_phase": macd_phase,
            "phase_flag": phase_flag,
        },
        "details": details,  # raw
        "criteria": criteria_obj,
        "missing": missing_obj,
        "snapshot": _snapshot_from_last_row(last_row),
    }

    # Build daily path
    day = _date_str_utc(run_ts_utc) if roll_daily else "all"
    full_path = os.path.join(base_dir, f"{full_prefix}_{day}.jsonl" if roll_daily else f"{full_prefix}.jsonl")
    focus_path = os.path.join(base_dir, f"{focus_prefix}_{day}.jsonl" if roll_daily else f"{focus_prefix}.jsonl")

    # Optional gzip yesterday when day rolls (best-effort)
    if gzip_yesterday and roll_daily:
        try:
            dt = datetime.fromisoformat(run_ts_utc.replace("Z", "+00:00"))
            yday = (dt.date() - timedelta(days=1)).isoformat()
            y_full = os.path.join(base_dir, f"{full_prefix}_{yday}.jsonl")
            y_focus = os.path.join(base_dir, f"{focus_prefix}_{yday}.jsonl")
            _gzip_file_if_exists(y_full)
            _gzip_file_if_exists(y_focus)
        except Exception:
            pass

    # Write FULL always
    _append_jsonl(full_path, payload)

    # Write FOCUS conditionally (Option B)
    if focus_only_signals:
        focus_ok = str(signal).upper() in ("LONG", "SHORT")
    else:
        focus_ok = (score_overall >= int(focus_min_score))

    if focus_ok:
        _append_jsonl(focus_path, payload)
