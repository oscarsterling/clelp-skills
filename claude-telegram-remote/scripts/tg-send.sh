#!/bin/bash
# Send a plain text Telegram message.
# Usage: tg-send.sh <chat_id> "<message>"

CHAT_ID="$1"
MESSAGE="$2"

if [ -z "$CHAT_ID" ] || [ -z "$MESSAGE" ]; then
    echo "Usage: tg-send.sh <chat_id> \"message\""
    exit 1
fi

# Read bot token from macOS Keychain
TOKEN=$(security find-generic-password -a clawdbot -s telegram-commander-bot-token -w 2>/dev/null)
if [ -z "$TOKEN" ]; then
    echo "ERROR: Bot token not found in Keychain"
    echo "Run: security add-generic-password -a clawdbot -s telegram-commander-bot-token -w 'YOUR_TOKEN'"
    exit 1
fi

curl -s -X POST "https://api.telegram.org/bot${TOKEN}/sendMessage" \
    -H "Content-Type: application/json" \
    -d "$(jq -n \
        --arg chat_id "$CHAT_ID" \
        --arg text "$MESSAGE" \
        '{chat_id: $chat_id, text: $text}')" \
    | jq -r '.ok // "failed"'
