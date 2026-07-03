#!/usr/bin/env bash
# Hook: spawn typing-indicator-pinger.py for any inbound Telegram chat_id.
#
# UserPromptSubmit hook. Reads stdin JSON, extracts each unique chat_id from
# <channel source="plugin:telegram:telegram" chat_id="..."> tags in the user
# message, and spawns one detached pinger per chat_id. The pinger itself
# enforces single-instance per chat_id via PID file - re-spawning is a no-op
# if one is already running.
#
# Always exits 0 - never blocks the main process.

PINGER="${HOME}/claude-telegram-remote/hooks/typing-indicator-pinger.py"

[ -f "$PINGER" ] || exit 0

INPUT="$(cat)"
[ -z "$INPUT" ] && exit 0

CHAT_IDS=$(printf '%s' "$INPUT" | python3 -c '
import json, re, sys
raw = sys.stdin.read()
try:
    obj = json.loads(raw)
    text = obj.get("prompt", "") or ""
    if not text:
        text = raw
except Exception:
    text = raw
ids = re.findall(r"<channel source=\"plugin:telegram:telegram\" chat_id=\"([^\"]+)\"", text)
seen = set()
for cid in ids:
    if cid not in seen:
        seen.add(cid)
        print(cid)
' 2>/dev/null)

[ -z "$CHAT_IDS" ] && exit 0

while IFS= read -r CID; do
    [ -z "$CID" ] && continue
    nohup python3 "$PINGER" "$CID" >/dev/null 2>&1 &
    disown $!
done <<< "$CHAT_IDS"

exit 0
