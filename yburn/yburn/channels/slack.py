"""Slack webhook output channel for yburn."""

import json
import logging
import time
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)


def send_slack(message: str, webhook_url: str) -> bool:
    """Send a message to a Slack incoming webhook, retrying once on failure."""
    if not message or not message.strip():
        logger.warning("Attempted to send empty Slack message")
        return False
    if not webhook_url:
        logger.warning("Slack webhook URL not configured")
        return False

    payload = json.dumps({"text": message}).encode("utf-8")
    request = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json"},
    )

    for attempt in range(2):
        try:
            response = urllib.request.urlopen(request, timeout=30)
            body = response.read().decode("utf-8", errors="replace").strip()
            if 200 <= response.status < 300:
                return True
            logger.error("Slack webhook failed with status %s: %s", response.status, body[:200])
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            logger.error("Slack HTTP error %s: %s", exc.code, body[:200])
        except urllib.error.URLError as exc:
            logger.error("Slack connection error: %s", exc.reason)
        except Exception as exc:
            logger.error("Slack send error: %s", exc)

        if attempt == 0:
            time.sleep(1)

    return False
