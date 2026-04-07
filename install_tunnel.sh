#!/bin/bash
# Install Cloudflare Tunnel & Telegram Notifier for WebUI

echo "===================================================="
echo "  WebUI Cloudflare Tunnel Installation"
echo "===================================================="

# 1. Install Cloudflared
echo ">> Checking cloudflared..."
if ! command -v cloudflared &> /dev/null; then
    echo ">> Installing cloudflared for ARM64..."
    ARCH=$(uname -m)
    if [ "$ARCH" = "aarch64" ]; then 
        FILE="cloudflared-linux-arm64"
    elif [ "$ARCH" = "armv7l" ] || [ "$ARCH" = "armv6l" ]; then 
        FILE="cloudflared-linux-arm"
    else 
        FILE="cloudflared-linux-arm64"
    fi
    curl -sLo cloudflared https://github.com/cloudflare/cloudflared/releases/latest/download/$FILE
    chmod +x cloudflared
    sudo mv cloudflared /usr/local/bin/cloudflared
else
    echo ">> cloudflared already installed."
fi

# 2. Check dependencies
echo ">> Checking python dependencies..."
python3 -c "import requests" 2>/dev/null || pip3 install requests

# 3. Setup Telegram Config
CONFIG_FILE="$HOME/.telegram_config.json"
echo "===================================================="
echo "  Telegram Bot Configuration"
echo "===================================================="
read -p "Enter Telegram Bot Token (or press Enter to skip if already set): " input_token
read -p "Enter Telegram Chat ID (or press Enter to skip if already set): " input_chat_id

if [ -f "$CONFIG_FILE" ]; then
    echo ">> Existing config found."
else
    echo "{
    \"bot_token\": \"REQUIRED_TOKEN_HERE\",
    \"chat_id\": \"REQUIRED_CHAT_ID_HERE\"
}" > "$CONFIG_FILE"
fi

if [ ! -z "$input_token" ]; then
    sed -i "s/\"bot_token\": \".*\"/\"bot_token\": \"$input_token\"/" "$CONFIG_FILE"
fi
if [ ! -z "$input_chat_id" ]; then
    sed -i "s/\"chat_id\": \".*\"/\"chat_id\": \"$input_chat_id\"/" "$CONFIG_FILE"
fi

# 4. Setup Systemd Service
echo ">> Configuring systemd service..."
SERVICE_FILE="/etc/systemd/system/cf-tunnel.service"

sudo bash -c "cat > $SERVICE_FILE" << EOF
[Unit]
Description=Cloudflare Tunnel & Telegram Bot
After=network.target

[Service]
Type=simple
User=$(whoami)
WorkingDirectory=$(pwd)
ExecStart=/usr/bin/python3 $(pwd)/cf_telegram_bot.py
Restart=on-failure
RestartSec=15

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable cf-tunnel.service
sudo systemctl restart cf-tunnel.service

echo "===================================================="
echo " Installation Complete!"
echo " Service Status:"
sudo systemctl status cf-tunnel.service --no-pager | grep "Active:"
echo "===================================================="
