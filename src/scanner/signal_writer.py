import hashlib
from src.common.csvio import append_row_csv

def make_signal_id(ts_signal: str, symbol: str, direction: str) -> str:
    raw = f"{ts_signal}|{symbol}|{direction}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]

def write_signal(path: str, row: dict) -> None:
    append_row_csv(path, row)
