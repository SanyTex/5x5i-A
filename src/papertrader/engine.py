#Version 1.2
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
from src.papertrader.gatekeeper import gatekeeper_can_open_trade

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

def _sold_pct(pos: dict) -> float:
    qty_total = float(pos.get("qty_total", 0.0) or 0.0)
    qty_open  = float(pos.get("qty_open", 0.0) or 0.0)
    if qty_total <= 0:
        return 0.0
    return max(0.0, min(1.0, 1.0 - (qty_open / qty_total)))


def _maybe_mark_restposition(pos: dict) -> None:
    """
    Patch 1.2: Restposition-Regel.
    Wenn >50% verkauft und SL über BE (Long) / unter BE (Short),
    dann no_active_decisions = True (zählt nicht mehr als aktiv gemanagt).
    """
    direction = pos.get("direction", "")
    sl = float(pos.get("sl", 0.0) or 0.0)
    be = pos.get("break_even", pos.get("entry", None))
    if be is None:
        return
    be = float(be)

    sold = _sold_pct(pos)
    if sold <= 0.5:
        # noch zu viel offen -> weiterhin aktiv
        pos["no_active_decisions"] = False
        return

    if direction == "LONG":
        if sl > be:
            pos["no_active_decisions"] = True
    else:
        # SHORT
        if sl < be:
            pos["no_active_decisions"] = True


