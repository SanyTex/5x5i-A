def tp_levels(entry: float, direction: str):
    # simple: 50/50
    if direction == "LONG":
        return {"TP1": entry * 1.0150, "TP2": entry * 1.0350}
    else:
        return {"TP1": entry * (1 - 0.0150), "TP2": entry * (1 - 0.0350)}

def tp_splits():
    return [("TP1", 0.50), ("TP2", 0.50)]

def after_tp2_sl_move(entry: float, direction: str):
    return entry  # BE
