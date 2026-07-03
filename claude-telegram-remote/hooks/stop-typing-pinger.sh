#!/usr/bin/env bash
# Hook: kill any running typing-indicator pingers.
#
# Wired to PostToolUse(mcp__plugin_telegram_telegram__reply) AND Stop. The
# reply path is the normal kill point; the Stop path is a backstop in case
# the turn ends without a reply tool call (errored, terminal-only response,
# etc.). Both are safe to fire repeatedly - kill is idempotent.
#
# Always exits 0 - never blocks the main process.

shopt -s nullglob
for pid_file in /tmp/tg-typing-*.pid; do
    pid="$(cat "$pid_file" 2>/dev/null)"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        kill -TERM "$pid" 2>/dev/null
    fi
    rm -f "$pid_file"
done
exit 0
