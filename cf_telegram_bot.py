import os
import sys
import json
import time
import re
import signal
import subprocess
import requests

CONFIG_FILE = os.path.expanduser("~/.telegram_config.json")
LOCAL_URL = "http://127.0.0.1:5000"

def load_config():
    if not os.path.exists(CONFIG_FILE):
        default_config = {
            "bot_token": "YOUR_BOT_TOKEN_HERE",
            "chat_id": "YOUR_CHAT_ID_HERE"
        }
        with open(CONFIG_FILE, 'w') as f:
            json.dump(default_config, f, indent=4)
        print(f"Created config template at {CONFIG_FILE}. Please edit it with your Telegram tokens.")
        sys.exit(1)
        
    with open(CONFIG_FILE, 'r') as f:
        config = json.load(f)
        
    if config.get("bot_token") == "YOUR_BOT_TOKEN_HERE" or config.get("chat_id") == "YOUR_CHAT_ID_HERE":
        print(f"Please update {CONFIG_FILE} with your actual Telegram bot_token and chat_id.")
        sys.exit(1)
        
    return config

def send_telegram_message(config, text):
    bot_token = config.get("bot_token")
    chat_id = config.get("chat_id")
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML"
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            print("Telegram notification sent successfully.")
        else:
            print(f"Failed to send Telegram message: {response.text}")
    except Exception as e:
        print(f"Exception while sending Telegram message: {e}")

def main():
    config = load_config()
    
    print(f"Starting cloudflared tunnel for {LOCAL_URL}...")
    
    # Start cloudflared
    cmd = ["cloudflared", "tunnel", "--url", LOCAL_URL]
    
    process = subprocess.Popen(
        cmd, 
        stdout=subprocess.PIPE, 
        stderr=subprocess.STDOUT, 
        text=True, 
        bufsize=1
    )
    
    # Handle graceful exit
    def signal_handler(sig, frame):
        print("Stopping cloudflared...")
        process.terminate()
        sys.exit(0)
        
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    import socket
    hostname = socket.gethostname()
    
    url_pattern = re.compile(r'(https://[a-zA-Z0-9-]+\.trycloudflare\.com)')
    url_found = False
    
    try:
        for line in iter(process.stdout.readline, ''):
            if not line:
                break
                
            print(line.strip())
            
            if not url_found:
                match = url_pattern.search(line)
                if match:
                    cloudflare_url = match.group(1)
                    print(f"\n>>> FOUND URL: {cloudflare_url} <<<\n")
                    message = f"ðŸŸ¢ <b>[{hostname}] WebUI is Online!</b>\n\nLink: {cloudflare_url}"
                    send_telegram_message(config, message)
                    url_found = True
                    
        process.wait()
    except KeyboardInterrupt:
        process.terminate()

if __name__ == "__main__":
    main()
