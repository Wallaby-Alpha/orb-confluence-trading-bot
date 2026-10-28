"""
market_data.py
==============
Fetches OHLCV data from Kraken's public REST API.
No API key required for public endpoints.

Provides:
  - get_ohlcv(symbol, tf_minutes, limit) -> list of OHLCV bars
  - compute_indicators(bars) -> adds EMA50 + ATR14
  - get_current_price(symbol) -> float
"""

import time
import logging
import requests
import numpy as np
from datetime import datetime, timezone
from config import KRAKEN_BASE_URL, KRAKEN_PAIRS

logger = logging.getLogger(__name__)

# Kraken OHLCV interval map (minutes -> Kraken interval integer)
KRAKEN_INTERVAL_MAP = {1: 1, 5: 5, 15: 15, 30: 30, 60: 60, 240: 240, 1440: 1440}


def get_ohlcv(symbol: str, tf_minutes: int = 5, limit: int = 500) -> list:
    """
    Fetches OHLCV bars from Kraken for the given symbol.
    Returns a list of dicts: [{ts, open, high, low, close, volume}, ...]
    sorted oldest-first.

    symbol: one of "BTC", "ETH", "SOL", "XRP", "BNB", "HYPE"
    """
    pair = KRAKEN_PAIRS.get(symbol)
    if not pair:
        logger.error(f"No Kraken pair mapped for symbol: {symbol}")
        return []

    interval = KRAKEN_INTERVAL_MAP.get(tf_minutes, 5)
    url = f"{KRAKEN_BASE_URL}/0/public/OHLC"
    params = {"pair": pair, "interval": interval}

    for attempt in range(3):
        try:
            resp = requests.get(url, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()

            if data.get("error"):
                logger.warning(f"Kraken API error for {symbol}: {data['error']}")
                time.sleep(2)
                continue

            result = data.get("result", {})
            # Kraken returns the pair key dynamically — get first non-'last' key
            ohlcv_key = next((k for k in result if k != "last"), None)
            if not ohlcv_key:
                logger.warning(f"No OHLCV key in Kraken response for {symbol}")
                return []

            raw_bars = result[ohlcv_key]
            bars = []
            for b in raw_bars[-limit:]:
                # Kraken OHLCV: [time, open, high, low, close, vwap, volume, count]
                bars.append({
                    "ts":     int(b[0]),
                    "open":   float(b[1]),
                    "high":   float(b[2]),
                    "low":    float(b[3]),
                    "close":  float(b[4]),
                    "volume": float(b[6]),
                })
            return bars

        except requests.RequestException as e:
            logger.warning(f"Kraken OHLCV request failed (attempt {attempt+1}): {e}")
            time.sleep(2 ** attempt)

    logger.error(f"All Kraken OHLCV attempts failed for {symbol}")
    return []


def get_current_price(symbol: str) -> float | None:
    """Fetches the latest mid-price from Kraken Ticker for the given symbol."""
    pair = KRAKEN_PAIRS.get(symbol)
    if not pair:
        return None

    url = f"{KRAKEN_BASE_URL}/0/public/Ticker"
    try:
        resp = requests.get(url, params={"pair": pair}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get("error"):
            return None
        result = data.get("result", {})
        ticker_key = next((k for k in result), None)
        if not ticker_key:
            return None
        # 'c': last trade price [price, lot volume]
        return float(result[ticker_key]["c"][0])
    except Exception as e:
        logger.error(f"Kraken Ticker fetch failed for {symbol}: {e}")
        return None


def compute_indicators(bars: list, ema_period: int = 50, atr_period: int = 14) -> list:
    """
    Appends 'ema' and 'atr' fields to each bar dict.
    Bars must be a list of dicts with 'close', 'high', 'low'.
    Returns the same list with added indicator fields.
    """
    n = len(bars)
    if n == 0:
        return bars

    closes = np.array([b["close"] for b in bars], dtype=float)
    highs  = np.array([b["high"]  for b in bars], dtype=float)
    lows   = np.array([b["low"]   for b in bars], dtype=float)

    # EMA
    ema = np.zeros(n)
    k = 2.0 / (ema_period + 1)
    ema[0] = closes[0]
    for i in range(1, n):
        ema[i] = closes[i] * k + ema[i - 1] * (1 - k)

    # ATR (True Range)
    tr = np.zeros(n)
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )

    atr = np.zeros(n)
    if n >= atr_period:
        atr[atr_period - 1] = np.mean(tr[:atr_period])
        for i in range(atr_period, n):
            atr[i] = (atr[i - 1] * (atr_period - 1) + tr[i]) / atr_period
        # Backfill early bars with first valid ATR
        first_valid = atr[atr_period - 1]
        for i in range(atr_period - 1):
            atr[i] = first_valid
    else:
        # Not enough bars — use simple mean
        atr_val = np.mean(tr) if n > 0 else 0.0
        atr[:] = atr_val

    for i, bar in enumerate(bars):
        bar["ema"] = float(ema[i])
        bar["atr"] = float(atr[i])

    return bars


def bars_in_utc_window(bars: list, start_hhmm: str, end_hhmm: str, target_date=None) -> list:
    """
    Filters bars to those whose timestamps fall within the given UTC time window.
    Window times are "HH:MM" strings (UTC).
    target_date: datetime.date (default: current UTC date).
    """
    if target_date is None:
        target_date = datetime.now(tz=timezone.utc).date()

    start_h, start_m = map(int, start_hhmm.split(":"))
    end_h,   end_m   = map(int, end_hhmm.split(":"))

    result = []
    for b in bars:
        dt = datetime.fromtimestamp(b["ts"], tz=timezone.utc)
        if dt.date() != target_date:
            continue
        bar_minutes = dt.hour * 60 + dt.minute
        start_minutes = start_h * 60 + start_m
        end_minutes   = end_h   * 60 + end_m
        if start_minutes <= bar_minutes < end_minutes:
            result.append(b)
    return result


def bars_in_et_window(bars: list, start_hhmm: str, end_hhmm: str, target_date=None) -> list:
    """
    Filters bars to those within the given ET time window (HH:MM ET strings).
    target_date: datetime.date (default: current ET date).
    """
    import pytz
    et_tz = pytz.timezone("America/New_York")
    if target_date is None:
        target_date = datetime.now(tz=et_tz).date()

    start_h, start_m = map(int, start_hhmm.split(":"))
    end_h,   end_m   = map(int, end_hhmm.split(":"))

    result = []
    for b in bars:
        dt_utc = datetime.fromtimestamp(b["ts"], tz=timezone.utc)
        dt_et  = dt_utc.astimezone(et_tz)
        if dt_et.date() != target_date:
            continue
        bar_minutes = dt_et.hour * 60 + dt_et.minute
        start_minutes = start_h * 60 + start_m
        end_minutes   = end_h   * 60 + end_m
        if start_minutes <= bar_minutes < end_minutes:
            result.append(b)
    return result


def compute_session_range(bars: list) -> dict | None:
    """
    Computes High, Low, Mid of a list of OHLCV bars.
    Returns None if no bars provided.
    """
    if not bars:
        return None
    highs  = [b["high"] for b in bars]
    lows   = [b["low"]  for b in bars]
    high   = max(highs)
    low    = min(lows)
    mid    = (high + low) / 2.0
    return {"high": high, "low": low, "mid": mid}
