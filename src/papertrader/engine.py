import os
from typing import Dict, Any
from config.settings import SETTINGS
from src.common.csvio import append_row_csv, read_csv_rows
from src.common.timeutils import utc_now_iso
from src.common.log import info, warn
from src.papertrader.state_store import load_json, save_json
from src.papertrader.price_feed import get_price
from src.papertrader.risk import calc_position_notional, apply_slippage

def _pt_paths(pt_dir: str):
    return {
        "cursor": os.path.join(pt_dir, "cursor.json"),
        "positions": os.path.join(pt_dir, "positions.json"),
        "trades": os.path.join(pt_dir, "trades.csv"),
        "equity": os.path.join(pt_dir, "equity.csv"),
        "events": os.path.join(pt_dir, "events.csv"),
    }

def _load_state(pt_dir: str):
    p = _pt_paths(pt_dir)
    cursor = load_json(p["cursor"], {"last_index": -1})
    positions = load_json(p["positions"], {"open": {}})
    equity = load_json(os.path.join(pt_dir, "equity_state.json"), {"balance": SETTINGS.START_BALANCE_USDT})
    return cursor, positions, equity

def _save_state(pt_dir: str, cursor, positions, equity):
    p = _pt_paths(pt_dir)
    save_json(p["cursor"], cursor)
    save_json(p["positions"], positions)
    save_json(os.path.join(pt_dir, "equity_state.json"), equity)

def _log_event(pt_dir: str, pt_tag: str, event: dict):
    p = _pt_paths(pt_dir)
    event2 = {"ts": utc_now_iso(), "pt_tag": pt_tag, **event}
    append_row_csv(p["events"], event2)

def _log_trade(pt_dir: str, pt_tag: str, trade: dict):
    p = _pt_paths(pt_dir)
    row = {"ts_close": utc_now_iso(), "pt_tag": pt_tag, **trade}
    append_row_csv(p["trades"], row)

def _log_equity(pt_dir: str, pt_tag: str, balance: float):
    p = _pt_paths(pt_dir)
    append_row_csv(p["equity"], {"ts": utc_now_iso(), "pt_tag": pt_tag, "balance": balance})

def _can_open(positions: dict, symbol: str) -> bool:
    open_pos = positions.get("open", {})
    if SETTINGS.ONE_POSITION_PER_SYMBOL and symbol in open_pos:
        return False
    return len(open_pos) < SETTINGS.MAX_OPEN_TRADES

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

    _log_event(pt_dir, pt_tag, {
        "type": "OPEN",
        "symbol": symbol,
        "direction": direction,
        "entry": entry,
        "sl": sl,
        "notional": notional,
        "qty": qty
    })
    info(f"🟢 {pt_tag} OPEN {symbol} {direction} entry={entry:.6f} sl={sl:.6f} notional={notional:.2f}")

def _fee_cost(notional: float) -> float:
    return notional * SETTINGS.FEE_PER_SIDE

