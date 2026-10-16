#!/bin/bash
cd "$(dirname "$0")"
echo ""
echo "==============================="
echo "   Discord Bot - Starting..."
echo "==============================="
if ! command -v python3 >/dev/null 2>&1; then
  echo "[X] Python is not installed. Opening the download page..."
  open https://www.python.org/downloads/
  read -p "Install it, then press Enter to close." ; exit 1
fi
if ! command -v ffmpeg >/dev/null 2>&1 && command -v brew >/dev/null 2>&1; then
  echo "Installing FFmpeg for music..."; brew install ffmpeg >/dev/null 2>&1
fi
if [ ! -f .installed-v2 ]; then
  echo "Installing required libraries, please wait..."
  python3 -m pip install -q -r requirements.txt || python3 -m pip install -q --user -r requirements.txt || { read -p "[X] Install failed. Press Enter."; exit 1; }
  touch .installed-v2
fi
if [ ! -f .env ]; then
  echo ""
  read -p "Paste your bot token then press Enter: " TOKEN
  read -p "Optional: paste your server ID (or press Enter): " GID
  printf "DISCORD_TOKEN=%s\nGUILD_ID=%s\nDATABASE_PATH=data/bot.db\n" "$TOKEN" "$GID" > .env
fi
echo ""
echo "Bot is running. Keep this window open. Close it to stop the bot."
python3 main.py
read -p "Press Enter to close."
