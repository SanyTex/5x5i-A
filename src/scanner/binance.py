import socket
import requests
import pandas as pd
import time
from src.common.log import warn

BASE_URLS = [
    "https://api.binance.com",
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://api3.binance.com",
]
KLINES_PATH = "/api/v3/klines"


# Force IPv4 (fixes sporadic 'No route to host' on Raspberry Pi)
def _force_ipv4_only():
    orig_getaddrinfo = socket.getaddrinfo

    def ipv4_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        return orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)

    socket.getaddrinfo = ipv4_getaddrinfo


_force_ipv4_only()


def fetch_klines(symbol: str, interval: str, limit: int = 200) -> pd.DataFrame | None:
    params = {"symbol": symbol, "interval": interval, "limit": limit}

    for base in BASE_URLS:
        try:
            r = requests.get(base + KLINES_PATH, params=params, timeout=20)

            if r.status_code == 429:
                warn(f"Rate limit {symbol} {interval} @ {base}")
                continue

            r.raise_for_status()
            data = r.json()
            if not data:
                return None

            cols = [
                "open_time",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "close_time",
                "qav",
                "trades",
                "tbb",
                "tbq",
                "ignore",
            ]
            df = pd.DataFrame(data, columns=cols)
            df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
            df = df.astype(
                {
                    "open": float,
                    "high": float,
                    "low": float,
                    "close": float,
                    "volume": float,
                }
            )
            return df

        except Exception as e:
            warn(f"Fetch error {symbol} {interval} @ {base}: {e}")
            time.sleep(1.5)

    return None
