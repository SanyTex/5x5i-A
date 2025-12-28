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

# simple in-process cache to avoid spamming price endpoint
_CACHE: dict[str, tuple[float, float]] = {}  # symbol -> (ts, price)
CACHE_TTL_SECONDS = 2.0


def _force_ipv4_only():
    orig_getaddrinfo = socket.getaddrinfo

    def ipv4_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        return orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)

    socket.getaddrinfo = ipv4_getaddrinfo


_force_ipv4_only()


def get_price(symbol: str) -> float | None:
    now = time.time()

    # return cached price if fresh
    if symbol in _CACHE:
        ts, px = _CACHE[symbol]
        if now - ts <= CACHE_TTL_SECONDS:
            return px

    params = {"symbol": symbol}

    # try primary first; on failure try ONE fallback (not all 4 in a storm)
    tried = 0
    for base in BASE_URLS:
        tried += 1
        try:
            r = requests.get(base + PRICE_PATH, params=params, timeout=10)

            if r.status_code == 429:
                warn(f"Rate limit price {symbol} @ {base}")
                time.sleep(2.0)
                continue

            r.raise_for_status()
            px = float(r.json()["price"])
            _CACHE[symbol] = (now, px)
            return px

        except Exception as e:
            warn(f"Price error {symbol} @ {base}: {e}")
            # avoid hammering if network is flaky
            time.sleep(2.0)
            # hard-stop after 2 bases to prevent storm
            if tried >= 2:
                return None

    return None
