#!/usr/bin/env python3
"""Log scanner template."""

import json
import os
import re
import sys
from pathlib import Path

CONFIG = {
    "telegram_token": os.environ.get("YBURN_TELEGRAM_TOKEN", ""),
    "telegram_chat_id": os.environ.get("YBURN_TELEGRAM_CHAT_ID", ""),
    "log_paths": [],
    "error_patterns": ["ERROR", "CRITICAL", "Traceback"],
    "alert_threshold": 1,
}


def send_output(message):
    """Send output via Telegram if configured, else print to stdout."""
    token = CONFIG["telegram_token"]
    chat_id = CONFIG["telegram_chat_id"]

    if token and chat_id:
        import urllib.request

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = json.dumps(
            {"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}
        ).encode("utf-8")
        request = urllib.request.Request(
            url, data=payload, headers={"Content-Type": "application/json"}
        )
        try:
            urllib.request.urlopen(request, timeout=30)
            return
        except Exception as exc:
            print(f"Telegram send failed: {exc}", file=sys.stderr)

    print(message)


def main():
    """Run the log scanner."""
    try:
        if not CONFIG["log_paths"]:
            send_output("*Log Scanner*\nNo log files configured.")
            return 1

        regexes = [re.compile(pattern) for pattern in CONFIG["error_patterns"]]
        total_matches = 0
        lines = ["*Log Scanner*", ""]

        for path_str in CONFIG["log_paths"]:
            path = Path(path_str).expanduser()
            if not path.exists():
                lines.append(f"- `{path}`: missing")
                continue

            text = path.read_text(encoding="utf-8", errors="replace")
            match_count = 0
            for regex in regexes:
                match_count += len(regex.findall(text))
            total_matches += match_count
            lines.append(f"- `{path}`: {match_count} match(es)")

        lines.extend(
            [
                "",
                f"*Patterns:* {len(regexes)}",
                f"*Total Matches:* {total_matches}",
                f"*Alert Threshold:* {CONFIG['alert_threshold']}",
            ]
        )

        send_output("\n".join(lines))
        return 1 if total_matches > CONFIG["alert_threshold"] else 0
    except Exception as exc:
        send_output(f"*Log Scanner Error*\n`{exc}`")
        return 2


if __name__ == "__main__":
    sys.exit(main())
