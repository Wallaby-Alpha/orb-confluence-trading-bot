"""
main.py
=======
ORB Confluence Trading Bot — Main Event Loop

Runs 24/7 on your Digital Ocean droplet.

Daily cycle:
  1. 00:00 UTC → begin monitoring Asia session (High/Low tracking)
  2. 08:00 UTC → Asia session closes → send Telegram alert with range/mid
  3. ~13:30 UTC (8:30 ET, DST-aware) → begin tracking premarket range
  4. ~14:30 UTC (9:30 ET) → premarket range closes → compute signals → send Telegram alert
  5. 09:30–12:00 ET → poll every 30s for breakout signals → enter trades → manage positions
  6. 12:00 ET → force-close all open positions → send daily summary → reset state
  7. Repeat the next day

Usage (on Digital Ocean droplet):
  python main.py

To run as a background daemon:
  nohup python main.py >> bot_stdout.log 2>&1 &

To run via systemd (recommended for auto-restart):
  See orb_bot.service in this directory.
"""

import time
import logging
import pytz
from datetime import datetime, timedelta, timezone
from typing import Optional

from config import (
    COIN_CONFIGS, DAILY_RISK_BUDGET_USD, POLL_INTERVAL_SECONDS,
    CANDLE_TF_MINUTES, LOG_FILE, LOG_LEVEL, EMA_PERIOD, ATR_PERIOD,
    validate_config,
)
from market_data import (
    get_ohlcv, compute_indicators, compute_session_range,
    bars_in_utc_window, bars_in_et_window, get_current_price
)
from strategy_engine import check_signal, determine_session_bias, TradeSignal
from trade_manager import TradeManager
from telegram_alerts import (
    send_startup_alert, send_asia_session_alert,
    send_premarket_range_alert, send_error_alert, send_daily_summary
)

# ===========================================================================
# LOGGING SETUP
# ===========================================================================
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("main")

ET_TZ = pytz.timezone("America/New_York")
UTC_TZ = timezone.utc

COINS = list(COIN_CONFIGS.keys())


# ===========================================================================
# STATE MACHINE
# ===========================================================================
class BotState:
    """Resets each trading day."""

    def __init__(self):
        self.asia_ranges = {c: None for c in COINS}
        self.prem_ranges = {c: None for c in COINS}
        self.signals     = {c: "NO_TRADE" for c in COINS}

        # Per-coin TradeManager instances
        self.managers: dict[str, Optional[TradeManager]] = {c: None for c in COINS}

        # Flags
        self.asia_alert_sent  = False
        self.prem_alert_sent  = False
        self.session_reset_done = False
        self.daily_summary_sent = False
        self.trade_window_active = False

    def reset(self):
        self.__init__()
        logger.info("=== BOT STATE RESET FOR NEW DAY ===")


# ===========================================================================
# HELPERS
# ===========================================================================
def now_utc() -> datetime:
    return datetime.now(tz=UTC_TZ)


def now_et() -> datetime:
    return datetime.now(tz=ET_TZ)


def et_hhmm() -> str:
    dt = now_et()
    return f"{dt.hour:02d}:{dt.minute:02d}"


def utc_hhmm() -> str:
    dt = now_utc()
    return f"{dt.hour:02d}:{dt.minute:02d}"


def is_after_utc(hhmm: str) -> bool:
    h, m = map(int, hhmm.split(":"))
    now = now_utc()
    return (now.hour, now.minute) >= (h, m)


def is_after_et(hhmm: str) -> bool:
    h, m = map(int, hhmm.split(":"))
    now = now_et()
    return (now.hour, now.minute) >= (h, m)


def premarket_start_et() -> str:
    return "08:30"


def premarket_end_et() -> str:
    return "09:30"


def trade_window_end_et() -> str:
    return "12:00"


def fetch_all_data() -> dict:
    """Fetches recent 5-min OHLCV bars + indicators for all coins."""
    data = {}
    for coin in COINS:
        bars = get_ohlcv(coin, tf_minutes=CANDLE_TF_MINUTES, limit=500)
        if bars:
            bars = compute_indicators(bars, ema_period=EMA_PERIOD, atr_period=ATR_PERIOD)
        data[coin] = bars
    return data


