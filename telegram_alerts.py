"""
telegram_alerts.py
==================
Handles all Telegram message formatting and delivery.
"""

import requests
import logging
from datetime import datetime
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)


def _send_message(text: str) -> bool:
    """Sends a message to the configured Telegram chat."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code != 200:
            logger.error(f"Telegram API returned {resp.status_code}: {resp.text}")
            return False
        return True
    except Exception as e:
        logger.error(f"Telegram send failed: {e}")
        return False


def send_startup_alert(coins: list, daily_risk_usd: float) -> None:
    """Sends a ping immediately upon bot startup to verify Telegram delivery."""
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [
        "🟢 <b>ORB BOT ONLINE</b>",
        f"<i>{now_str}</i>",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"  <b>Coins:</b> {', '.join(coins)}",
        f"  <b>Daily Risk Budget:</b> ${daily_risk_usd:.2f} USD",
        "  <b>Status:</b> Listening for Asia Close (08:00 UTC)",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "✅ System connected and monitoring feeds.",
    ]
    _send_message("\n".join(lines))
    logger.info("Sent bot startup alert to Telegram.")


def send_asia_session_alert(asia_ranges: dict) -> None:
    """
    Sends ONE combined alert after the Asia session closes (08:00 UTC).
    Shows the High, Low, Midpoint and range width for all 6 coins.
    """
    now_utc = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "🌏 <b>ASIA SESSION CLOSED</b>",
        f"<i>{now_utc}</i>",
        "━━━━━━━━━━━━━━━━━━━━━━",
    ]

    for coin, data in asia_ranges.items():
        if data is None:
            lines.append(f"  <b>{coin}</b>: ❌ No data")
            continue
        h, l, mid = data["high"], data["low"], data["mid"]
        width_pct = ((h - l) / mid) * 100
        lines.append(
            f"  <b>{coin}</b>: H={h:.4f}  L={l:.4f}  Mid={mid:.4f}  "
            f"({width_pct:.2f}%)"
        )

    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("⏳ Watching 8:30–9:30 ET range...")
    _send_message("\n".join(lines))
    logger.info("Sent Asia session alert.")


def send_premarket_range_alert(prem_ranges: dict, asia_ranges: dict, signals: dict) -> None:
    """
    Sends ONE combined alert after the 8:30–9:30 ET range closes.
    Shows the premarket range, midpoint, and directional bias per coin.

    signals: dict of coin -> "LONG" | "SHORT" | "NO_TRADE" | "SKIP (width)"
    """
    now_et = datetime.now().strftime("%Y-%m-%d %H:%M ET")
    lines = [
        "📊 <b>8:30–9:30 ET RANGE CLOSED</b>",
        f"<i>{now_et}</i>",
        "━━━━━━━━━━━━━━━━━━━━━━",
    ]

    active_count = sum(1 for s in signals.values() if s in ("LONG", "SHORT"))

    for coin in prem_ranges:
        p = prem_ranges.get(coin)
        a = asia_ranges.get(coin)
        sig = signals.get(coin, "NO_TRADE")

        if p is None:
            lines.append(f"  <b>{coin}</b>: ❌ No premarket data")
            continue

        h, l, mid = p["high"], p["low"], p["mid"]
        width_pct = ((h - l) / mid) * 100

        if sig == "LONG":
            emoji = "🟢"
            bias_txt = "LOOKING LONG"
        elif sig == "SHORT":
            emoji = "🔴"
            bias_txt = "LOOKING SHORT"
        elif sig.startswith("SKIP"):
            emoji = "⚪"
            bias_txt = sig
        else:
            emoji = "⚫"
            bias_txt = "NO SETUP"

        asia_mid_txt = ""
        if a:
            asia_mid_txt = f"  Asia Mid={a['mid']:.4f}"

        lines.append(
            f"\n  {emoji} <b>{coin}</b>"
            f"\n    Range: {l:.4f} – {h:.4f}  (Mid={mid:.4f}, {width_pct:.2f}%)"
            f"{chr(10) + asia_mid_txt if asia_mid_txt else ''}"
            f"\n    📌 {bias_txt}"
        )

    lines.append("\n━━━━━━━━━━━━━━━━━━━━━━")
    if active_count > 0:
        per_coin_r = 1.0 / active_count
        lines.append(
            f"⚡ <b>{active_count} coin(s) active</b> — "
            f"Sizing <b>${per_coin_r:.2f}R</b> per coin (Daily Budget: $1.00)"
        )
    else:
        lines.append("😴 No setups qualify today — No trades will be taken.")

    lines.append("⏳ Monitoring for breakout entries until 12:00 ET...")
    _send_message("\n".join(lines))
    logger.info(f"Sent premarket range alert. Active coins: {active_count}")


def send_trade_entry_alert(coin: str, direction: str, entry_price: float,
                           stop_price: float, tp1_price: float, tp2_price: float,
                           size_usd: float, stop_mode: str, tp_mode: str) -> None:
    """Sent immediately when a breakout entry order is placed."""
    direction_emoji = "🟢 LONG" if direction == "LONG" else "🔴 SHORT"
    risk_usd = abs(entry_price - stop_price) * (size_usd / abs(entry_price - stop_price))

    lines = [
        f"🚀 <b>TRADE ENTRY — {coin}</b>",
        f"<i>{datetime.now().strftime('%H:%M:%S ET')}</i>",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"  Direction : {direction_emoji}",
        f"  Entry     : <b>{entry_price:.4f}</b>",
        f"  Stop Loss : {stop_price:.4f}  ({stop_mode})",
        f"  TP1 (50%) : {tp1_price:.4f}",
        f"  TP2 (50%) : {tp2_price:.4f}  ({tp_mode})",
        f"  Risk      : <b>${size_usd:.4f}</b> (1R = ${risk_usd:.4f})",
        "━━━━━━━━━━━━━━━━━━━━━━",
    ]
    _send_message("\n".join(lines))
    logger.info(f"Sent trade entry alert: {coin} {direction} @ {entry_price}")


def send_tp1_hit_alert(coin: str, direction: str, tp1_price: float,
                       pnl_r: float, be_stop: float) -> None:
    """Sent when TP1 is hit and stop is moved to breakeven."""
    lines = [
        f"🎯 <b>TP1 HIT — {coin}</b>",
        f"<i>{datetime.now().strftime('%H:%M:%S ET')}</i>",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"  Closed 50% @ {tp1_price:.4f}",
        f"  P&L (partial): <b>+{pnl_r:.3f}R</b>",
        f"  🔒 Stop moved to BE: {be_stop:.4f}",
        f"  ⏳ Riding remaining 50% to TP2...",
        "━━━━━━━━━━━━━━━━━━━━━━",
    ]
    _send_message("\n".join(lines))
    logger.info(f"Sent TP1 hit alert: {coin} @ {tp1_price}")


def send_trade_exit_alert(coin: str, direction: str, exit_price: float,
                          exit_reason: str, total_pnl_r: float, total_pnl_usd: float) -> None:
    """Sent on final trade exit (stop hit, TP2 hit, or 12:00 ET time stop)."""
    if total_pnl_r >= 0:
        result_emoji = "✅ WIN"
        pnl_sign = "+"
    else:
        result_emoji = "❌ LOSS"
        pnl_sign = ""

    lines = [
        f"{result_emoji} <b>TRADE CLOSED — {coin}</b>",
        f"<i>{datetime.now().strftime('%H:%M:%S ET')}</i>",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"  Exit Price  : {exit_price:.4f}",
        f"  Exit Reason : {exit_reason}",
        f"  Total P&L   : <b>{pnl_sign}{total_pnl_r:.3f}R  ({pnl_sign}${total_pnl_usd:.4f})</b>",
        "━━━━━━━━━━━━━━━━━━━━━━",
    ]
    _send_message("\n".join(lines))
    logger.info(f"Sent trade exit alert: {coin} {exit_reason} {total_pnl_r:.3f}R")


def send_error_alert(message: str) -> None:
    """Sends a critical error notification."""
    lines = [
        "⚠️ <b>BOT ERROR</b>",
        f"<i>{datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}</i>",
        "━━━━━━━━━━━━━━━━━━━━━━",
        message,
    ]
    _send_message("\n".join(lines))
    logger.error(f"Sent error alert: {message}")


def send_daily_summary(pnl_by_coin: dict, total_r: float, total_usd: float) -> None:
    """Sent at end of trading session with daily P&L summary."""
    lines = [
        "📈 <b>DAILY SESSION SUMMARY</b>",
        f"<i>{datetime.now().strftime('%Y-%m-%d')}</i>",
        "━━━━━━━━━━━━━━━━━━━━━━",
    ]
    for coin, stats in pnl_by_coin.items():
        r = stats.get("r", 0.0)
        usd = stats.get("usd", 0.0)
        reason = stats.get("reason", "-")
        sign = "+" if r >= 0 else ""
        emoji = "✅" if r >= 0 else "❌" if r < 0 else "⚫"
        lines.append(f"  {emoji} <b>{coin}</b>: {sign}{r:.3f}R  ({sign}${usd:.4f})  [{reason}]")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    total_sign = "+" if total_r >= 0 else ""
    lines.append(f"  <b>TOTAL: {total_sign}{total_r:.3f}R  ({total_sign}${total_usd:.4f})</b>")
    _send_message("\n".join(lines))
    logger.info(f"Sent daily summary: {total_r:.3f}R / ${total_usd:.4f}")
