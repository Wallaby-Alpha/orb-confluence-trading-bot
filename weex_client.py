"""
weex_client.py
==============
Handles all trade execution via the Weex futures API.

Weex API docs: https://weex.gitbook.io/weex-futures-api-en
Authentication: HMAC-SHA256 signature.

This module provides:
  - place_market_order(symbol, side, qty_usd)  -> order_id or None
  - place_limit_order(symbol, side, price, qty) -> order_id or None
  - cancel_order(symbol, order_id) -> bool
  - get_position(symbol) -> dict or None
  - close_position(symbol) -> bool
"""

import hmac
import hashlib
import time
import logging
import requests
from urllib.parse import urlencode
from config import WEEX_API_KEY, WEEX_SECRET_KEY, WEEX_BASE_URL

logger = logging.getLogger(__name__)


def _sign(params: dict) -> str:
    """Creates HMAC-SHA256 signature from sorted query string."""
    query_string = urlencode(sorted(params.items()))
    signature = hmac.new(
        WEEX_SECRET_KEY.encode("utf-8"),
        query_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return signature


def _timestamp_ms() -> int:
    return int(time.time() * 1000)


def _post(endpoint: str, params: dict) -> dict | None:
    """Signed POST request to Weex API."""
    params["timestamp"] = _timestamp_ms()
    params["signature"] = _sign(params)

    url = f"{WEEX_BASE_URL}{endpoint}"
    headers = {
        "X-BX-APIKEY": WEEX_API_KEY,
        "Content-Type": "application/json",
    }
    try:
        resp = requests.post(url, json=params, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") not in (0, "0", "200", 200):
            logger.error(f"Weex API error [{endpoint}]: {data}")
            return None
        return data.get("data", data)
    except Exception as e:
        logger.error(f"Weex POST {endpoint} failed: {e}")
        return None


def _get(endpoint: str, params: dict = None) -> dict | None:
    """Signed GET request to Weex API."""
    params = params or {}
    params["timestamp"] = _timestamp_ms()
    params["signature"] = _sign(params)

    url = f"{WEEX_BASE_URL}{endpoint}"
    headers = {"X-BX-APIKEY": WEEX_API_KEY}
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") not in (0, "0", "200", 200):
            logger.error(f"Weex API error [{endpoint}]: {data}")
            return None
        return data.get("data", data)
    except Exception as e:
        logger.error(f"Weex GET {endpoint} failed: {e}")
        return None


def get_symbol_info(symbol: str) -> dict | None:
    """
    Fetches contract info for a symbol to get stepSize, tickSize, etc.
    Needed for proper order quantity rounding.
    """
    result = _get("/openApi/swap/v2/quote/contracts")
    if result is None:
        return None
    contracts = result if isinstance(result, list) else result.get("contracts", [])
    for c in contracts:
        if c.get("symbol") == symbol:
            return c
    return None


def _qty_from_usd_risk(symbol: str, entry_price: float, stop_price: float,
                        risk_usd: float) -> float:
    """
    Calculates the contract quantity such that the distance from entry to stop
    equals exactly risk_usd in USD.

    qty = risk_usd / |entry_price - stop_price|

    NOTE: Weex uses USDT-denominated perpetuals where 1 contract = 1 unit of base.
    """
    stop_dist = abs(entry_price - stop_price)
    if stop_dist <= 0:
        logger.error(f"Stop distance is zero for {symbol}. Cannot calculate qty.")
        return 0.0
    qty = risk_usd / stop_dist
    # Round to a reasonable number of decimal places (adjust per contract spec)
    return round(qty, 6)


def place_market_order(symbol: str, side: str, qty: float) -> str | None:
    """
    Places a market order on Weex.

    symbol: e.g. "BTCUSDT"
    side: "BUY" or "SELL"
    qty: contract quantity (not USD notional)

    Returns the orderId string if successful, else None.
    """
    params = {
        "symbol":    symbol,
        "side":      side,
        "positionSide": "LONG" if side == "BUY" else "SHORT",
        "type":      "MARKET",
        "quantity":  str(qty),
    }
    result = _post("/openApi/swap/v2/trade/order", params)
    if result:
        order_id = result.get("orderId") or result.get("order_id")
        logger.info(f"Market order placed: {symbol} {side} qty={qty} id={order_id}")
        return str(order_id) if order_id else None
    return None


def place_limit_order(symbol: str, side: str, price: float, qty: float) -> str | None:
    """Places a limit order. Returns orderId or None."""
    params = {
        "symbol":      symbol,
        "side":        side,
        "positionSide": "LONG" if side == "BUY" else "SHORT",
        "type":        "LIMIT",
        "price":       str(round(price, 6)),
        "quantity":    str(qty),
        "timeInForce": "GTC",
    }
    result = _post("/openApi/swap/v2/trade/order", params)
    if result:
        order_id = result.get("orderId") or result.get("order_id")
        logger.info(f"Limit order placed: {symbol} {side} qty={qty} @ {price} id={order_id}")
        return str(order_id) if order_id else None
    return None


def place_stop_loss_order(symbol: str, side: str, stop_price: float, qty: float) -> str | None:
    """
    Places a stop-market order (stop loss).
    side: the CLOSING side — "SELL" for long, "BUY" for short.
    """
    params = {
        "symbol":      symbol,
        "side":        side,
        "positionSide": "LONG" if side == "SELL" else "SHORT",
        "type":        "STOP_MARKET",
        "stopPrice":   str(round(stop_price, 6)),
        "quantity":    str(qty),
        "closePosition": "true",
    }
    result = _post("/openApi/swap/v2/trade/order", params)
    if result:
        order_id = result.get("orderId") or result.get("order_id")
        logger.info(f"Stop-loss placed: {symbol} {side} stopPrice={stop_price} id={order_id}")
        return str(order_id) if order_id else None
    return None


def place_take_profit_order(symbol: str, side: str, tp_price: float, qty: float) -> str | None:
    """
    Places a take-profit limit order.
    side: the CLOSING side.
    """
    params = {
        "symbol":      symbol,
        "side":        side,
        "positionSide": "LONG" if side == "SELL" else "SHORT",
        "type":        "TAKE_PROFIT",
        "stopPrice":   str(round(tp_price, 6)),
        "price":       str(round(tp_price, 6)),
        "quantity":    str(qty),
        "timeInForce": "GTC",
    }
    result = _post("/openApi/swap/v2/trade/order", params)
    if result:
        order_id = result.get("orderId") or result.get("order_id")
        logger.info(f"Take-profit placed: {symbol} {side} price={tp_price} id={order_id}")
        return str(order_id) if order_id else None
    return None


def cancel_order(symbol: str, order_id: str) -> bool:
    """Cancels an open order by orderId."""
    params = {"symbol": symbol, "orderId": order_id}
    result = _post("/openApi/swap/v2/trade/cancel", params)
    if result is not None:
        logger.info(f"Order cancelled: {symbol} {order_id}")
        return True
    return False


def cancel_all_orders(symbol: str) -> bool:
    """Cancels ALL open orders for a symbol."""
    params = {"symbol": symbol}
    result = _post("/openApi/swap/v2/trade/cancelAllOpenOrders", params)
    return result is not None


def get_position(symbol: str) -> dict | None:
    """
    Returns the current open position for a symbol.
    Returns None if no position or on error.
    """
    result = _get("/openApi/swap/v2/user/positions", {"symbol": symbol})
    if not result:
        return None
    positions = result if isinstance(result, list) else []
    for pos in positions:
        if pos.get("symbol") == symbol and float(pos.get("positionAmt", 0)) != 0:
            return pos
    return None


def close_position_market(symbol: str) -> bool:
    """Closes the current open position at market price."""
    pos = get_position(symbol)
    if not pos:
        logger.info(f"No open position to close for {symbol}")
        return True

    pos_amt = float(pos.get("positionAmt", 0))
    pos_side = pos.get("positionSide", "LONG")

    if pos_amt == 0:
        return True

    close_side = "SELL" if pos_side == "LONG" else "BUY"
    qty = abs(pos_amt)
    order_id = place_market_order(symbol, close_side, qty)
    if order_id:
        logger.info(f"Position closed via market for {symbol}")
        return True
    return False


def get_entry_price_and_qty(symbol: str, direction: str,
                             stop_price: float, risk_usd: float) -> tuple[float, float] | None:
    """
    Gets the current price and calculates the contract quantity needed
    so that a loss to stop_price equals exactly risk_usd USD.

    Returns (current_price, qty) or None on failure.
    """
    from market_data import get_current_price
    price = get_current_price(symbol.replace("USDT", "").replace("USD", ""))
    if price is None:
        logger.error(f"Cannot get current price for {symbol}")
        return None
    qty = _qty_from_usd_risk(symbol, price, stop_price, risk_usd)
    if qty <= 0:
        return None
    return price, qty
