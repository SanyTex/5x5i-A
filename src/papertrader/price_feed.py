import socket
import time
import requests
from src.common.log import warn

BASE_URLS = [
    "https://api.binance.com",
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://api3.binance.com",
]
PRICE_PATH = "/api/v3/ticker/price"


# Force IPv4 (fixes sporadic 'No route to host' on Raspberry Pi)
def _force_ipv4_only():
    orig_getaddrinfo = socket.getaddrinfo

    def ipv4_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        return orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)

    socket.getaddrinfo = ipv4_getaddrinfo


_force_ipv4_only()


def get_price(symbol: str) -> float | None:
    params = {"symbol": symbol}

    for base in BASE_URLS:
        try:
            r = requests.get(base + PRICE_PATH, params=params, timeout=10)

            if r.status_code == 429:
                warn(f"Rate limit price {symbol} @ {base}")
                time.sleep(1.0)
                continue

            r.raise_for_status()
            return float(r.json()["price"])

        except Exception as e:
            warn(f"Price error {symbol} @ {base}: {e}")
            time.sleep(1.0)

    return None