def compute_asia_ranges(all_bars: dict) -> dict:
    """Computes Asia session ranges from bar data."""
    asia_ranges = {}
    for coin, bars in all_bars.items():
        asia_bars = bars_in_utc_window(bars, "00:00", "08:00")
        asia_ranges[coin] = compute_session_range(asia_bars)
    return asia_ranges


def compute_prem_ranges(all_bars: dict) -> dict:
    """Computes 8:30–9:30 ET premarket ranges."""
    prem_ranges = {}
    for coin, bars in all_bars.items():
        prem_bars = bars_in_et_window(bars, "08:30", "09:30")
        prem_ranges[coin] = compute_session_range(prem_bars)
    return prem_ranges


def evaluate_signals(prem_ranges: dict, asia_ranges: dict, all_bars: dict) -> dict:
    """
    Evaluates pre-9:30 state to determine which coins have a directional bias.
    Returns signals dict: coin -> "LONG" | "SHORT" | "SKIP (width)" | "NO_TRADE"
    """
    signals = {}
    for coin in COINS:
        cfg = COIN_CONFIGS[coin]
        p = prem_ranges.get(coin)
        a = asia_ranges.get(coin)

        if not p or not a:
            signals[coin] = "NO_TRADE"
            continue

        # Width filter
        width_pct = (p["high"] - p["low"]) / p["mid"]
        if width_pct < cfg["min_width_pct"]:
            signals[coin] = f"SKIP (range {width_pct:.2%} < min {cfg['min_width_pct']:.2%})"
            continue

        bias = determine_session_bias(a, p)
        if bias == "LONG":
            signals[coin] = "LONG"
        elif bias == "SHORT":
            signals[coin] = "SHORT"
        else:
            signals[coin] = "NO_TRADE"

    return signals


def count_active_coins(signals: dict) -> int:
    return sum(1 for s in signals.values() if s in ("LONG", "SHORT"))


