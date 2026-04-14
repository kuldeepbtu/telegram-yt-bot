#!/usr/bin/env bash
# deploy.sh — Full AWS EC2 setup for Pyrogram YouTube Bot
# Run: bash deploy.sh
set -euo pipefail

DIR="/home/ubuntu/ytbot-pyrogram"
SVC="ytbot"
GREEN="\e[32m"; YELLOW="\e[33m"; RESET="\e[0m"
info() { echo -e "${GREEN}[INFO]${RESET} $*"; }
warn() { echo -e "${YELLOW}[WARN]${RESET} $*"; }

info "Installing system packages…"
sudo apt-get update -qq
sudo apt-get install -y -qq python3 python3-pip python3-venv ffmpeg git

info "Setting up project directory…"
mkdir -p "$DIR"
cp -r ./* "$DIR/" 2>/dev/null || true
chown -R ubuntu:ubuntu "$DIR"

info "Creating Python virtual environment…"
cd "$DIR"
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q

if [[ ! -f "$DIR/.env" ]]; then
    cp "$DIR/.env.example" "$DIR/.env"
    warn ".env created from template — fill in API_ID, API_HASH, SESSION_STRING"
fi

info "Installing systemd service…"
sudo cp "$DIR/ytbot.service" "/etc/systemd/system/${SVC}.service"
sudo systemctl daemon-reload
sudo systemctl enable "$SVC"

sudo mkdir -p /tmp/ytbot_downloads
sudo chown ubuntu:ubuntu /tmp/ytbot_downloads

# Check if all required vars are set
source "$DIR/.env" 2>/dev/null || true
if [[ -z "${SESSION_STRING:-}" || "${SESSION_STRING}" == "BQA..."* ]]; then
    warn "SESSION_STRING not set. Run generate_session.py locally first."
    warn "Then: nano $DIR/.env  →  sudo systemctl start $SVC"
else
    sudo systemctl start "$SVC"
    info "✅ Bot started! Logs: sudo journalctl -u $SVC -f"
fi

info "🎉 Deploy complete."
