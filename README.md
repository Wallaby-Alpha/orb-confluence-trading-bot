# ORB Confluence Trading Bot

> Automated intraday Opening Range Breakout strategy running on BTC, ETH, SOL, XRP, BNB, and HYPE. Uses Kraken for market data, Weex for trade execution, and Telegram for alerts.

## Strategy Summary

- **Range:** 8:30–9:30 ET pre-market range
- **Entry filter:** Triple confluence — breakout of range + 50 EMA alignment + Asia session midpoint bias
- **Stop loss:** ATR-based (2.5x or 3.0x) or opposite side of range (per-coin optimized)
- **Take profit:** 50% at 1R → move stop to breakeven → 50% at 1.5R or 2R
- **Time stop:** Force-close all positions at 12:00 ET
- **Risk:** Dynamic daily budget of $1R split equally across all active coins

## Daily Alert Flow

1. **08:00 UTC** — Asia session close alert (range + midpoint for all 6 coins)
2. **09:30 ET** — Premarket range close alert (bias: LONG / SHORT / SKIP per coin)
3. **On breakout** — Trade entry alert (direction, entry, stop, TP1, TP2)
4. **On TP1 hit** — TP1 alert + stop moved to breakeven
5. **On exit** — Trade result (stop hit / TP2 hit / 12:00 ET time stop)
6. **12:00 ET** — Daily summary (P&L per coin + total)

## Files

| File | Purpose |
|------|---------|
| `config.py` | Strategy params + env var loading |
| `market_data.py` | Kraken OHLCV + ATR/EMA indicators |
| `strategy_engine.py` | Confluence signal detection |
| `trade_manager.py` | Trade lifecycle (entry → TP1 → exit) |
| `weex_client.py` | Weex futures API client |
| `telegram_alerts.py` | All Telegram notifications |
| `main.py` | 24/7 event loop |
| `orb_bot.service` | systemd service definition |
| `deploy.sh` | One-command Digital Ocean setup |
| `.env.example` | Environment variable template |

## Setup

### 1. Clone the repo

```bash
git clone https://github.com/YOUR_USERNAME/orb-confluence-bot.git
cd orb-confluence-bot
```

### 2. Get your API keys

**Telegram:**
1. Message `@BotFather` → `/newbot` → copy your token
2. Message your bot, then visit `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy `chat_id`

**Weex:**
1. weex.com → Account → API Management → Create key with Trade permissions

### 3. Deploy to Digital Ocean

```bash
# On your local machine — upload to droplet
scp -r ./* root@YOUR_DROPLET_IP:/tmp/orb_bot/

# SSH into your droplet
ssh root@YOUR_DROPLET_IP

# Run setup
cd /tmp/orb_bot
chmod +x deploy.sh
sudo ./deploy.sh

# Fill in your secrets
sudo nano /etc/orb_bot.env

# Restart bot with new secrets
sudo systemctl restart orb_bot

# Verify it's running
sudo systemctl status orb_bot
sudo journalctl -u orb_bot -f
```

### 4. Verify Telegram works

```bash
cd /home/ubuntu/orb_bot
source venv/bin/activate
source /etc/orb_bot.env
python -c "from telegram_alerts import send_error_alert; send_error_alert('Bot connected ✅')"
```

## Configuration

All secrets live in `/etc/orb_bot.env` on the droplet (never in code):

```bash
TELEGRAM_BOT_TOKEN=your_token
TELEGRAM_CHAT_ID=your_chat_id
WEEX_API_KEY=your_key
WEEX_SECRET_KEY=your_secret
WEEX_PASSPHRASE=your_passphrase

# Optional
DAILY_RISK_BUDGET_USD=1.0
LOG_LEVEL=INFO
```

## Running Locally (for testing)

```bash
# Create a local .env file (do NOT commit this)
cp .env.example .env
# Edit .env with real values
nano .env

# Install deps
pip install -r requirements.txt

# Run (Linux/Mac)
export $(cat .env | xargs) && python main.py

# Run (Windows PowerShell)
Get-Content .env | ForEach-Object { $k,$v = $_ -split '=',2; [System.Environment]::SetEnvironmentVariable($k,$v) }
python main.py
```

## Quick Commands (on droplet)

```bash
sudo systemctl status orb_bot      # Check status
sudo systemctl restart orb_bot     # Restart after config changes
sudo journalctl -u orb_bot -f      # Live log stream
tail -f /home/ubuntu/orb_bot/orb_bot.log  # Log file
sudo nano /etc/orb_bot.env         # Edit secrets
```

## Important Notes

- **Paper mode first:** Comment out `place_market_order()` calls in `trade_manager.py` and run for 1–2 weeks to verify signals before going live.
- **HYPE on Kraken:** May not be listed yet — disable in `COIN_CONFIGS` if data fails.
- **Weex API:** Verify endpoint paths against [Weex API docs](https://weex.gitbook.io) before live trading.
- **Scaling up:** Once verified, increase `DAILY_RISK_BUDGET_USD` in `/etc/orb_bot.env`.