def update_positions(pt_dir: str, pt_tag: str, exits_mod, equity: dict, positions: dict):
    open_pos = positions.get("open", {})
    if not open_pos:
        return

    balance = float(equity["balance"])
    closed_symbols = []

    for symbol, pos in list(open_pos.items()):
        try:
            px = get_price(symbol)
        except Exception as e:
            warn(f"{pt_tag} price error {symbol}: {e}")
            continue

        direction = pos["direction"]
        entry = float(pos["entry"])
        sl = float(pos["sl"])
        qty_open = float(pos["qty_open"])

        # --- Stop check ---
        stop_hit = (px <= sl) if direction == "LONG" else (px >= sl)
        if stop_hit:
            exit_fill = apply_slippage(sl, direction, SETTINGS.SLIPPAGE, side="exit")
            pnl = (exit_fill - entry) * qty_open if direction == "LONG" else (entry - exit_fill) * qty_open
            fee = _fee_cost(pos["notional"])
            balance += pnl - fee
            _log_event(pt_dir, pt_tag, {"type":"STOP", "symbol":symbol, "price":px, "sl":sl, "pnl":pnl})
            _log_trade(pt_dir, pt_tag, {
                "symbol": symbol,
                "direction": direction,
                "entry": entry,
                "exit": exit_fill,
                "reason": "SL",
                "pnl_usdt": pnl - fee,
                "signal_id": pos.get("signal_id",""),
            })
            closed_symbols.append(symbol)
            continue

        # --- TP checks in Reihenfolge ---
        for tp_name, frac in pos["splits"]:
            if pos["filled"].get(tp_name, 0.0) >= frac:
                continue

            tp_price = float(pos["tps"][tp_name])
            tp_hit = (px >= tp_price) if direction == "LONG" else (px <= tp_price)
            if not tp_hit:
                continue

            close_qty = pos["qty_total"] * frac
            close_qty = min(close_qty, pos["qty_open"])
            if close_qty <= 0:
                pos["filled"][tp_name] = frac
                continue

            exit_fill = apply_slippage(tp_price, direction, SETTINGS.SLIPPAGE, side="exit")
            pnl = (exit_fill - entry) * close_qty if direction == "LONG" else (entry - exit_fill) * close_qty
            fee = _fee_cost(pos["notional"]) * frac

            balance += pnl - fee
            pos["qty_open"] -= close_qty
            pos["filled"][tp_name] = frac

            _log_event(pt_dir, pt_tag, {"type":"TP", "symbol":symbol, "tp":tp_name, "tp_price":tp_price, "pnl":pnl})

            # SL move rules (A nach TP2, B nach TP3, C nach TP2)
            if hasattr(exits_mod, "after_tp2_sl_move") and tp_name == "TP2" and not pos["moved_sl"]:
                new_sl = exits_mod.after_tp2_sl_move(entry, direction)
                pos["sl"] = float(new_sl)
                pos["moved_sl"] = True
                _log_event(pt_dir, pt_tag, {"type":"MOVE_SL", "symbol":symbol, "new_sl":new_sl, "after":tp_name})

            if hasattr(exits_mod, "after_tp3_sl_move") and tp_name == "TP3" and not pos["moved_sl"]:
                new_sl = exits_mod.after_tp3_sl_move(entry, direction)
                pos["sl"] = float(new_sl)
                pos["moved_sl"] = True
                _log_event(pt_dir, pt_tag, {"type":"MOVE_SL", "symbol":symbol, "new_sl":new_sl, "after":tp_name})

            info(f"🟡 {pt_tag} TP {symbol} {tp_name} pnl={pnl - fee:.2f} bal={balance:.2f}")

            # wenn alles geschlossen
            if pos["qty_open"] <= 1e-12:
                _log_trade(pt_dir, pt_tag, {
                    "symbol": symbol,
                    "direction": direction,
                    "entry": entry,
                    "exit": exit_fill,
                    "reason": "TP_LAST",
                    "pnl_usdt": pnl - fee,
                    "signal_id": pos.get("signal_id",""),
                })
                closed_symbols.append(symbol)
            break  # immer nur 1 TP pro loop

    for sym in closed_symbols:
        open_pos.pop(sym, None)

    equity["balance"] = float(balance)
    _log_equity(pt_dir, pt_tag, float(balance))

def run_papertrader_loop(pt_tag: str, pt_dir: str, exits_mod):
    cursor, positions, equity = _load_state(pt_dir)

    info(f"🚀 Papertrader gestartet: {pt_tag} | balance={equity['balance']:.2f}")

    while True:
        # 1) neue Signals lesen
        signals = read_csv_rows(SETTINGS.SIGNALS_CONFIRMED)
        last_idx = int(cursor.get("last_index", -1))

        new_rows = []
        if signals and last_idx < len(signals) - 1:
            new_rows = signals[last_idx+1:]

        # 2) Trades oeffnen
        for i, row in enumerate(new_rows, start=last_idx+1):
            cursor["last_index"] = i
            # nur LONG/SHORT
            if row.get("direction") not in ("LONG", "SHORT"):
                continue
            open_position(pt_dir, pt_tag, exits_mod, row, equity, positions)

        # 3) offene Trades managen
        update_positions(pt_dir, pt_tag, exits_mod, equity, positions)

        # 4) State speichern
        _save_state(pt_dir, cursor, positions, equity)
