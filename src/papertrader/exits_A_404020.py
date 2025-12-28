def tp_levels(entry: float, direction: str):
    # Proof: fix
    if direction == "LONG":
        return {
            "TP1": entry * 1.0100,
            "TP2": entry * 1.0225,
            "TP3": entry * 1.0400,
        }
    else:
        return {
            "TP1": entry * (1 - 0.0100),
            "TP2": entry * (1 - 0.0225),
            "TP3": entry * (1 - 0.0400),
        }

def tp_splits():
    return [("TP1", 0.40), ("TP2", 0.40), ("TP3", 0.20)]

def after_tp2_sl_move(entry: float, direction: str):
    # SL der Restposition auf TP1 Level
    return entry * 1.0100 if direction == "LONG" else entry * (1 - 0.0100)
