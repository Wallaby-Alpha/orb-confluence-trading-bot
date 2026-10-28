"""
trade_manager.py
================
Manages the lifecycle of a single active trade for one coin:
  - Entry (market order + stop loss + TP1 limit + TP2 limit)
  - TP1 hit → cancel old stop → place BE stop → send alert
  - TP2 hit or Stop hit or 12:00 ET time stop → close + send alert
  - Tracks P&L and state

This runs per-coin in the main bot loop.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Optional
from config import COIN_CONFIGS, DAILY_RISK_BUDGET_USD
from strategy_engine import TradeSignal
from weex_client import (
    place_market_order, place_stop_loss_order, place_take_profit_order,
    cancel_order, cancel_all_orders, close_position_market
)
from telegram_alerts import (
    send_trade_entry_alert, send_tp1_hit_alert, send_trade_exit_alert
)

logger = logging.getLogger(__name__)


@dataclass
class TradeState:
    coin:             str
    symbol:           str
    direction:        str      # "LONG" or "SHORT"
    entry_price:      float
    stop_price:       float
    current_stop:     float    # Updated when BE is set
    tp1_price:        float
    tp2_price:        float
    qty_full:         float    # Full contract quantity
    qty_half:         float    # Half qty for partial close
    risk_usd:         float    # USD at risk for this trade
    stop_mode:        str
    tp_mode:          str

    # Order IDs (for management)
    entry_order_id:   Optional[str] = None
    sl_order_id:      Optional[str] = None
    tp1_order_id:     Optional[str] = None
    tp2_order_id:     Optional[str] = None

    # State flags
    tp1_hit:          bool = False
    be_stop_set:      bool = False
    is_closed:        bool = False
    exit_reason:      str = ""

    # P&L tracking
    pnl_r:            float = 0.0
    pnl_usd:          float = 0.0

    # Accumulated partial P&L
    partial_pnl_r:    float = 0.0
    partial_pnl_usd:  float = 0.0


class TradeManager:
    """Manages a single coin's trade from entry to exit."""

    def __init__(self, signal: TradeSignal, risk_usd: float):
        self.signal = signal
        self.risk_usd = risk_usd
        self.state: Optional[TradeState] = None

    def enter_trade(self) -> bool:
        """
        Executes the trade entry:
          1. Calculate quantity from risk_usd and stop distance
          2. Market buy/sell
          3. Place stop loss order
          4. Place TP1 limit order (half qty)
          5. Place TP2 limit order (half qty)
          6. Send Telegram entry alert
        """
        sig = self.signal
        risk = self.risk_usd
        stop_dist = abs(sig.entry_price - sig.stop_price)

        if stop_dist <= 0:
            logger.error(f"{sig.coin}: Zero stop distance. Cannot enter.")
            return False

        # Quantity = risk_usd / stop_dist_per_unit
        qty_full = round(risk / stop_dist, 6)
        qty_half = round(qty_full / 2.0, 6)

        if qty_full <= 0:
            logger.error(f"{sig.coin}: Zero quantity calculated. Cannot enter.")
            return False

        # Determine order sides
        entry_side = "BUY" if sig.direction == "LONG" else "SELL"
        close_side = "SELL" if sig.direction == "LONG" else "BUY"

        logger.info(
            f"Entering {sig.coin} {sig.direction}: qty={qty_full} @ ~{sig.entry_price:.4f} "
            f"SL={sig.stop_price:.4f} TP1={sig.tp1_price:.4f} TP2={sig.tp2_price:.4f}"
        )

        # 1. Market entry
        entry_oid = place_market_order(sig.symbol, entry_side, qty_full)
        if not entry_oid:
            logger.error(f"{sig.coin}: Market order failed. Aborting entry.")
            return False

        time.sleep(0.5)   # Brief pause for order to register

        # 2. Stop loss (full qty)
        sl_oid = place_stop_loss_order(sig.symbol, close_side, sig.stop_price, qty_full)

        # 3. TP1 (half qty) — limit order at TP1
        tp1_oid = place_take_profit_order(sig.symbol, close_side, sig.tp1_price, qty_half)

        # 4. TP2 (half qty)
        tp2_oid = place_take_profit_order(sig.symbol, close_side, sig.tp2_price, qty_half)

        # Create state
        self.state = TradeState(
            coin=sig.coin,
            symbol=sig.symbol,
            direction=sig.direction,
            entry_price=sig.entry_price,
            stop_price=sig.stop_price,
            current_stop=sig.stop_price,
            tp1_price=sig.tp1_price,
            tp2_price=sig.tp2_price,
            qty_full=qty_full,
            qty_half=qty_half,
            risk_usd=risk,
            stop_mode=sig.stop_mode,
            tp_mode=sig.tp_mode,
            entry_order_id=entry_oid,
            sl_order_id=sl_oid,
            tp1_order_id=tp1_oid,
            tp2_order_id=tp2_oid,
        )

        # Send entry alert
        send_trade_entry_alert(
            coin=sig.coin,
            direction=sig.direction,
            entry_price=sig.entry_price,
            stop_price=sig.stop_price,
            tp1_price=sig.tp1_price,
            tp2_price=sig.tp2_price,
            size_usd=risk,
            stop_mode=sig.stop_mode,
            tp_mode=sig.tp_mode,
        )

        logger.info(f"{sig.coin}: Trade entered. SL_id={sl_oid} TP1_id={tp1_oid} TP2_id={tp2_oid}")
        return True

    def check_and_handle_tp1(self, current_price: float) -> None:
        """
        Call this in the polling loop.
        If TP1 price is reached and we haven't yet handled it,
        cancel old SL, place BE stop, send TP1 alert.
        """
        if not self.state or self.state.tp1_hit or self.state.is_closed:
            return

        st = self.state
        tp1_hit = (
            (st.direction == "LONG"  and current_price >= st.tp1_price) or
            (st.direction == "SHORT" and current_price <= st.tp1_price)
        )

        if not tp1_hit:
            return

        logger.info(f"{st.coin}: TP1 hit @ {current_price:.4f}. Moving stop to BE.")
        st.tp1_hit = True

        # Partial P&L: half position closed at TP1
        tp1_r = (abs(st.tp1_price - st.entry_price) / abs(st.entry_price - st.stop_price)) * 0.5
        tp1_usd = tp1_r * st.risk_usd
        st.partial_pnl_r   = tp1_r
        st.partial_pnl_usd = tp1_usd

        # Cancel old SL
        if st.sl_order_id:
            cancel_order(st.symbol, st.sl_order_id)

        # Place BE stop on remaining half
        close_side = "SELL" if st.direction == "LONG" else "BUY"
        be_stop = st.entry_price
        be_sl_oid = place_stop_loss_order(st.symbol, close_side, be_stop, st.qty_half)
        st.sl_order_id = be_sl_oid
        st.current_stop = be_stop
        st.be_stop_set = True

        send_tp1_hit_alert(
            coin=st.coin,
            direction=st.direction,
            tp1_price=st.tp1_price,
            pnl_r=tp1_r,
            be_stop=be_stop,
        )

    def close_trade(self, reason: str, exit_price: float) -> None:
        """
        Forces the trade closed at market:
          - Cancel all open orders
          - Market close remaining position
          - Calculate and record final P&L
          - Send exit alert
        """
        if not self.state or self.state.is_closed:
            return

        st = self.state
        logger.info(f"{st.coin}: Closing trade. Reason={reason} Price={exit_price:.4f}")

        # Cancel remaining open orders
        cancel_all_orders(st.symbol)

        # Close any remaining position
        close_position_market(st.symbol)

        # Calculate P&L for remaining position
        remaining_fraction = 0.5 if st.tp1_hit else 1.0
        price_diff = (exit_price - st.entry_price) if st.direction == "LONG" else (st.entry_price - exit_price)
        stop_dist = abs(st.entry_price - st.stop_price)
        remaining_r = (price_diff / stop_dist) * remaining_fraction if stop_dist > 0 else 0.0
        remaining_usd = remaining_r * st.risk_usd

        # Total P&L = partial (from TP1) + remaining
        st.pnl_r   = st.partial_pnl_r + remaining_r
        st.pnl_usd = st.partial_pnl_usd + remaining_usd
        st.exit_reason = reason
        st.is_closed = True

        send_trade_exit_alert(
            coin=st.coin,
            direction=st.direction,
            exit_price=exit_price,
            exit_reason=reason,
            total_pnl_r=st.pnl_r,
            total_pnl_usd=st.pnl_usd,
        )

    def is_done(self) -> bool:
        return self.state is not None and self.state.is_closed

    def get_pnl(self) -> dict:
        if not self.state:
            return {"r": 0.0, "usd": 0.0, "reason": "No trade"}
        return {
            "r": self.state.pnl_r,
            "usd": self.state.pnl_usd,
            "reason": self.state.exit_reason,
        }