# ===========================================================================
# MAIN LOOP
# ===========================================================================
def run_bot():
    # Validate all env vars are set before doing anything
    validate_config()

    logger.info("=" * 60)
    logger.info("  ORB Confluence Bot Starting Up")
    logger.info(f"  Coins: {COINS}")
    logger.info(f"  Daily Risk Budget: ${DAILY_RISK_BUDGET_USD:.2f} USD")
    logger.info("=" * 60)

    # Send startup confirmation to Telegram
    try:
        send_startup_alert(COINS, DAILY_RISK_BUDGET_USD)
    except Exception as e:
        logger.error(f"Failed to send startup alert: {e}")

    state = BotState()
    last_day = None

    while True:
        try:
            current_day = now_utc().date()

            # --- DAILY RESET (at 00:05 UTC each new day) ---
            if last_day is not None and current_day != last_day:
                if is_after_utc("00:05"):
                    state.reset()
                    logger.info(f"New trading day: {current_day}")

            last_day = current_day

            # ---------------------------------------------------------------
            # PHASE 1: ASIA SESSION CLOSE ALERT (after 08:00 UTC)
            # ---------------------------------------------------------------
            if is_after_utc("08:00") and not state.asia_alert_sent:
                logger.info("Asia session closed. Fetching data and sending alert...")
                try:
                    all_bars = fetch_all_data()
                    state.asia_ranges = compute_asia_ranges(all_bars)
                    send_asia_session_alert(state.asia_ranges)
                    state.asia_alert_sent = True
                except Exception as e:
                    logger.error(f"Asia alert error: {e}")
                    send_error_alert(f"Asia session alert failed: {e}")

            # ---------------------------------------------------------------
            # PHASE 2: PREMARKET RANGE CLOSE ALERT (after 09:30 ET)
            # ---------------------------------------------------------------
            if is_after_et("09:30") and not state.prem_alert_sent:
                logger.info("Premarket range closed. Evaluating signals...")
                try:
                    all_bars = fetch_all_data()
                    state.prem_ranges = compute_prem_ranges(all_bars)

                    # Need fresh Asia ranges too (in case first fetch failed)
                    if any(v is None for v in state.asia_ranges.values()):
                        state.asia_ranges = compute_asia_ranges(all_bars)

                    state.signals = evaluate_signals(
                        state.prem_ranges, state.asia_ranges, all_bars
                    )

                    send_premarket_range_alert(
                        state.prem_ranges, state.asia_ranges, state.signals
                    )
                    state.prem_alert_sent = True
                    state.trade_window_active = True

                    # Pre-create TradeManagers for active coins
                    n_active = count_active_coins(state.signals)
                    if n_active > 0:
                        per_coin_risk = DAILY_RISK_BUDGET_USD / n_active
                        for coin, sig in state.signals.items():
                            if sig in ("LONG", "SHORT"):
                                # Manager will be fully initialized on signal entry
                                state.managers[coin] = None   # placeholder
                        logger.info(
                            f"Active coins: {n_active} | Per-coin risk: ${per_coin_risk:.4f}"
                        )

                except Exception as e:
                    logger.error(f"Premarket alert error: {e}")
                    send_error_alert(f"Premarket range alert failed: {e}")

            # ---------------------------------------------------------------
            # PHASE 3: TRADE WINDOW (09:30–12:00 ET)
            # ---------------------------------------------------------------
            if state.trade_window_active and not is_after_et("12:00"):
                try:
                    all_bars = fetch_all_data()
                    n_active = count_active_coins(state.signals)
                    per_coin_risk = (
                        DAILY_RISK_BUDGET_USD / n_active if n_active > 0
                        else DAILY_RISK_BUDGET_USD
                    )

                    for coin in COINS:
                        mgr = state.managers.get(coin)

                        # Skip coins not in setup
                        if state.signals.get(coin) not in ("LONG", "SHORT"):
                            continue

                        bars = all_bars.get(coin, [])
                        if not bars:
                            continue

                        # Check for new signal if no trade yet
                        if mgr is None or (mgr is not None and not mgr.is_done()):
                            if mgr is None:
                                sig = check_signal(
                                    coin=coin,
                                    bars=bars,
                                    asia_range=state.asia_ranges.get(coin),
                                    prem_range=state.prem_ranges.get(coin),
                                )
                                if sig is not None:
                                    # Verify direction matches our pre-session bias
                                    expected = state.signals[coin]
                                    if sig.direction == expected:
                                        mgr = TradeManager(sig, per_coin_risk)
                                        entered = mgr.enter_trade()
                                        if entered:
                                            state.managers[coin] = mgr
                                            logger.info(f"{coin}: Trade entered successfully.")
                                        else:
                                            logger.warning(f"{coin}: Trade entry failed.")
                                            mgr = None
                            elif not mgr.is_done():
                                # Monitor existing trade
                                current_price = get_current_price(coin)
                                if current_price:
                                    mgr.check_and_handle_tp1(current_price)

                except Exception as e:
                    logger.error(f"Trade window loop error: {e}")

            # ---------------------------------------------------------------
            # PHASE 4: 12:00 ET TIME STOP — CLOSE ALL POSITIONS
            # ---------------------------------------------------------------
            if state.trade_window_active and is_after_et("12:00") and not state.daily_summary_sent:
                logger.info("12:00 ET — Closing all positions and sending daily summary.")
                state.trade_window_active = False

                pnl_by_coin = {}
                total_r   = 0.0
                total_usd = 0.0

                for coin in COINS:
                    mgr = state.managers.get(coin)
                    if mgr is None:
                        pnl_by_coin[coin] = {"r": 0.0, "usd": 0.0, "reason": "No setup"}
                        continue

                    if not mgr.is_done():
                        # Force close
                        current_price = get_current_price(coin) or mgr.signal.entry_price
                        mgr.close_trade("12:00 ET Time Stop", current_price)

                    pnl = mgr.get_pnl()
                    pnl_by_coin[coin] = pnl
                    total_r   += pnl["r"]
                    total_usd += pnl["usd"]

                send_daily_summary(pnl_by_coin, total_r, total_usd)
                state.daily_summary_sent = True
                logger.info(f"Day complete. Total: {total_r:.3f}R / ${total_usd:.4f}")

        except KeyboardInterrupt:
            logger.info("Bot stopped by user (KeyboardInterrupt).")
            break
        except Exception as e:
            logger.error(f"Unhandled main loop error: {e}", exc_info=True)
            try:
                send_error_alert(f"Unhandled bot error: {e}")
            except Exception:
                pass

        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    run_bot()
