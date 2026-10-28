"""
strategy_engine.py
==================
Implements the ORB confluence strategy logic for live trading.

Per-coin strategy:
  1. Compute Asia session range (00:00–08:00 UTC)
  2. Compute 8:30–9:30 ET pre-market range
  3. Check triple confluence: breakout + EMA50 trend + Asia midpoint bias + min width filter
  4. On breakout signal: calculate stop (ATR-based or opposite side) and TP levels
  5. Return TradeSignal or None

Mirrors the backtest logic from test_enhancements.py and run_user_ideas_test.py.
"""

import logging
from dataclasses import dataclass
from typing import Optional

from config import COIN_CONFIGS, ATR_PERIOD, EMA_PERIOD

logger = logging.getLogger(__name__)


@dataclass
class TradeSignal:
    symbol:     str       # e.g. "BTCUSDT"
    coin:       str       # e.g. "BTC"
    direction:  str       # "LONG" or "SHORT"
    entry_price: float    # current price at signal time
    stop_price:  float    # initial stop loss
    tp1_price:   float    # 50% partial target
    tp2_price:   float    # 50% final target
    stop_mode:   str      # description for alerts
    tp_mode:     str
    atr:         float    # ATR at signal time
    asia_mid:    float
    prem_mid:    float


def check_signal(coin: str,
                 bars: list,          # recent 5-min bars with ema + atr fields
                 asia_range: dict,    # {"high", "low", "mid"}
                 prem_range: dict,    # {"high", "low", "mid"}
                 ) -> Optional[TradeSignal]:
    """
    Checks if the current bar produces a trade signal.

    bars: list of recent bars (oldest first), each bar has keys:
          ts, open, high, low, close, volume, ema, atr

    Returns a TradeSignal if all filters pass, else None.
    """
    cfg = COIN_CONFIGS.get(coin)
    if not cfg or not bars:
        return None

    if not asia_range or not prem_range:
        return None

    # --- Range width filter ---
    min_width_pct = cfg["min_width_pct"]
    prem_h = prem_range["high"]
    prem_l = prem_range["low"]
    prem_mid = prem_range["mid"]
    prem_width_pct = (prem_h - prem_l) / prem_mid

    if prem_width_pct < min_width_pct:
        logger.debug(f"{coin}: Range width {prem_width_pct:.4%} < min {min_width_pct:.4%}. Skip.")
        return None

    # --- Current bar ---
    bar = bars[-1]
    c_close = bar["close"]
    c_ema   = bar["ema"]
    c_atr   = bar["atr"]

    asia_mid = asia_range["mid"]

    # --- Triple confluence signal ---
    # Long: close > prem high AND close > EMA50 AND prem_mid > asia_mid
    # Short: close < prem low  AND close < EMA50 AND prem_mid < asia_mid
    direction = None
    if c_close > prem_h and c_close > c_ema and prem_mid > asia_mid:
        direction = "LONG"
    elif c_close < prem_l and c_close < c_ema and prem_mid < asia_mid:
        direction = "SHORT"

    if direction is None:
        return None

    # --- Stop Loss Calculation ---
    stop_mode = cfg["stop_mode"]
    if stop_mode == "Opposite_Side":
        # Stop at opposite side of range
        stop_price = prem_l if direction == "LONG" else prem_h
    elif stop_mode == "ATR_2.5x":
        stop_dist = c_atr * 2.5
        stop_price = c_close - stop_dist if direction == "LONG" else c_close + stop_dist
    elif stop_mode == "ATR_3.0x":
        stop_dist = c_atr * 3.0
        stop_price = c_close - stop_dist if direction == "LONG" else c_close + stop_dist
    else:
        # Fallback: opposite side
        stop_price = prem_l if direction == "LONG" else prem_h

    stop_dist = abs(c_close - stop_price)
    if stop_dist <= 0:
        logger.warning(f"{coin}: Zero stop distance. Skip.")
        return None

    # --- Take Profit Calculation ---
    tp_mode = cfg["tp_mode"]
    tp2_mult = cfg["tp2_mult"]

    # TP1 is always at 1.0R
    if direction == "LONG":
        tp1_price = c_close + stop_dist          # 1R
        tp2_price = c_close + (stop_dist * tp2_mult)
    else:
        tp1_price = c_close - stop_dist          # 1R
        tp2_price = c_close - (stop_dist * tp2_mult)

    symbol = cfg["weex_symbol"]

    logger.info(
        f"{coin} SIGNAL: {direction} | Entry~{c_close:.4f} | Stop={stop_price:.4f} | "
        f"TP1={tp1_price:.4f} | TP2={tp2_price:.4f} | ATR={c_atr:.4f}"
    )

    return TradeSignal(
        symbol=symbol,
        coin=coin,
        direction=direction,
        entry_price=c_close,
        stop_price=stop_price,
        tp1_price=tp1_price,
        tp2_price=tp2_price,
        stop_mode=stop_mode,
        tp_mode=tp_mode,
        atr=c_atr,
        asia_mid=asia_mid,
        prem_mid=prem_mid,
    )


def determine_session_bias(asia_range: dict, prem_range: dict) -> str:
    """
    Returns "LONG", "SHORT", or "NEUTRAL" based on Asia midpoint vs premarket midpoint.
    Used only for the alert message (not the trade entry filter).
    """
    if not asia_range or not prem_range:
        return "NEUTRAL"
    if prem_range["mid"] > asia_range["mid"]:
        return "LONG"
    elif prem_range["mid"] < asia_range["mid"]:
        return "SHORT"
    return "NEUTRAL"