def open_position(pt_dir: str, pt_tag: str, exits_mod, signal_row: dict, equity: dict, positions: dict):
    symbol = signal_row["symbol"]
    direction = signal_row["direction"]
 
    # -----------------------------
    # PATCH 1.2: Central Gatekeeper (P0)
    # - max active managed = MAX_OPEN_TRADES (Restposition-rule aware)
    # - no hedge per symbol
    # - one position per symbol
    # -----------------------------
    gk = gatekeeper_can_open_trade(
        symbol=symbol,
        side=direction,              # "LONG" / "SHORT"
        positions_state=positions,   # expects {"open": {...}}
        max_active_managed=SETTINGS.MAX_OPEN_TRADES,
        enforce_one_position_per_symbol=True,
        enforce_no_hedge=True,
    )

    if not gk.allow:
        _log_event(
            pt_dir,
            pt_tag,
            {
                "type": "GATEKEEPER_BLOCK",
                "symbol": symbol,
                "direction": direction,
                "reason": gk.reason,
                "meta": str(gk.meta),
            },
        )
        warn(f"⛔ {pt_tag} GATEKEEPER_BLOCK {symbol} {direction} | {gk.reason} | {gk.meta}")
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
        "break_even": entry,
        "no_active_decisions": False,
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

    balance = float(equity.get("balance", 0.0))
    closed_symbols = []

    for symbol, pos in list(open_pos.items()):
        # --- PRICE FETCH ---
        try:
            px = get_price(symbol)
        except Exception as e:
            warn(f"{pt_tag} price error {symbol}: {e}")
            continue

        if px is None:
            warn(f"{pt_tag} price unavailable {symbol} -> skip manage step")
            continue

        direction = pos.get("direction", "LONG")
        entry = float(pos.get("entry", 0.0) or 0.0)
        sl = float(pos.get("sl", 0.0) or 0.0)
        qty_open = float(pos.get("qty_open", 0.0) or 0.0)
        qty_total = float(pos.get("qty_total", qty_open) or 0.0)

        if qty_open <= 0 or entry <= 0:
            # safety: remove zombie
            closed_symbols.append(symbol)
            continue

        # --- STOP LOSS CHECK ---
        stop_hit = (px <= sl) if direction == "LONG" else (px >= sl)
        if stop_hit:
            exit_fill = apply_slippage(sl, direction, SETTINGS.SLIPPAGE, side="exit")
            notional_exit = qty_open * exit_fill

            # PnL
            if direction == "LONG":
                pnl = (exit_fill - entry) * qty_open
            else:
                pnl = (entry - exit_fill) * qty_open

            fee = _fee_cost(notional_exit)

            # Update equity
            equity["balance"] = float(equity.get("balance", 0.0)) + float(pnl) - float(fee)

            _log_trade(
                pt_dir,
                pt_tag,
                {
                    "symbol": symbol,
                    "type": "STOP",
                    "direction": direction,
                    "qty": qty_open,
                    "entry": entry,
                    "exit": exit_fill,
                    "pnl": pnl,
                    "fee": fee,
                },
            )

            _log_event(
                pt_dir,
                pt_tag,
                {
                    "type": "CLOSE_STOP",
                    "symbol": symbol,
                    "direction": direction,
                    "qty": qty_open,
                    "entry": entry,
                    "exit": exit_fill,
                    "pnl": pnl,
                    "fee": fee,
                },
            )

            info(f"🔴 {pt_tag} STOP {symbol} {direction} px={px:.6f} sl={sl:.6f} exit={exit_fill:.6f} pnl={pnl:.2f} fee={fee:.2f}")

            # Close position
            pos["qty_open"] = 0.0
            closed_symbols.append(symbol)
            continue

        # --- TAKE PROFITS ---
        # levels are dict-like: {"TP1": price, "TP2": price, ...} OR list/tuple => handle both
        tps = pos.get("tps", None)
        if tps is None:
            tps = exits_mod.tp_levels(entry, direction)
            pos["tps"] = tps

        splits = pos.get("splits", None)
        if splits is None:
            splits = exits_mod.tp_splits()
            pos["splits"] = splits

        filled = pos.get("filled", None)
        if not isinstance(filled, dict):
            filled = {}
            pos["filled"] = filled

        # helper to get tp price
        def _tp_price(tp_key: str) -> float:
            if isinstance(tps, dict):
                return float(tps.get(tp_key, 0.0) or 0.0)
            # if list of tuples
            try:
                for k, v in tps:
                    if k == tp_key:
                        return float(v)
            except Exception:
                pass
            return 0.0

        # Execute TP fills in order as defined by splits
        for tp_key, pct in splits:
            if qty_open <= 0:
                break

            if float(filled.get(tp_key, 0.0) or 0.0) > 0:
                continue  # already filled

            tp_price = _tp_price(tp_key)
            if tp_price <= 0:
                continue

            tp_hit = (px >= tp_price) if direction == "LONG" else (px <= tp_price)
            if not tp_hit:
                continue

            # fill this chunk
            pct = float(pct)
            fill_qty = min(qty_open, qty_total * pct)
            if fill_qty <= 0:
                continue

            exit_fill = apply_slippage(tp_price, direction, SETTINGS.SLIPPAGE, side="exit")
            notional_exit = fill_qty * exit_fill

            if direction == "LONG":
                pnl = (exit_fill - entry) * fill_qty
            else:
                pnl = (entry - exit_fill) * fill_qty

            fee = _fee_cost(notional_exit)

            equity["balance"] = float(equity.get("balance", 0.0)) + float(pnl) - float(fee)

            qty_open -= fill_qty
            pos["qty_open"] = qty_open
            filled[tp_key] = float(filled.get(tp_key, 0.0) or 0.0) + fill_qty

            _log_trade(
                pt_dir,
                pt_tag,
                {
                    "symbol": symbol,
                    "type": tp_key,
                    "direction": direction,
                    "qty": fill_qty,
                    "entry": entry,
                    "exit": exit_fill,
                    "pnl": pnl,
                    "fee": fee,
                },
            )

            _log_event(
                pt_dir,
                pt_tag,
                {
                    "type": "TP_FILL",
                    "symbol": symbol,
                    "direction": direction,
                    "tp": tp_key,
                    "qty": fill_qty,
                    "entry": entry,
                    "exit": exit_fill,
                    "pnl": pnl,
                    "fee": fee,
                    "qty_open": qty_open,
                },
            )

            info(f"🟡 {pt_tag} {tp_key} {symbol} {direction} exit={exit_fill:.6f} qty={fill_qty:.6f} pnl={pnl:.2f} fee={fee:.2f} remaining={qty_open:.6f}")

            # --- PATCH 1.2: Restposition-Regel check after each TP fill
            _maybe_mark_restposition(pos)

            # --- Move SL logic delegated to exits_mod (if available)
            # If module has a function: maybe_move_sl(pos, tp_key) -> new_sl or None
            new_sl = None
            if hasattr(exits_mod, "maybe_move_sl"):
                try:
                    new_sl = exits_mod.maybe_move_sl(pos, tp_key)
                except Exception as e:
                    warn(f"{pt_tag} maybe_move_sl error {symbol}: {e}")

            if new_sl is not None:
                try:
                    new_sl = float(new_sl)
                    if new_sl > 0:
                        pos["sl"] = new_sl
                        pos["moved_sl"] = True
                        _log_event(
                            pt_dir,
                            pt_tag,
                            {"type": "SL_MOVE", "symbol": symbol, "direction": direction, "new_sl": new_sl, "after_tp": tp_key},
                        )
                        info(f"🔧 {pt_tag} SL_MOVE {symbol} {direction} -> {new_sl:.6f} (after {tp_key})")

                        # Restposition check again (SL changed)
                        _maybe_mark_restposition(pos)
                except Exception:
                    pass

        # If fully closed via TPs
        if qty_open <= 0:
            _log_event(pt_dir, pt_tag, {"type": "CLOSE_TP", "symbol": symbol, "direction": direction})
            closed_symbols.append(symbol)

    # Remove closed
    for s in closed_symbols:
        try:
            positions["open"].pop(s, None)
        except Exception:
            pass

    # Persist state
    try:
        # equity_state saved outside by caller, but we update dict here
        pass
    except Exception:
        pass
