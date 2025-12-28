import requests

BASE = "https://api.binance.com/api/v3/ticker/price"

def get_price(symbol: str) -> float | None:
    r = requests.get(BASE, params={"symbol": symbol}, timeout=10)
    r.raise_for_status()
    return float(r.json()["price"])
