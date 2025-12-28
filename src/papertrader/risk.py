def calc_position_notional(balance: float, risk_pct: float, entry: float, sl: float) -> float:
    """
    Notional so dass Loss bei SL = balance * risk_pct.
    """
    risk_usdt = balance * risk_pct
    sl_dist = abs(entry - sl)
    if sl_dist <= 0:
        return 0.0
    # Loss = qty * sl_dist  => qty = risk/sl_dist  => notional = qty*entry
    qty = risk_usdt / sl_dist
    notional = qty * entry
    return float(notional)

def apply_slippage(price: float, direction: str, slippage: float, side: str) -> float:
    """
    side: "entry" oder "exit"
    LONG: entry schlechter (hoch), exit schlechter (runter)
    SHORT: entry schlechter (runter), exit schlechter (hoch)
    """
    if direction == "LONG":
        return price * (1 + slippage) if side == "entry" else price * (1 - slippage)
    else:
        return price * (1 - slippage) if side == "entry" else price * (1 + slippage)
