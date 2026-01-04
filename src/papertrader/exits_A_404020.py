#Version 1.2 (universelle update_positions)
def tp_splits():
    return [
        ("TP1", 0.40),
        ("TP2", 0.40),
        ("TP3", 0.20),
    ]


def tp_levels(entry: float, direction: str):
    entry = float(entry)
    direction = (direction or "LONG").upper()

    p1, p2, p3 = 0.010, 0.022, 0.035

    if direction == "SHORT":
        return {
            "TP1": entry * (1.0 - p1),
            "TP2": entry * (1.0 - p2),
            "TP3": entry * (1.0 - p3),
        }

    return {
        "TP1": entry * (1.0 + p1),
        "TP2": entry * (1.0 + p2),
        "TP3": entry * (1.0 + p3),
    }


def after_tp2_sl_move(entry: float, direction: str):
    tps = tp_levels(entry, direction)
    return float(tps["TP1"])

def update_positions(pt_dir: str, pt_tag: str, exits_mod, equity: dict, positions: dict):
    open_pos = positions.get("open", {})
    if not open_pos:
        return

    def _maybe_sl_move_after_tp(tp_key: str, entry: float, direction: str):
        """
        Supports:
        - exits_mod.after_tp2_sl_move(entry, direction) triggered after TP2
        - exits_mod.after_tp3_sl_move(entry, direction) triggered after TP3
        Extendable later.
        """
        fn_name = None
        if tp_key == "TP2" and hasattr(exits_mod, "after_tp2_sl_move"):
            fn_name = "after_tp2_sl_move"
        elif tp_key == "TP3" and hasattr(exits_mod, "after_tp3_sl_move"):
            fn_name = "after_tp3_sl_move"

        if not fn_name:
            return None

        try:
            fn = getattr(exits_mod, fn_name)
            return float(fn(entry, direction))
        except Exception as e:
            warn(f"{pt_tag} SL move error {tp_key}: {e}")
            return None

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
            positions["open"].pop(symbol, None)
            continue

        # Ensure patch fields exist (backward compatibility)
        pos.setdefault("break_even", entry)
        pos.setdefault("no_active_decisions", False)
        pos.setdefault("filled", {})

        # -----------------------------
        # STOP LOSS
        # -----------------------------
        stop_hit = (px <= sl) if direction == "LONG" else (px >= sl)
        if stop_hit:
            exit_fill = apply_slippage(sl, direction, SETTINGS.SLIPPAGE, side="exit")
            notional_exit = qty_open * exit_fill

            pnl = (exit_fill - entry) * qty_open if direction == "LONG" else (entry - exit_fill) * qty_open
            fee = _fee_cost(notional_exit)

            equity["balance"] = float(equity.get("balance", 0.0)) + pnl - fee

            _log_trade(pt_dir, pt_tag, {
                "symbol": symbol,
                "type": "STOP",
                "direction": direction,
                "qty": qty_open,
                "entry": entry,
                "exit": exit_fill,
                "pnl": pnl,
                "fee": fee,
            })

            _log_event(pt_dir, pt_tag, {
                "type": "CLOSE_STOP",
                "symbol": symbol,
                "direction": direction,
                "qty": qty_open,
                "entry": entry,
                "exit": exit_fill,
                "pnl": pnl,
                "fee": fee,
            })

            info(f"🔴 {pt_tag} STOP {symbol} {direction} exit={exit_fill:.6f} pnl={pnl:.2f} fee={fee:.2f}")
            positions["open"].pop(symbol, None)
            continue

        # -----------------------------
        # TAKE PROFITS (generic TP1..TPn)
        # -----------------------------
        tps = pos.get("tps")
        if not isinstance(tps, dict):
            tps = exits_mod.tp_levels(entry, direction)
            pos["tps"] = tps

        splits = pos.get("splits")
        if not isinstance(splits, list):
            splits = exits_mod.tp_splits()
            pos["splits"] = splits

        filled = pos.get("filled", {})
        if not isinstance(filled, dict):
            filled = {}
            pos["filled"] = filled

        def _tp_hit(tp_price: float) -> bool:
            return (px >= tp_price) if direction == "LONG" else (px <= tp_price)

        # Execute fills in defined split order
        for tp_key, pct in splits:
            if qty_open <= 0:
                break

            if float(filled.get(tp_key, 0.0) or 0.0) > 0.0:
                continue  # already filled

            tp_price = float(tps.get(tp_key, 0.0) or 0.0)
            if tp_price <= 0:
                continue

            if not _tp_hit(tp_price):
                continue

            pct = float(pct)
            fill_qty = min(qty_open, qty_total * pct)
            if fill_qty <= 0:
                continue

            exit_fill = apply_slippage(tp_price, direction, SETTINGS.SLIPPAGE, side="exit")
            notional_exit = fill_qty * exit_fill

            pnl = (exit_fill - entry) * fill_qty if direction == "LONG" else (entry - exit_fill) * fill_qty
            fee = _fee_cost(notional_exit)

            equity["balance"] = float(equity.get("balance", 0.0)) + pnl - fee

            qty_open -= fill_qty
            pos["qty_open"] = qty_open
            filled[tp_key] = fill_qty

            _log_trade(pt_dir, pt_tag, {
                "symbol": symbol,
                "type": tp_key,
                "direction": direction,
                "qty": fill_qty,
                "entry": entry,
                "exit": exit_fill,
                "pnl": pnl,
                "fee": fee,
            })

            _log_event(pt_dir, pt_tag, {
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
            })

            info(f"🟡 {pt_tag} {tp_key} {symbol} {direction} exit={exit_fill:.6f} qty={fill_qty:.6f} pnl={pnl:.2f} fee={fee:.2f} remain={qty_open:.6f}")

            # Optional SL move rule (variant-specific)
            new_sl = _maybe_sl_move_after_tp(tp_key, entry, direction)
            if new_sl is not None and new_sl > 0:
                pos["sl"] = float(new_sl)
                pos["moved_sl"] = True
                _log_event(pt_dir, pt_tag, {
                    "type": "SL_MOVE",
                    "symbol": symbol,
                    "direction": direction,
                    "new_sl": float(new_sl),
                    "after_tp": tp_key,
                })
                info(f"🔧 {pt_tag} SL_MOVE {symbol} {direction} -> {float(new_sl):.6f} (after {tp_key})")

            # Patch 1.2: Restposition-Regel nach Fill/SL-Move prüfen
            _maybe_mark_restposition(pos)

        # -----------------------------
        # Close if fully exited
        # -----------------------------
        if float(pos.get("qty_open", 0.0) or 0.0) <= 0.0:
            _log_event(pt_dir, pt_tag, {"type": "CLOSE_TP", "symbol": symbol, "direction": direction})
            positions["open"].pop(symbol, None)
            info(f"✅ {pt_tag} CLOSED {symbol} {direction}")
            continue
