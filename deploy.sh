#!/usr/bin/env bash
# deploy.sh
# =========
# One-time setup script for the ORB Trading Bot on a fresh Ubuntu 22.04 droplet.
# Run as root or with sudo.
#
# Usage (after uploading files):
#   chmod +x deploy.sh
#   sudo ./deploy.sh

set -e

BOT_DIR="/home/ubuntu/orb_bot"
VENV_DIR="$BOT_DIR/venv"
ENV_FILE="/etc/orb_bot.env"
SERVICE_FILE="/etc/systemd/system/orb_bot.service"

echo ""
echo "==================================================="
echo "  ORB Confluence Bot — Droplet Setup"
echo "==================================================="

# 1. System dependencies
echo ""
echo "[1/7] Installing system packages..."
apt-get update -qq
apt-get install -y python3 python3-pip python3-venv git curl nano

# 2. Create ubuntu user if needed
if ! id -u ubuntu &>/dev/null; then
    echo "[2/7] Creating ubuntu user..."
    adduser --disabled-password --gecos "" ubuntu
else
    echo "[2/7] ubuntu user already exists. Skipping."
fi

# 3. Create bot directory and set ownership
echo "[3/7] Setting up bot directory at $BOT_DIR..."
mkdir -p "$BOT_DIR"
cp -r ./* "$BOT_DIR/" 2>/dev/null || true
cp .gitignore "$BOT_DIR/" 2>/dev/null || true
cp .env.example "$BOT_DIR/" 2>/dev/null || true
chown -R ubuntu:ubuntu "$BOT_DIR"

# 4. Create Python virtual environment
echo "[4/7] Creating Python virtual environment..."
sudo -u ubuntu python3 -m venv "$VENV_DIR"
sudo -u ubuntu "$VENV_DIR/bin/pip" install --upgrade pip -q
sudo -u ubuntu "$VENV_DIR/bin/pip" install -r "$BOT_DIR/requirements.txt" -q

# 5. Create env file if it doesn't exist
echo "[5/7] Checking environment file..."
if [ ! -f "$ENV_FILE" ]; then
    cp "$BOT_DIR/.env.example" "$ENV_FILE"
    chown root:ubuntu "$ENV_FILE"
    chmod 640 "$ENV_FILE"
    echo ""
    echo "  ⚠️  Created $ENV_FILE from template."
    echo "  ➡️  You MUST edit it now before starting the bot:"
    echo "      sudo nano $ENV_FILE"
    echo ""
else
    echo "  ✅ $ENV_FILE already exists. Skipping."
fi

# 6. Install and update systemd service
echo "[6/7] Installing systemd service..."
cp "$BOT_DIR/orb_bot.service" "$SERVICE_FILE"
systemctl daemon-reload
systemctl enable orb_bot.service

# 7. Start the service
echo "[7/7] Starting bot service..."
systemctl restart orb_bot.service

echo ""
echo "==================================================="
echo "  ✅ Setup complete!"
echo ""
echo "  NEXT STEPS:"
echo "  1. Edit your secrets:  sudo nano $ENV_FILE"
echo "  2. Restart the bot:    sudo systemctl restart orb_bot"
echo ""
echo "  USEFUL COMMANDS:"
echo "  Status:   sudo systemctl status orb_bot"
echo "  Logs:     sudo journalctl -u orb_bot -f"
echo "  Stop:     sudo systemctl stop orb_bot"
echo "  Restart:  sudo systemctl restart orb_bot"
echo "==================================================="
