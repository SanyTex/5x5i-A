import requests
import pandas as pd
from src.common.log import warn

BASE_URL = "https://api.binance.com/api/v3/klines"

def fetch_klines(symbol: str, interval: str, limit: int = 200) -> pd.DataFrame | None:
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    for attempt in range(3):
        try:
            r = requests.get(BASE_URL, params=params, timeout=20)
            if r.status_code == 429:
                warn(f"Rate limit {symbol} {interval} – retry {attempt+1}/3")
                continue
            r.raise_for_status()
            data = r.json()
            if not data:
                return None

            cols = [
                "open_time","open","high","low","close","volume",
                "close_time","qav","trades","tbb","tbq","ignore"
            ]
            df = pd.DataFrame(data, columns=cols)
            df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
            df = df.astype({"open":float,"high":float,"low":float,"close":float,"volume":float})
            return df
        except Exception as e:
            warn(f"Fetch error {symbol} {interval}: {e}")
    return None
