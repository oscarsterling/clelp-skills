#!/usr/bin/env python3
"""SSL certificate expiry template."""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone

CONFIG = {
    "telegram_token": os.environ.get("YBURN_TELEGRAM_TOKEN", ""),
    "telegram_chat_id": os.environ.get("YBURN_TELEGRAM_CHAT_ID", ""),
    "domains": [],
    "warn_if_expiring_within_days": 30,
    "critical_if_expiring_within_days": 7,
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


def fetch_not_after(domain):
    """Fetch certificate expiry for one domain."""
    handshake = subprocess.run(
        ["openssl", "s_client", "-servername", domain, "-connect", f"{domain}:443"],
        input="",
        capture_output=True,
        text=True,
        timeout=30,
    )
    if handshake.returncode != 0:
        raise RuntimeError(handshake.stderr.strip() or f"openssl s_client failed for {domain}")

    cert = subprocess.run(
        ["openssl", "x509", "-noout", "-enddate"],
        input=handshake.stdout,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    line = cert.stdout.strip()
    if not line.startswith("notAfter="):
        raise ValueError(f"Unexpected openssl output for {domain}: {line}")
    value = line.split("=", 1)[1].strip()
    return datetime.strptime(value, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)


def classify_expiry(expiry):
    """Classify certificate state."""
    now = datetime.now(timezone.utc)
    days_remaining = (expiry - now).total_seconds() / 86400.0
    if days_remaining < 0:
        return "critical", days_remaining
    if days_remaining < CONFIG["critical_if_expiring_within_days"]:
        return "critical", days_remaining
    if days_remaining < CONFIG["warn_if_expiring_within_days"]:
        return "warning", days_remaining
    return "ok", days_remaining


def main():
    """Run the SSL cert expiry check."""
    try:
        if not CONFIG["domains"]:
            send_output("*SSL Cert Expiry*\nNo domains configured.")
            return 1

        results = []
        exit_code = 0

        for domain in CONFIG["domains"]:
            expiry = fetch_not_after(domain)
            level, days_remaining = classify_expiry(expiry)
            if level != "ok":
                exit_code = 1
            results.append((domain, level, days_remaining, expiry))

        lines = [
            "*SSL Cert Expiry*",
            "",
            f"*Checked:* {len(results)} domain(s)",
            "",
        ]
        for domain, level, days_remaining, expiry in results:
            lines.append(
                f"- `{domain}`: {level} ({days_remaining:.1f} days, expires {expiry.date().isoformat()})"
            )

        send_output("\n".join(lines))
        return exit_code
    except Exception as exc:
        send_output(f"*SSL Cert Expiry Error*\n`{exc}`")
        return 2


if __name__ == "__main__":
    sys.exit(main())
