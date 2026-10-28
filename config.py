"""
config.py
=========
Central configuration for the ORB Trading Bot.
All secrets are loaded from ENVIRONMENT VARIABLES — never hardcoded here.

Set these on your Digital Ocean droplet using /etc/orb_bot.env (see README).
"""

import os

def _load_env_file(filepath: str):
    """Loads key=value pairs into os.environ if file exists and key not already set."""
    if not os.path.exists(filepath):
        return
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
    except Exception:
        pass

# Auto-load /etc/orb_bot.env (droplet) and local .env (development)
_load_env_file("/etc/orb_bot.env")
_load_env_file(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

# ===========================================================================
# TELEGRAM CONFIGURATION
# ===========================================================================
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")

# ===========================================================================
# WEEX API CONFIGURATION (Trade Execution)
# ===========================================================================
WEEX_API_KEY    = os.environ.get("WEEX_API_KEY", "")
WEEX_SECRET_KEY = os.environ.get("WEEX_SECRET_KEY", "")
WEEX_PASSPHRASE = os.environ.get("WEEX_PASSPHRASE", "")
WEEX_BASE_URL   = os.environ.get("WEEX_BASE_URL", "https://api-contract.weex.com")

# ===========================================================================
# KRAKEN API CONFIGURATION (Market Data — public, no auth needed)
# ===========================================================================
KRAKEN_BASE_URL = "https://api.kraken.com"

# Kraken pair mapping: our symbols -> Kraken REST pair names
KRAKEN_PAIRS = {
    "BTC":  "XBTUSD",
    "ETH":  "ETHUSD",
    "SOL":  "SOLUSD",
    "XRP":  "XRPUSD",
    "BNB":  "BNBUSD",
    "HYPE": "HYPEUSD",   # Verify listing; fallback handled in market_data.py
}

# ===========================================================================
# PORTFOLIO & RISK MANAGEMENT
# ===========================================================================
# Daily risk budget in USD. Total portfolio can lose at most this amount per day.
# 1R = $1 USD — scale this up once you've verified the bot in paper mode.
DAILY_RISK_BUDGET_USD = float(os.environ.get("DAILY_RISK_BUDGET_USD", "1.0"))

# ===========================================================================
# STRATEGY PARAMETERS (Best OOS configs from backtests)
# stop_mode: "ATR_2.5x" | "ATR_3.0x" | "Opposite_Side"
# tp_mode:   "Scaled_1R_1.5R" | "Scaled_1R_2R"
# min_width_pct: minimum 8:30-9:30 range width as a fraction
# ===========================================================================
COIN_CONFIGS = {
    "BTC":  {"weex_symbol": "BTCUSDT",  "stop_mode": "ATR_2.5x",     "tp_mode": "Scaled_1R_1.5R", "tp2_mult": 1.5, "min_width_pct": 0.006},
    "ETH":  {"weex_symbol": "ETHUSDT",  "stop_mode": "Opposite_Side", "tp_mode": "Scaled_1R_2R",   "tp2_mult": 2.0, "min_width_pct": 0.006},
    "SOL":  {"weex_symbol": "SOLUSDT",  "stop_mode": "ATR_2.5x",     "tp_mode": "Scaled_1R_1.5R", "tp2_mult": 1.5, "min_width_pct": 0.008},
    "XRP":  {"weex_symbol": "XRPUSDT",  "stop_mode": "Opposite_Side", "tp_mode": "Scaled_1R_2R",   "tp2_mult": 2.0, "min_width_pct": 0.015},
    "BNB":  {"weex_symbol": "BNBUSDT",  "stop_mode": "ATR_2.5x",     "tp_mode": "Scaled_1R_1.5R", "tp2_mult": 1.5, "min_width_pct": 0.010},
    "HYPE": {"weex_symbol": "HYPEUSDT", "stop_mode": "ATR_3.0x",     "tp_mode": "Scaled_1R_1.5R", "tp2_mult": 1.5, "min_width_pct": 0.020},
}

# ATR lookback period (in 5-minute bars)
ATR_PERIOD = 14

# EMA trend filter length (in 5-minute bars)
EMA_PERIOD = 50

# ===========================================================================
# SESSION TIMING (ET-aware; bot uses pytz for DST)
# ===========================================================================
# Asia session: 00:00 - 08:00 UTC
ASIA_SESSION_START_UTC = "00:00"
ASIA_SESSION_END_UTC   = "08:00"

# Trade evaluation window ends at 12:00 ET
TRADE_WINDOW_END_ET = "12:00"

# ===========================================================================
# BOT OPERATIONAL SETTINGS
# ===========================================================================
POLL_INTERVAL_SECONDS = 30
CANDLE_TF_MINUTES     = 5
BASE_DIR              = os.path.dirname(os.path.abspath(__file__))
LOG_FILE              = os.environ.get("LOG_FILE", os.path.join(BASE_DIR, "orb_bot.log"))
LOG_LEVEL             = os.environ.get("LOG_LEVEL", "INFO")


def validate_config():
    """Call at startup to ensure required env vars are set."""
    missing = []
    if not TELEGRAM_BOT_TOKEN:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not TELEGRAM_CHAT_ID:
        missing.append("TELEGRAM_CHAT_ID")
    if not WEEX_API_KEY:
        missing.append("WEEX_API_KEY")
    if not WEEX_SECRET_KEY:
        missing.append("WEEX_SECRET_KEY")
    if not WEEX_PASSPHRASE:
        missing.append("WEEX_PASSPHRASE")
    if missing:
        raise EnvironmentError(
            f"Missing required environment variables: {', '.join(missing)}\n"
            "Set them in /etc/orb_bot.env and restart the service."
        )
