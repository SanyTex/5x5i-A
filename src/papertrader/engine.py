import os
import time
from typing import Dict, Any

from config.settings import SETTINGS
from src.common.csvio import append_row_csv, read_csv_rows
from src.common.timeutils import utc_now_iso
from src.common.log import info, warn
from src.papertrader.state_store import load_json, save_json
from src.papertrader.price_feed import get_price
from src.papertrader.risk import calc_position_notional, apply_slippage


# -----------------------------
# Equity logging throttle (NEW)
# -----------------------------
# Log equity at most every N seconds, unless balance changed.
EQUITY_LOG_MIN_INTERVAL_SEC = getattr(SETTINGS, "EQUITY_LOG_MIN_INTERVAL_SEC", 60)
_last_equity_log_ts = 0.0
_last_equity_logged_balance = None


def _pt_paths(pt_dir: str) -> Dict[str, str]:
    return {
        "cursor": os.path.join(pt_dir, "cursor.json"),
        "positions": os.path.join(pt_dir, "positions.json"),
        "trades": os.path.join(pt_dir, "trades.csv"),
        "equity": os.path.join(pt_dir, "equity.csv"),
        "events": os.path.join(pt_dir, "events.csv"),
        "equity_state": os.path.join(pt_dir, "equity_state.json"),
    }


def _load_state(pt_dir: str):
    p = _pt_paths(pt_dir)
    cursor = load_json(p["cursor"], {"last_index": -1})
    positions = load_json(p["positions"], {"open": {}})
    equity = load_json(p["equity_state"], {"balance": SETTINGS.START_BALANCE_USDT})
    return cursor, positions, equity


def _save_state(pt_dir: str, cursor: dict, positions: dict, equity: dict):
    p = _pt_paths(pt_dir)
    save_json(p["cursor"], cursor)
    save_json(p["positions"], positions)
    save_json(p["equity_state"], equity)


def _log_event(pt_dir: str, pt_tag: str, event: dict):
    p = _pt_paths(pt_dir)
    event2 = {"ts": utc_now_iso(), "pt_tag": pt_tag, **event}
    append_row_csv(p["events"], event2)


def _log_trade(pt_dir: str, pt_tag: str, trade: dict):
    p = _pt_paths(pt_dir)
    row = {"ts_close": utc_now_iso(), "pt_tag": pt_tag, **trade}
    append_row_csv(p["trades"], row)


def _log_equity_raw(pt_dir: str, pt_tag: str, balance: float):
    p = _pt_paths(pt_dir)
    append_row_csv(p["equity"], {"ts": utc_now_iso(), "pt_tag": pt_tag, "balance": balance})


def _log_equity_throttled(pt_dir: str, pt_tag: str, balance: float):
    """
    NEW: Equity logging throttling:
    - log if min interval passed OR balance changed
    """
    global _last_equity_log_ts, _last_equity_logged_balance

    now_ts = time.time()

    should_log = False
    if (now_ts - _last_equity_log_ts) >= float(EQUITY_LOG_MIN_INTERVAL_SEC):
        should_log = True

    if _last_equity_logged_balance is None or float(balance) != float(_last_equity_logged_balance):
        should_log = True

    if should_log:
        _log_equity_raw(pt_dir, pt_tag, float(balance))
        _last_equity_log_ts = now_ts
        _last_equity_logged_balance = float(balance)


def _can_open(positions: dict, symbol: str) -> bool:
    open_pos = positions.get("open", {})
    if SETTINGS.ONE_POSITION_PER_SYMBOL and symbol in open_pos:
        return False
    return len(open_pos) < SETTINGS.MAX_OPEN_TRADES


def _fee_cost(notional: float) -> float:
    return float(notional) * SETTINGS.FEE_PER_SIDE


def open_position(pt_dir: str, pt_tag: str, exits_mod, signal_row: dict, equity: dict, positions: dict):
    symbol = signal_row["symbol"]
    direction = signal_row["direction"]

    if not _can_open(positions, symbol):
        return

    entry_ref = float(signal_row["entry_ref_price"])
    ema25_4h = float(signal_row["ema25_4h"])
    fib0236 = float(signal_row["fib_0236"])

    # SL Regel: unter EMA25_4h UND unter Fib0.236 -> mathematisch: unter min(...)
    if direction == "LONG":
        sl_raw = min(ema25_4h, fib0236)
        sl = sl_raw * (1 - SETTINGS.SL_BUFFER)
    else:
        sl_raw = max(ema25_4h, fib0236)  # fuer Short ueber beidem
        sl = sl_raw * (1 + SETTINGS.SL_BUFFER)

    entry = apply_slippage(entry_ref, direction, SETTINGS.SLIPPAGE, side="entry")
    balance = float(equity["balance"])

    notional = calc_position_notional(balance, SETTINGS.RISK_PCT, entry, sl)
    if notional <= 0:
        return

    qty = notional / entry

    tps = exits_mod.tp_levels(entry, direction)
    splits = exits_mod.tp_splits()

    positions.setdefault("open", {})
    positions["open"][symbol] = {
        "symbol": symbol,
        "direction": direction,
        "entry": entry,
        "entry_ref": entry_ref,
        "sl": sl,
        "qty_total": qty,
        "qty_open": qty,
        "notional": notional,
        "tps": tps,
        "splits": splits,
        "filled": {k: 0.0 for k, _ in splits},
        "moved_sl": False,
        "signal_id": signal_row.get("signal_id", ""),
        "ts_open": utc_now_iso(),
    }

    _log_event(
        pt_dir,
        pt_tag,
        {
            "type": "OPEN",
            "symbol": symbol,
            "direction": direction,
            "entry": entry,
            "sl": sl,
            "notional": notional,
            "qty": qty,
        },
    )
    info(f"🟢 {pt_tag} OPEN {symbol} {direction} entry={entry:.6f} sl={sl:.6f} notional={notional:.2f}")


def update_positions(pt_dir: str, pt_tag: str, exits_mod, equity: dict, positions: dict):
    open_pos = positions.get("open", {})
    if not open_pos:
        return

    balance = float(equity["balance"])
    closed_symbols = []

    for symbol, pos in list(open_pos.items()):
        # --- PRICE FETCH (BUGFIX: handle None safely) ---
        try:
            px = get_price(symbol)
        except Exception as e:
            warn(f"{pt_tag} price error {symbol}: {e}")
            continue

        if px is None:
            # price can be None (network hiccup). Do NOT crash the engine.
            warn(f"{pt_tag} price unavailable {symbol} -> skip manage step")
            continue

        direction = pos["direction"]
        entry = float(pos["entry"])
        sl = float(pos["sl"])
        qty_open = float(pos["qty_open"])

        # --- Stop check ---
        stop_hit = (px <= sl) if direction == "LONG" else (px >= sl)
        if stop_hit:
            exit_fill = apply_slippage(sl, direction, SETTINGS.SL
