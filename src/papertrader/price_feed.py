# src/papertrader/price_feed.py
from __future__ import annotations

import socket
import time
from typing import Optional, Dict, Tuple

import requests

from src.common.log import warn, info

BASE_URLS = [
    "https://api.binance.com",
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://api3.binance.com",
]

PRICE_PATH = "/api/v3/ticker/price"
PING_PATH = "/api/v3/time"

# Cache: symbol -> (ts_epoch, price)
_CACHE: Dict[str, Tuple[float, float]] = {}

# Return fresh cache without touching network
CACHE_TTL_SECONDS = 2.0

# If network is broken, we still allow using "stale" cache up to this age
STALE_CACHE_MAX_AGE_SECONDS = 60 * 15  # 15 min

# Circuit breaker: after hard network failure, pause outbound requests briefly
FAIL_COOLDOWN_SECONDS = 20.0
_CONSEC_FAILS = 0
_NEXT_ALLOWED_NET_TS = 0.0

# For reduced log spam: only log "network down" message every N seconds
_LAST_NET_DOWN_LOG_TS = 0.0
NET_DOWN_LOG_EVERY_SECONDS = 30.0


def _force_ipv4_only() -> None:
    """
    Binance + some networks sometimes resolve to IPv6 first.
    If your host has broken IPv6 routing, you'll see "No route to host".
    Forcing IPv4 often fixes this class of issues immediately.
    """
    orig_getaddrinfo = socket.getaddrinfo

    def ipv4_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        return orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)

    socket.getaddrinfo = ipv4_getaddrinfo


_force_ipv4_only()

# Reuse TCP connections (faster + fewer failures)
_SESSION = requests.Session()


def _now() -> float:
    return time.time()


def _cache_get(symbol: str) -> Optional[float]:
    if symbol not in _CACHE:
        return None
    ts, px = _CACHE[symbol]
    # always allow returning something from cache (freshness checked by caller)
    return px


def _cache_age(symbol: str) -> Optional[float]:
    if symbol not in _CACHE:
        return None
    ts, _ = _CACHE[symbol]
    return _now() - ts


def _cache_set(symbol: str, price: float) -> None:
    _CACHE[symbol] = (_now(), price)


def _should_skip_network() -> bool:
    return _now() < _NEXT_ALLOWED_NET_TS


def _bump_fail_cooldown(reason: str) -> None:
    """
    Progressive backoff to avoid request storms when network is down.
    """
    global _CONSEC_FAILS, _NEXT_ALLOWED_NET_TS, _LAST_NET_DOWN_LOG_TS

    _CONSEC_FAILS += 1
    # exponential-ish: 20s, 40s, 60s, capped at 120s
    cooldown = min(FAIL_COOLDOWN_SECONDS * _CONSEC_FAILS, 120.0)
    _NEXT_ALLOWED_NET_TS = _now() + cooldown

    # log only every NET_DOWN_LOG_EVERY_SECONDS
    if _now() - _LAST_NET_DOWN_LOG_TS >= NET_DOWN_LOG_EVERY_SECONDS:
        _LAST_NET_DOWN_LOG_TS = _now()
        warn(f"PRICE_FEED network degraded ({reason}). Cooldown {cooldown:.0f}s (fails={_CONSEC_FAILS}).")


def _reset_fail_state() -> None:
    global _CONSEC_FAILS
    _CONSEC_FAILS = 0


def _quick_connectivity_probe(base: str, timeout: float = 5.0) -> bool:
    """
    Optional quick check: /time is lightweight.
    If this fails with route/DNS problems, we know it's not a symbol issue.
    """
    try:
        r = _SESSION.get(base + PING_PATH, timeout=timeout)
        r.raise_for_status()
        return True
    except Exception:
        return False


def get_price(symbol: str) -> Optional[float]:
    """
    Robust price fetcher for Papertrader.

    Behavior:
    1) Return fresh cache if available (<= CACHE_TTL_SECONDS).
    2) If network is in cooldown, return stale cache if <= STALE_CACHE_MAX_AGE_SECONDS.
    3) Try up to 2 base URLs max per call (prevents storms).
    4) On hard network errors, set cooldown and return stale cache if available.
    """
    now = _now()

    # 1) Fresh cache
    if symbol in _CACHE:
        ts, px = _CACHE[symbol]
        if now - ts <= CACHE_TTL_SECONDS:
            return px

    # 2) If we are in cooldown, use stale cache (if not too old)
    if _should_skip_network():
        age = _cache_age(symbol)
        if age is not None and age <= STALE_CACHE_MAX_AGE_SECONDS:
            return _cache_get(symbol)
        return None

    params = {"symbol": symbol}

    tried = 0
    last_err: Optional[Exception] = None

    for base in BASE_URLS:
        tried += 1
        try:
            r = _SESSION.get(base + PRICE_PATH, params=params, timeout=10)

            if r.status_code == 429:
                warn(f"Rate limit price {symbol} @ {base} (429). Cooling down.")
                _bump_fail_cooldown("rate_limit_429")
                # try next base only if we have attempts left
                if tried >= 2:
                    break
                continue

            r.raise_for_status()
            px = float(r.json()["price"])
            _cache_set(symbol, px)
            _reset_fail_state()
            return px

        except Exception as e:
            last_err = e
            warn(f"Price error {symbol} @ {base}: {e}")

            # If it looks like "network down", don't keep hammering:
            # - quick probe once to classify
            # - apply cooldown
            # - return stale cache if possible
            if not _quick_connectivity_probe(base):
                _bump_fail_cooldown("no_route_dns_or_block")
                age = _cache_age(symbol)
                if age is not None and age <= STALE_CACHE_MAX_AGE_SECONDS:
                    return _cache_get(symbol)
                return None

            # mild failure (endpoint hiccup) -> short sleep then maybe try 2nd base
            time.sleep(1.5)

            if tried >= 2:
                break

    # After attempts exhausted, return stale cache if possible
    age = _cache_age(symbol)
    if age is not None and age <= STALE_CACHE_MAX_AGE_SECONDS:
        return _cache_get(symbol)

    # nothing usable
    if last_err is not None:
        warn(f"PRICE_FEED failed for {symbol} after {tried} tries. last_err={last_err}")
    return None
