#!/usr/bin/env python3
"""OAuth token health check template."""

import base64
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

CONFIG = {
    "telegram_token": os.environ.get("YBURN_TELEGRAM_TOKEN", ""),
    "telegram_chat_id": os.environ.get("YBURN_TELEGRAM_CHAT_ID", ""),
    "token_file_paths": [],
    "token_env_vars": [],
    "warn_if_expiring_within_days": 7,
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
    """Parse multiple timestamp shapes into UTC datetimes."""
    if value in (None, ""):
        return None

    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)

    if isinstance(value, str):
        cleaned = value.strip()
        if cleaned.isdigit():
            return datetime.fromtimestamp(int(cleaned), tz=timezone.utc)
        if cleaned.endswith("Z"):
            cleaned = cleaned[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(cleaned)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    return None


def decode_jwt_exp(token):
    """Return JWT exp timestamp if present."""
    parts = token.split(".")
    if len(parts) < 2:
        return None

    payload = parts[1]
    padding = "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode((payload + padding).encode("utf-8"))
        data = json.loads(decoded.decode("utf-8"))
    except Exception:
        return None

    return parse_timestamp(data.get("exp"))


def extract_expiry_from_json(data):
    """Try common OAuth expiry keys."""
    for key in (
        "expires_at",
        "expiresAt",
        "expiry",
        "expires_on",
        "expiration",
        "expiry_date",
        "token_expiry",
    ):
        parsed = parse_timestamp(data.get(key))
        if parsed:
            return parsed

    for key in ("access_token", "id_token", "token"):
        value = data.get(key)
        if isinstance(value, str):
            parsed = decode_jwt_exp(value)
            if parsed:
                return parsed
    return None


def inspect_token_value(source, token_value):
    """Inspect one token string."""
    if not token_value:
        return {"source": source, "status": "missing", "expiry": None}

    expiry = decode_jwt_exp(token_value)
    if expiry is None:
        return {"source": source, "status": "present", "expiry": None}
    return {"source": source, "status": "present", "expiry": expiry}


def inspect_token_file(path_str):
    """Inspect one token file."""
    path = Path(path_str).expanduser()
    if not path.exists():
        return {"source": str(path), "status": "missing", "expiry": None}

    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return {"source": str(path), "status": "missing", "expiry": None}

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return inspect_token_value(str(path), text)

    expiry = extract_expiry_from_json(data)
    if expiry is None:
        return {"source": str(path), "status": "present", "expiry": None}
    return {"source": str(path), "status": "present", "expiry": expiry}


def evaluate_results(results):
    """Assign health state."""
    now = datetime.now(timezone.utc)
    warning_cutoff = now + timedelta(days=CONFIG["warn_if_expiring_within_days"])
    exit_code = 0

    for result in results:
        expiry = result["expiry"]
        if result["status"] == "missing":
            result["level"] = "critical"
            exit_code = 1
        elif expiry is None:
            result["level"] = "unknown"
        elif expiry <= now:
            result["level"] = "critical"
            exit_code = 1
        elif expiry <= warning_cutoff:
            result["level"] = "warning"
            exit_code = max(exit_code, 1)
        else:
            result["level"] = "ok"

    return exit_code


def build_report(results):
    """Format markdown report."""
    counts = {"ok": 0, "warning": 0, "critical": 0, "unknown": 0}
    for result in results:
        counts[result["level"]] += 1

    lines = [
        "*OAuth Health Check*",
        "",
        f"*Checked:* {len(results)} token source(s)",
        f"*OK:* {counts['ok']} | *Warning:* {counts['warning']} | *Critical:* {counts['critical']} | *Unknown:* {counts['unknown']}",
        "",
    ]

    for result in results:
        expiry = result["expiry"]
        if expiry is None:
            suffix = "no expiry metadata"
        else:
            suffix = expiry.isoformat()
        lines.append(f"- `{result['source']}`: {result['level']} ({suffix})")

    return "\n".join(lines)


def main():
    """Run the OAuth health check."""
    try:
        results = []

        for path in CONFIG["token_file_paths"]:
            results.append(inspect_token_file(path))

        for env_name in CONFIG["token_env_vars"]:
            results.append(inspect_token_value(env_name, os.environ.get(env_name, "")))

        if not results:
            send_output("*OAuth Health Check*\nNo token files or env vars configured.")
            return 1

        exit_code = evaluate_results(results)
        send_output(build_report(results))
        return exit_code
    except Exception as exc:
        send_output(f"*OAuth Health Check Error*\n`{exc}`")
        return 2


if __name__ == "__main__":
    sys.exit(main())
