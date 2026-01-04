def tp_levels(entry: float, direction: str):
    if direction == "LONG":
        return {
            "TP1": entry * 1.0100,
            "TP2": entry * 1.0200,
            "TP3": entry * 1.0300,
            "TP4": entry * 1.0400,
        }
    else:
        return {
            "TP1": entry * (1 - 0.0100),
            "TP2": entry * (1 - 0.0200),
            "TP3": entry * (1 - 0.0300),
            "TP4": entry * (1 - 0.0400),
        }

def tp_splits():
    return [("TP1", 0.25), ("TP2", 0.25), ("TP3", 0.25), ("TP4", 0.25)]

def after_tp3_sl_move(entry: float, direction: str):
    # Nach TP3 SL -> TP2
    return entry * 1.0200 if direction == "LONG" else entry * (1 - 0.0200)
