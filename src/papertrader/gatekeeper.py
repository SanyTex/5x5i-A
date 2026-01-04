# src/papertrader/gatekeeper.py
# neu seit update version 1.2
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple, List


# -----------------------------
# Result object
# -----------------------------
@dataclass
class GatekeeperDecision:
    allow: bool
    reason: str
    meta: Dict[str, Any]


# -----------------------------
# Helpers (robust parsing)
# -----------------------------
def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


def _safe_str(x: Any, default: str = "") -> str:
    try:
        return default if x is None else str(x)
    except Exception:
        return default


def _norm_side(side: str) -> str:
    s = _safe_str(side).upper().strip()
    if s in ("LONG", "BUY"):
        return "LONG"
    if s in ("SHORT", "SELL"):
        return "SHORT"
    return s  # fallback


def _remaining_qty(pos: Dict[str, Any]) -> float:
    """
    Canonical remaining qty for our papertrader schema.

    Supports BOTH schemas:
    - old: remaining_qty / qty / position_qty
    - v1.2: qty_open (and qty_total)
    """
    # v1.2 primary
    q_open = _safe_float(pos.get("qty_open", None), default=float("nan"))
    if q_open == q_open:  # not nan
        return q_open

    # fallbacks
    return _safe_float(pos.get("remaining_qty", pos.get("qty", pos.get("position_qty", 0.0))), 0.0)


def _initial_qty(pos: Dict[str, Any]) -> float:
    """
    Initial qty for sold_pct computation.
    Supports:
    - v1.2: qty_total
    - old: initial_qty / qty_initial / initial_position_qty
    """
    q_total = _safe_float(pos.get("qty_total", None), default=float("nan"))
    if q_total == q_total:  # not nan
        return q_total

    return _safe_float(pos.get("initial_qty", pos.get("qty_initial", pos.get("initial_position_qty", 0.0))), 0.0)


# -----------------------------
# Restposition Rule (Patch 1.2)
# "not active managed" if:
# - sold_pct > 50% (remaining <= 50%)
# - SL above BreakEven
# - and no "requires_management" flag (optional)
# -----------------------------
def is_rest_position_not_active(pos: Dict[str, Any]) -> bool:
    """
    Returns True if position should NOT count as 'active managed'
    according to Marvin's Restposition-Regel.

    Schema tolerant:
    - supports v1.2 qty_total/qty_open
    - supports old initial_qty/remaining_qty
    - uses sl, break_even/be, entry_price as fallback
    - uses requires_management/managed flag as override if present
    """

    # If strategy explicitly says it's still managed, count it active
    requires_management = pos.get("requires_management", None)
    if requires_management is True:
        return False

    # --- Compute sold_pct ---
    sold_pct = pos.get("sold_pct", None)
    if sold_pct is None:
        initial_qty = _initial_qty(pos)
        remaining_qty = _remaining_qty(pos)

        if initial_qty > 0:
            sold_pct = max(0.0, min(1.0, 1.0 - (remaining_qty / initial_qty)))
        else:
            # fallback: if we can't compute, assume not rest-position
            sold_pct = 0.0
    else:
        sold_pct = _safe_float(sold_pct, 0.0)
        if sold_pct > 1.0:
            sold_pct = sold_pct / 100.0

    # Condition 1: more than 50% sold
    cond_sold = sold_pct > 0.5

    # --- SL vs BreakEven ---
    sl = _safe_float(pos.get("sl", pos.get("stop_loss", None)), default=float("nan"))
    be = pos.get("break_even", pos.get("be", None))
    if be is None:
        be = pos.get("entry_price", pos.get("avg_entry", pos.get("entry", None)))
    be = _safe_float(be, default=float("nan"))

    side = _norm_side(pos.get("side", pos.get("direction", "")))

    # For LONG: SL > BE
    # For SHORT: SL < BE
    cond_sl_be = False
    if side == "LONG":
        if sl == sl and be == be:  # not nan
            cond_sl_be = sl > be
    elif side == "SHORT":
        if sl == sl and be == be:
            cond_sl_be = sl < be

    # Condition 3: no active setup decisions needed
    no_active_decisions = pos.get("no_active_decisions", None)
    if no_active_decisions is None:
        # neutral fallback: if sold+sl>be satisfied, treat as rest
        cond_decisions = True
    else:
        cond_decisions = bool(no_active_decisions)

    return bool(cond_sold and cond_sl_be and cond_decisions)


