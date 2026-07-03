#!/usr/bin/env python3
"""typing-indicator-pinger.py - Show "typing..." in a Telegram chat while Claude works.

Spawned by the UserPromptSubmit hook when an inbound TG message arrives. Loops
sendChatAction(typing) every 4s for the given chat_id until killed (PostToolUse
on the reply tool, or the Stop hook backstop) or until the hard 10-min ceiling
hits.

Usage:
    python3 typing-indicator-pinger.py <chat_id>

Safety properties (no external supervisor needed):
- Hard 10-min wall-clock ceiling. Checked at the TOP of every loop iteration.
- 3-second socket timeout on the Telegram HTTP call. Even if the API hangs,
  the next iteration's age check kicks in within ~7s.
- Clean SIGTERM/SIGINT exit. PID file removed on exit (atexit).
- Single-instance per chat_id. If a pinger is already running, the new spawn
  exits silently.

Token source: ~/.claude/channels/telegram/.env (TELEGRAM_BOT_TOKEN=...)
or the TELEGRAM_BOT_TOKEN environment variable.
"""

import atexit
import os
import signal
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PING_INTERVAL_SECONDS = 4
HARD_CEILING_SECONDS = 600  # 10 minutes
HTTP_TIMEOUT_SECONDS = 3
PID_DIR = Path("/tmp")
ENV_PATH = Path.home() / ".claude" / "channels" / "telegram" / ".env"


def _read_token():
    env = os.environ.get("TELEGRAM_BOT_TOKEN")
    if env:
        return env
    if not ENV_PATH.exists():
        return None
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if line.startswith("TELEGRAM_BOT_TOKEN="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _claim_pid_file(pid_path):
    """Single-instance guard. Returns True if we own the file, False to defer."""
    if pid_path.exists():
        try:
            existing = int(pid_path.read_text().strip())
            if _pid_alive(existing):
                return False
        except (ValueError, OSError):
            pass
        try:
            pid_path.unlink()
        except OSError:
            pass
    pid_path.write_text(str(os.getpid()))
    return True


def _release_pid_file(pid_path):
    try:
        if pid_path.exists() and pid_path.read_text().strip() == str(os.getpid()):
            pid_path.unlink()
    except OSError:
        pass


def _send_chat_action(token, chat_id):
    url = f"https://api.telegram.org/bot{token}/sendChatAction"
    data = urllib.parse.urlencode({"chat_id": chat_id, "action": "typing"}).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as resp:
            resp.read()
    except (urllib.error.URLError, socket.timeout, ConnectionError):
        pass


def main():
    if len(sys.argv) != 2:
        return 0
    chat_id = sys.argv[1].strip()
    if not chat_id:
        return 0

    token = _read_token()
    if not token:
        return 0

    pid_path = PID_DIR / f"tg-typing-{chat_id.replace('-', 'neg')}.pid"
    if not _claim_pid_file(pid_path):
        return 0
    atexit.register(_release_pid_file, pid_path)

    def _graceful_exit(signum, frame):
        sys.exit(0)
    signal.signal(signal.SIGTERM, _graceful_exit)
    signal.signal(signal.SIGINT, _graceful_exit)

    started = time.monotonic()
    while True:
        if time.monotonic() - started > HARD_CEILING_SECONDS:
            return 0
        _send_chat_action(token, chat_id)
        time.sleep(PING_INTERVAL_SECONDS)


if __name__ == "__main__":
    sys.exit(main())
