#!/bin/bash
# Hook: Cache inbound Telegram messages (UserPromptSubmit)
# Reads the prompt from stdin, extracts Telegram channel tags, logs to JSONL.
#
# Install in Claude Code settings.json under hooks.UserPromptSubmit

CACHE_DIR="${TELEGRAM_CACHE_DIR:-$HOME/claude-telegram-remote/cache}"
mkdir -p "$CACHE_DIR"

# Read hook input from stdin
INPUT=$(cat)

# Extract channel messages from the prompt text
echo "$INPUT" | python3 -c "
import sys, json, re, os
from datetime import datetime

data = json.load(sys.stdin)
prompt = data.get('prompt', '') or data.get('user_message', '') or ''

# Find <channel> tags
pattern = r'<channel[^>]*chat_id=\"([^\"]+)\"[^>]*message_id=\"([^\"]+)\"[^>]*user=\"([^\"]+)\"[^>]*ts=\"([^\"]+)\"[^>]*>(.*?)</channel>'
matches = re.findall(pattern, prompt, re.DOTALL)

cache_dir = os.environ.get('TELEGRAM_CACHE_DIR', os.path.expanduser('~/claude-telegram-remote/cache'))

for chat_id, msg_id, user, ts, text in matches:
    safe_id = chat_id.replace('-', 'neg')
    path = os.path.join(cache_dir, f'{safe_id}.jsonl')
    entry = {
        'message_id': msg_id,
        'sender': user,
        'text': text.strip()[:500],
        'ts': ts
    }
    with open(path, 'a') as f:
        f.write(json.dumps(entry) + '\n')
" 2>/dev/null
