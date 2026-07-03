#!/usr/bin/env python3
"""Session cleanup template for stuck or zombie OpenClaw sessions."""

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

CONFIG = {
    "telegram_token": os.environ.get("YBURN_TELEGRAM_TOKEN", ""),
    "telegram_chat_id": os.environ.get("YBURN_TELEGRAM_CHAT_ID", ""),
    "max_session_age_hours": 2,
    "dry_run": True,
    "exclude_session_labels": [],
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


def parse_timestamp(value):
    """Parse an ISO-ish timestamp into an aware datetime."""
    if not value or not isinstance(value, str):
        return None

    cleaned = value.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(cleaned)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def load_sessions():
    """Fetch sessions from openclaw."""
    result = subprocess.run(
        ["openclaw", "sessions", "list", "--json"],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(result.stdout)
    if isinstance(data, dict):
        for key in ("sessions", "items", "data"):
            value = data.get(key)
            if isinstance(value, list):
                return value
    if isinstance(data, list):
        return data
    raise ValueError("Unexpected JSON shape from openclaw sessions list")


def session_label(session):
    """Best-effort session label lookup."""
    for key in ("label", "name", "title"):
        value = session.get(key)
        if value:
            return str(value)
    return str(session.get("id", "unknown-session"))


def session_started_at(session):
    """Best-effort start-time lookup."""
    for key in ("startedAt", "createdAt", "startTime", "created_at"):
        parsed = parse_timestamp(session.get(key))
        if parsed:
            return parsed
    return None


def session_status(session):
    """Best-effort status lookup."""
    for key in ("status", "state"):
        value = session.get(key)
        if isinstance(value, str):
            return value.lower()
    return "unknown"


def find_stuck_sessions(sessions):
    """Find sessions older than the configured threshold."""
    cutoff = datetime.now(timezone.utc) - timedelta(
        hours=CONFIG["max_session_age_hours"]
    )
    excluded = {str(label).lower() for label in CONFIG["exclude_session_labels"]}
    checked = 0
    stuck = []

    for session in sessions:
        checked += 1
        label = session_label(session)
        if label.lower() in excluded:
            continue

        started_at = session_started_at(session)
        if not started_at:
            continue

        if session_status(session) in {"ended", "finished", "stopped", "killed"}:
            continue

        if started_at <= cutoff:
            stuck.append(
                {
                    "id": str(session.get("id", "")),
                    "label": label,
                    "started_at": started_at,
                }
            )

    return checked, stuck


def kill_session(session_id):
    """Kill one session by id."""
    subprocess.run(
        ["openclaw", "sessions", "kill", session_id],
        capture_output=True,
        text=True,
        check=True,
    )


def build_report(checked, stuck, killed, dry_run):
    """Create markdown output."""
    action_word = "would kill" if dry_run else "killed"
    lines = [
        "*Session Cleanup Report*",
        "",
        f"*Checked:* {checked} session(s)",
        f"*Threshold:* {CONFIG['max_session_age_hours']} hour(s)",
        f"*Matches:* {len(stuck)} stuck session(s)",
        f"*Result:* {len(killed)} session(s) {action_word}",
        "",
    ]

    if stuck:
        lines.append("*Stuck Sessions:*")
        for item in stuck:
            age = datetime.now(timezone.utc) - item["started_at"]
            age_hours = age.total_seconds() / 3600.0
            lines.append(f"- `{item['label']}` ({item['id']}) age={age_hours:.1f}h")
    else:
        lines.append("No stuck sessions found.")

    return "\n".join(lines)


def main():
    """Run session cleanup."""
    try:
        sessions = load_sessions()
        checked, stuck = find_stuck_sessions(sessions)
        killed = []

        if not CONFIG["dry_run"]:
            for session in stuck:
                if not session["id"]:
                    raise ValueError(
                        f"Session '{session['label']}' missing id; cannot kill"
                    )
                kill_session(session["id"])
                killed.append(session["label"])
        else:
            killed = [session["label"] for session in stuck]

        send_output(build_report(checked, stuck, killed, CONFIG["dry_run"]))
        return 0
    except Exception as exc:
        send_output(f"*Session Cleanup Error*\n`{exc}`")
        return 1


if __name__ == "__main__":
    sys.exit(main())