def counts_as_active_managed(pos: Dict[str, Any]) -> bool:
    """
    True if this position counts toward the max-active-managed limit.
    """
    # closed flags
    if pos.get("status") in ("CLOSED", "CLOSE", "DONE"):
        return False
    if bool(pos.get("is_closed", False)) is True:
        return False

    # remaining qty -> not active
    remaining_qty = _remaining_qty(pos)
    if remaining_qty <= 0:
        return False

    # Restposition-Regel
    if is_rest_position_not_active(pos):
        return False

    return True


def extract_open_positions(positions_state: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """
    Normalizes various state shapes to a {symbol: position_dict} mapping.
    Expected common shape: {"open": {...}} or direct mapping.
    """
    if not isinstance(positions_state, dict):
        return {}

    if "open" in positions_state and isinstance(positions_state["open"], dict):
        return positions_state["open"]

    maybe = {k: v for k, v in positions_state.items() if isinstance(v, dict)}
    return maybe


# -----------------------------
# Core Gatekeeper
# -----------------------------
def gatekeeper_can_open_trade(
    *,
    symbol: str,
    side: str,
    positions_state: Dict[str, Any],
    max_active_managed: int = 3,
    enforce_one_position_per_symbol: bool = True,
    enforce_no_hedge: bool = True,
) -> GatekeeperDecision:
    """
    Central rule enforcement BEFORE opening a new position.
    Works for PT_A / PT_B / PT_C.
    """

    sym = _safe_str(symbol).upper().strip()
    req_side = _norm_side(side)

    open_positions = extract_open_positions(positions_state)

    # 1) Enforce one position per symbol (no doubles)
    if enforce_one_position_per_symbol and sym in open_positions:
        pos = open_positions[sym]
        remaining_qty = _remaining_qty(pos)
        if remaining_qty > 0:
            return GatekeeperDecision(
                allow=False,
                reason="BLOCK: symbol already has an open position",
                meta={
                    "rule": "one_position_per_symbol",
                    "symbol": sym,
                    "existing_side": _norm_side(pos.get("side", pos.get("direction", ""))),
                    "remaining_qty": remaining_qty,
                },
            )

    # 2) No hedge rule (LONG + SHORT simultaneously)
    if enforce_no_hedge and sym in open_positions:
        pos = open_positions[sym]
        existing_side = _norm_side(pos.get("side", pos.get("direction", "")))
        if existing_side and existing_side != req_side:
            remaining_qty = _remaining_qty(pos)
            if remaining_qty > 0:
                return GatekeeperDecision(
                    allow=False,
                    reason="BLOCK: hedge detected (opposite side already open)",
                    meta={"rule": "no_hedge", "symbol": sym, "requested": req_side, "existing": existing_side},
                )

    # 3) Max active managed positions (Restposition-Regel applied)
    active_managed: List[Tuple[str, Dict[str, Any]]] = []
    for s, p in open_positions.items():
        if counts_as_active_managed(p):
            active_managed.append((s, p))

    if len(active_managed) >= max_active_managed:
        return GatekeeperDecision(
            allow=False,
            reason=f"BLOCK: max active managed positions reached ({len(active_managed)}/{max_active_managed})",
            meta={
                "rule": "max_active_managed",
                "active_count": len(active_managed),
                "max": max_active_managed,
                "active_symbols": [s for s, _ in active_managed],
            },
        )

    return GatekeeperDecision(
        allow=True,
        reason="ALLOW",
        meta={"active_count": len(active_managed), "max": max_active_managed},
    )
