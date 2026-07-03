#!/bin/bash
# Send a Telegram message with inline keyboard buttons.
# Usage: tg-buttons.sh <chat_id> "message" <context> [pick|'<json>']
#
# "pick" shorthand generates 1/2/3/Ignore buttons with a unique ID.
#
# Examples:
#   tg-buttons.sh 123456789 "Pick a reply:" smr pick
#   -> Buttons: 1, 2, 3, Ignore (each with unique callback data)

CHAT_ID="$1"
MESSAGE="$2"
CONTEXT="$3"
BUTTONS="$4"

if [ -z "$CHAT_ID" ] || [ -z "$MESSAGE" ] || [ -z "$CONTEXT" ]; then
    echo "Usage: tg-buttons.sh <chat_id> \"message\" <context> [pick|'<buttons_json>']"
    exit 1
fi

TOKEN=$(security find-generic-password -a clawdbot -s telegram-buttons-bot-token -w 2>/dev/null)
if [ -z "$TOKEN" ]; then
    echo "ERROR: Buttons bot token not found in Keychain"
    echo "This uses a separate bot token for button messages."
    echo "Run: security add-generic-password -a clawdbot -s telegram-buttons-bot-token -w 'YOUR_TOKEN'"
    exit 1
fi

# Generate unique ID: context + 6 random hex chars
UNIQUE_ID="${CONTEXT}_$(openssl rand -hex 3)"

# Shorthand: "pick" generates 1/2/3/Ignore buttons
if [ "$BUTTONS" = "pick" ] || [ -z "$BUTTONS" ]; then
    BUTTONS="[[{\"text\":\"1\",\"callback_data\":\"${UNIQUE_ID}:1\"},{\"text\":\"2\",\"callback_data\":\"${UNIQUE_ID}:2\"},{\"text\":\"3\",\"callback_data\":\"${UNIQUE_ID}:3\"},{\"text\":\"Ignore\",\"callback_data\":\"${UNIQUE_ID}:ignore\"}]]"
fi

REPLY_MARKUP="{\"inline_keyboard\":${BUTTONS}}"

RESULT=$(curl -s -X POST "https://api.telegram.org/bot${TOKEN}/sendMessage" \
    -H "Content-Type: application/json" \
    -d "$(jq -n \
        --arg chat_id "$CHAT_ID" \
        --arg text "$MESSAGE" \
        --argjson reply_markup "$REPLY_MARKUP" \
        '{chat_id: $chat_id, text: $text, reply_markup: $reply_markup}')")

MSG_ID=$(echo "$RESULT" | jq -r '.result.message_id // "null"')
OK=$(echo "$RESULT" | jq -r '.ok // "false"')

echo "{\"ok\": $OK, \"message_id\": $MSG_ID, \"unique_id\": \"${UNIQUE_ID}\"}"
