#!/bin/bash
# Hook: Cache outbound Telegram replies (PostToolUse on telegram reply)
# Logs Claude's replies to the same JSONL cache as inbound messages.
#
# Install in Claude Code settings.json under hooks.PostToolUse
# with matcher: "mcp__plugin_telegram_telegram__reply"

CACHE_DIR="${TELEGRAM_CACHE_DIR:-$HOME/claude-telegram-remote/cache}"
mkdir -p "$CACHE_DIR"

# Read hook input from stdin
INPUT=$(cat)

echo "$INPUT" | python3 -c "
import sys, json, os
from datetime import datetime, timezone

data = json.load(sys.stdin)
tool_input = data.get('tool_input', data.get('input', {}))
tool_response = data.get('tool_response', data.get('result', {}))

chat_id = tool_input.get('chat_id', '')
text = tool_input.get('text', '')

if not chat_id or not text:
    sys.exit(0)

# Extract message ID from response
msg_id = ''
if isinstance(tool_response, dict):
    msg_id = str(tool_response.get('message_id', ''))
elif isinstance(tool_response, str):
    import re
    m = re.search(r'id:\s*(\d+)', tool_response)
    if m:
        msg_id = m.group(1)

cache_dir = os.environ.get('TELEGRAM_CACHE_DIR', os.path.expanduser('~/claude-telegram-remote/cache'))
safe_id = chat_id.replace('-', 'neg')
path = os.path.join(cache_dir, f'{safe_id}.jsonl')

entry = {
    'message_id': msg_id,
    'sender': 'assistant',
    'text': text[:500],
    'ts': datetime.now(timezone.utc).isoformat()
}
with open(path, 'a') as f:
    f.write(json.dumps(entry) + '\n')
" 2>/dev/null
