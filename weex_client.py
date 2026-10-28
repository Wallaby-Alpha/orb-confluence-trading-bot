"""
weex_client.py
==============
Handles trade execution via the Weex Futures V3 API.

Weex V3 Futures API:
  - Base URL: https://api-contract.weex.com
  - Auth Headers: ACCESS-KEY, ACCESS-SIGN, ACCESS-PASSPHRASE, ACCESS-TIMESTAMP
  - Signature: Base64(HMAC-SHA256(timestamp + METHOD + path + [?query] + [body], secret))

Endpoints:
  - Order:            POST /capi/v3/order
  - Cancel:           DELETE /capi/v3/order
  - TP/SL Plan:       POST /capi/v3/placeTpSlOrder
  - Single Position:  GET /capi/v3/account/position/singlePosition
  - All Positions:    GET /capi/v3/account/position/allPosition
"""

import hmac
import hashlib
import base64
import time
import uuid
import json
import logging
import requests
from urllib.parse import urlencode
from config import WEEX_API_KEY, WEEX_SECRET_KEY, WEEX_PASSPHRASE, WEEX_BASE_URL

logger = logging.getLogger(__name__)


def _timestamp_ms() -> int:
    return int(time.time() * 1000)


def _generate_signature(timestamp: int, method: str, path: str, query_str: str = "", body_str: str = "") -> str:
    """
    Constructs Weex V3 pre-sign string:
    timestamp + method.upper() + path + [?query] + [body]
    Returns base64 encoded HMAC-SHA256 signature.
    """
    pre_sign = str(timestamp) + method.upper() + path
    if query_str:
        pre_sign += f"?{query_str}"
    if body_str:
        pre_sign += body_str

    sig_bytes = hmac.new(
        WEEX_SECRET_KEY.encode("utf-8"),
        pre_sign.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return base64.b64encode(sig_bytes).decode("utf-8")


def _get_headers(timestamp: int, method: str, path: str, query_str: str = "", body_str: str = "") -> dict:
    signature = _generate_signature(timestamp, method, path, query_str, body_str)
    return {
        "ACCESS-KEY": WEEX_API_KEY,
        "ACCESS-SIGN": signature,
        "ACCESS-PASSPHRASE": WEEX_PASSPHRASE,
        "ACCESS-TIMESTAMP": str(timestamp),
        "Content-Type": "application/json",
    }


def _post(endpoint: str, payload: dict) -> dict | None:
    """Signed POST request to Weex V3 API."""
    timestamp = _timestamp_ms()
    body_str = json.dumps(payload, separators=(',', ':'))
    headers = _get_headers(timestamp, "POST", endpoint, "", body_str)
    url = f"{WEEX_BASE_URL}{endpoint}"

    try:
        resp = requests.post(url, data=body_str, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") not in (0, "0", 200, "200"):
            logger.error(f"Weex POST error [{endpoint}]: {data}")
            return None
        return data.get("data", data)
    except Exception as e:
        logger.error(f"Weex POST {endpoint} failed: {e}")
        return None


def _get(endpoint: str, params: dict = None) -> dict | None:
    """Signed GET request to Weex V3 API."""
    params = params or {}
    query_str = urlencode(sorted(params.items())) if params else ""
    timestamp = _timestamp_ms()
    headers = _get_headers(timestamp, "GET", endpoint, query_str, "")
    url = f"{WEEX_BASE_URL}{endpoint}"
    if query_str:
        url = f"{url}?{query_str}"

    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") not in (0, "0", 200, "200"):
            logger.error(f"Weex GET error [{endpoint}]: {data}")
            return None
        return data.get("data", data)
    except Exception as e:
        logger.error(f"Weex GET {endpoint} failed: {e}")
        return None


def _delete(endpoint: str, params: dict = None) -> dict | None:
    """Signed DELETE request to Weex V3 API."""
    params = params or {}
    query_str = urlencode(sorted(params.items())) if params else ""
    timestamp = _timestamp_ms()
    headers = _get_headers(timestamp, "DELETE", endpoint, query_str, "")
    url = f"{WEEX_BASE_URL}{endpoint}"
    if query_str:
        url = f"{url}?{query_str}"

    try:
        resp = requests.delete(url, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") not in (0, "0", 200, "200"):
            logger.error(f"Weex DELETE error [{endpoint}]: {data}")
            return None
        return data.get("data", data)
    except Exception as e:
        logger.error(f"Weex DELETE {endpoint} failed: {e}")
        return None


def _new_client_order_id() -> str:
    """Generates a unique clientOrderId matching ^[.A-Z:/a-z0-9_-]{1,36}$."""
    return f"orb_{uuid.uuid4().hex[:16]}"


def _qty_from_usd_risk(symbol: str, entry_price: float, stop_price: float,
                        risk_usd: float) -> float:
    """
    Calculates the contract quantity such that:
      qty * |entry_price - stop_price| = risk_usd (USD)
    """
    stop_dist = abs(entry_price - stop_price)
    if stop_dist <= 0:
        logger.error(f"Stop distance is zero for {symbol}. Cannot calculate qty.")
        return 0.0
    qty = risk_usd / stop_dist
    return round(qty, 4)


def place_market_order(symbol: str, side: str, qty: float) -> str | None:
    """
    Places a market order on Weex V3.
    symbol: e.g. "BTCUSDT"
    side: "BUY" or "SELL"
    qty: contract quantity
    """
    client_id = _new_client_order_id()
    payload = {
        "symbol":           symbol,
        "side":             side,
        "positionSide":     "LONG" if side == "BUY" else "SHORT",
        "type":             "MARKET",
        "quantity":         str(qty),
        "newClientOrderId": client_id,
    }
    result = _post("/capi/v3/order", payload)
    if result:
        order_id = result.get("orderId") or client_id
        logger.info(f"Market order placed: {symbol} {side} qty={qty} id={order_id}")
        return str(order_id)
    return None


def place_limit_order(symbol: str, side: str, price: float, qty: float) -> str | None:
    """Places a limit order on Weex V3. Returns orderId or None."""
    client_id = _new_client_order_id()
    payload = {
        "symbol":           symbol,
        "side":             side,
        "positionSide":     "LONG" if side == "BUY" else "SHORT",
        "type":             "LIMIT",
        "price":            str(round(price, 4)),
        "quantity":         str(qty),
        "timeInForce":      "GTC",
        "newClientOrderId": client_id,
    }
    result = _post("/capi/v3/order", payload)
    if result:
        order_id = result.get("orderId") or client_id
        logger.info(f"Limit order placed: {symbol} {side} qty={qty} @ {price} id={order_id}")
        return str(order_id)
    return None


def place_stop_loss_order(symbol: str, side: str, stop_price: float, qty: float) -> str | None:
    """
    Places a standalone Stop-Loss plan order via /capi/v3/placeTpSlOrder.
    side: the closing side ("SELL" for long, "BUY" for short).
    """
    pos_side = "LONG" if side == "SELL" else "SHORT"
    algo_id = _new_client_order_id()
    payload = {
        "symbol":           symbol,
        "clientAlgoId":     algo_id,
        "planType":         "STOP_LOSS",
        "triggerPrice":     str(round(stop_price, 4)),
        "executePrice":     "0",    # 0 = market execution on trigger
        "quantity":         str(qty),
        "positionSide":     pos_side,
        "triggerPriceType": "CONTRACT_PRICE",
        "reduceOnly":       True,
    }
    result = _post("/capi/v3/placeTpSlOrder", payload)
    if result:
        logger.info(f"Stop-loss placed: {symbol} {pos_side} trigger={stop_price} id={algo_id}")
        return str(algo_id)
    return None


def place_take_profit_order(symbol: str, side: str, tp_price: float, qty: float) -> str | None:
    """
    Places a standalone Take-Profit plan order via /capi/v3/placeTpSlOrder.
    side: the closing side ("SELL" for long, "BUY" for short).
    """
    pos_side = "LONG" if side == "SELL" else "SHORT"
    algo_id = _new_client_order_id()
    payload = {
        "symbol":           symbol,
        "clientAlgoId":     algo_id,
        "planType":         "TAKE_PROFIT",
        "triggerPrice":     str(round(tp_price, 4)),
        "executePrice":     str(round(tp_price, 4)),  # limit order execution at TP price
        "quantity":         str(qty),
        "positionSide":     pos_side,
        "triggerPriceType": "CONTRACT_PRICE",
        "reduceOnly":       True,
    }
    result = _post("/capi/v3/placeTpSlOrder", payload)
    if result:
        logger.info(f"Take-profit placed: {symbol} {pos_side} price={tp_price} id={algo_id}")
        return str(algo_id)
    return None


def cancel_order(symbol: str, order_id: str) -> bool:
    """Cancels an open order by orderId or clientOrderId."""
    params = {"symbol": symbol}
    if order_id.isdigit():
        params["orderId"] = order_id
    else:
        params["origClientOrderId"] = order_id

    result = _delete("/capi/v3/order", params)
    if result is not None:
        logger.info(f"Order cancelled: {symbol} {order_id}")
        return True
    return False


def get_position(symbol: str) -> dict | None:
    """
    Returns the current open position for a symbol.
    Queries /capi/v3/account/position/singlePosition.
    """
    result = _get("/capi/v3/account/position/singlePosition", {"symbol": symbol})
    if not result:
        return None
    positions = result if isinstance(result, list) else [result]
    for pos in positions:
        pos_amt = float(pos.get("positionAmt", pos.get("total", 0)))
        if pos_amt != 0:
            return pos
    return None


def close_position_market(symbol: str) -> bool:
    """Closes current open position at market price."""
    pos = get_position(symbol)
    if not pos:
        logger.info(f"No open position to close for {symbol}")
        return True

    pos_amt = float(pos.get("positionAmt", pos.get("total", 0)))
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
    Gets current price and calculates contract quantity needed
    so that risk equals exactly risk_usd.
    """
    from market_data import get_current_price
    coin_key = symbol.replace("USDT", "").replace("USD", "")
    price = get_current_price(coin_key)
    if price is None:
        logger.error(f"Cannot get current price for {symbol}")
        return None
    qty = _qty_from_usd_risk(symbol, price, stop_price, risk_usd)
    if qty <= 0:
        return None
    return price, qty
