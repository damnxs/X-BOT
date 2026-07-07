#!/usr/bin/env bash
#
# X Bot — deploy script for Ubuntu/Debian.
#
# HOW TO USE (on the VPS):
#   1) Upload this whole project folder to the VPS (e.g. rsync/scp/git clone).
#   2) cd into that folder (the one containing bot.py, server/, web/, deploy.sh).
#   3) bash deploy.sh
#
# It installs everything, builds the frontend, sets up a systemd service, and
# configures nginx for HTTP. HTTPS is a one-liner you run AFTER you point DNS
# (see the end of this script).
#
set -euo pipefail

DOMAIN="mhfcorp.com"
SERVICE="xbot"
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$APP_DIR/.venv"
ENV_FILE="/etc/xbot.env"
RUN_USER="$(whoami)"

echo "==> Deploying X Bot"
echo "    domain : $DOMAIN"
echo "    app dir: $APP_DIR"
echo "    user   : $RUN_USER"
echo

# --- 1) system packages ------------------------------------------------
echo "==> Installing system packages..."
sudo apt-get update -y
sudo apt-get install -y \
  python3-venv python3-pip python3-dev build-essential \
  nginx certbot python3-certbot-nginx git curl ca-certificates

# --- 2) Node 20 (needed to build the Vite frontend) --------------------
if ! command -v node >/dev/null 2>&1 || [ "$(node -v 2>/dev/null | cut -d. -f1 | tr -d v)" -lt 18 ]; then
  echo "==> Installing Node 20..."
  curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
  sudo apt-get install -y nodejs
fi

# --- 3) Python venv + dependencies -------------------------------------
echo "==> Setting up Python venv + deps..."
python3 -m venv "$VENV"
"$VENV/bin/pip" install --upgrade pip
"$VENV/bin/pip" install -r "$APP_DIR/requirements.txt"

# --- 4) Playwright Chromium + OS libs ----------------------------------
echo "==> Installing Playwright Chromium + system libs..."
"$VENV/bin/playwright" install chromium
sudo "$VENV/bin/playwright" install-deps chromium

# --- 5) Build the frontend ---------------------------------------------
echo "==> Building frontend..."
pushd "$APP_DIR/web" >/dev/null
  npm install
  npm run build
popd >/dev/null

# --- 6) Secrets: /etc/xbot.env -----------------------------------------
echo "==> Writing secrets to $ENV_FILE ..."
sudo install -d -m 755 /etc
[ -f "$ENV_FILE" ] || sudo install -m 600 /dev/null "$ENV_FILE"
if ! sudo grep -q '^XBOT_SESSION_SECRET=' "$ENV_FILE"; then
  echo "XBOT_SESSION_SECRET=$(python3 -c 'import secrets; print(secrets.token_hex(32))')" \
    | sudo tee -a "$ENV_FILE" >/dev/null
fi
read -r -s -p "Set the X Bot UI password: " UIPW; echo
UIPW="${XBOT_UI_PASSWORD:-$UIPW}"
TMP="$(mktemp)"
sudo cat "$ENV_FILE" | grep -v '^XBOT_UI_PASSWORD=' > "$TMP" || true
echo "XBOT_UI_PASSWORD=$UIPW" >> "$TMP"
sudo cp "$TMP" "$ENV_FILE"
sudo chmod 600 "$ENV_FILE"
rm -f "$TMP"

# --- 7) systemd service ------------------------------------------------
echo "==> Installing systemd service..."
sudo tee "/etc/systemd/system/${SERVICE}.service" >/dev/null <<EOF
[Unit]
Description=X Bot
After=network.target

[Service]
User=$RUN_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$VENV/bin/uvicorn server.app:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now "$SERVICE"
sleep 2
echo "    service status:"
sudo systemctl --no-pager status "$SERVICE" | head -8 || true

# --- 8) nginx (HTTP first; HTTPS after DNS) ----------------------------
echo "==> Configuring nginx (HTTP)..."
sudo tee "/etc/nginx/sites-available/${DOMAIN}" >/dev/null <<EOF
server {
    listen 80;
    server_name $DOMAIN www.$DOMAIN;

    client_max_body_size 10m;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 300s;
    }
}
EOF
sudo ln -sf "/etc/nginx/sites-available/${DOMAIN}" "/etc/nginx/sites-enabled/${DOMAIN}"
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl enable nginx
sudo systemctl restart nginx

echo
echo "============================================================"
echo " DONE — X Bot is running on the VPS over HTTP."
echo "============================================================"
echo
echo "NEXT STEPS:"
echo "  1) Point DNS: add an A record for  $DOMAIN  and  www.$DOMAIN"
echo "     -> this VPS's public IP. Wait for it to resolve:"
echo "       dig +short $DOMAIN"
echo
echo "  2) Once DNS resolves, enable HTTPS (free Let's Encrypt cert):"
echo "       sudo certbot --nginx -d $DOMAIN -d www.$DOMAIN --redirect"
echo
echo "  3) Open http://$DOMAIN (or https after step 2) and log in"
echo "     with the password you just set."
echo
echo "LOGS / CONTROL:"
echo "   tail logs      : sudo journalctl -u $SERVICE -f"
echo "   restart bot    : sudo systemctl restart $SERVICE"
echo "   restart nginx  : sudo systemctl reload nginx"
echo
echo "NOTE: Chromium is memory-hungry; this VPS should have ~2GB RAM."
echo "      X logins from a datacenter IP can sometimes need verification —"
echo "      if login fails, check logs/actions.jsonl + screenshots for that account."
