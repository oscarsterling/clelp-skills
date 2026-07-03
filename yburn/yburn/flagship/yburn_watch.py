#!/usr/bin/env python3
"""yburn-watch - Endpoint and uptime monitor.

Monitor multiple URLs for status, response time, and SSL certificate expiry.
Python 3.9+ stdlib only. Exit codes: 0 = all up, 1 = warnings, 2 = critical.
"""

import argparse
import datetime
import json
import logging
import os
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

__version__ = "1.0.0"

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Status constants
# ---------------------------------------------------------------------------

OK = "ok"
WARN = "warn"
CRITICAL = "critical"

ICON = {OK: "\u2705", WARN: "\u26a0\ufe0f", CRITICAL: "\U0001f534"}

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def _load_yaml_config(path: Path) -> Dict[str, Any]:
    """Try loading YAML config. Falls back to empty dict if PyYAML is absent."""
    if not path.is_file():
        return {}
    try:
        import yaml  # type: ignore[import-untyped]

        with open(path, "r") as fh:
            return yaml.safe_load(fh) or {}
    except ImportError:
        logger.debug("PyYAML not installed, skipping %s", path)
        return {}
    except Exception as exc:
        logger.warning("Failed to parse %s: %s", path, exc)
        return {}


def load_config() -> Dict[str, Any]:
    """Build merged config from YAML file and env vars.

    Env vars take precedence. Recognised env vars:
        YBURN_WATCH_URLS             - comma-separated URLs to monitor
        YBURN_WATCH_TIMEOUT          - request timeout in seconds (default 10)
        YBURN_WATCH_RESPONSE_WARN    - response time warning threshold ms (default 2000)
        YBURN_WATCH_SSL_WARN_DAYS    - SSL warning threshold days (default 14)
        YBURN_WATCH_SSL_CRIT_DAYS    - SSL critical threshold days (default 7)
        YBURN_WATCH_ALERT            - stdout | telegram | discord | slack
        YBURN_WATCH_TELEGRAM_TOKEN   - Telegram bot token
        YBURN_WATCH_TELEGRAM_CHAT    - Telegram chat ID
        YBURN_WATCH_DISCORD_WEBHOOK  - Discord webhook URL
        YBURN_WATCH_SLACK_WEBHOOK    - Slack webhook URL
    """
    cfg = _load_yaml_config(Path.home() / ".yburn" / "watch.yaml")

    if os.getenv("YBURN_WATCH_URLS"):
        urls_str = os.environ["YBURN_WATCH_URLS"]
        cfg["endpoints"] = [
            {"url": u.strip()} for u in urls_str.split(",") if u.strip()
        ]
    if os.getenv("YBURN_WATCH_TIMEOUT"):
        cfg["timeout"] = int(os.environ["YBURN_WATCH_TIMEOUT"])
    if os.getenv("YBURN_WATCH_RESPONSE_WARN"):
        cfg["response_warn_ms"] = int(os.environ["YBURN_WATCH_RESPONSE_WARN"])
    if os.getenv("YBURN_WATCH_SSL_WARN_DAYS"):
        cfg["ssl_warn_days"] = int(os.environ["YBURN_WATCH_SSL_WARN_DAYS"])
    if os.getenv("YBURN_WATCH_SSL_CRIT_DAYS"):
        cfg["ssl_crit_days"] = int(os.environ["YBURN_WATCH_SSL_CRIT_DAYS"])
    if os.getenv("YBURN_WATCH_ALERT"):
        cfg["alert"] = os.environ["YBURN_WATCH_ALERT"]
    for key in ("TELEGRAM_TOKEN", "TELEGRAM_CHAT", "DISCORD_WEBHOOK", "SLACK_WEBHOOK"):
        env = f"YBURN_WATCH_{key}"
        if os.getenv(env):
            cfg[key.lower()] = os.environ[env]

    cfg.setdefault("endpoints", [])
    cfg.setdefault("timeout", 10)
    cfg.setdefault("response_warn_ms", 2000)
    cfg.setdefault("ssl_warn_days", 14)
    cfg.setdefault("ssl_crit_days", 7)
    cfg.setdefault("alert", "stdout")
    return cfg


# ---------------------------------------------------------------------------
# Check result container
# ---------------------------------------------------------------------------


class EndpointResult:
    """Result of checking a single endpoint."""

    def __init__(
        self,
        url: str,
        status: str,
        status_code: Optional[int] = None,
        response_ms: Optional[int] = None,
        ssl_days: Optional[int] = None,
        error: Optional[str] = None,
        slow: bool = False,
    ):
        self.url = url
        self.status = status
        self.status_code = status_code
        self.response_ms = response_ms
        self.ssl_days = ssl_days
        self.error = error
        self.slow = slow

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "url": self.url,
            "status": self.status,
        }
        if self.status_code is not None:
            d["status_code"] = self.status_code
        if self.response_ms is not None:
            d["response_ms"] = self.response_ms
        if self.ssl_days is not None:
            d["ssl_days_remaining"] = self.ssl_days
        if self.error:
            d["error"] = self.error
        d["slow"] = self.slow
        return d

    def pretty(self) -> str:
        icon = ICON.get(self.status, "?")
        if self.error:
            return f"{icon} {self.url} - {self.error}"

        parts = [f"{icon} {self.url}"]
        if self.status_code is not None:
            reason = _http_reason(self.status_code)
            parts.append(f"- {self.status_code} {reason}")
        if self.response_ms is not None:
            parts.append(f"({self.response_ms}ms)")
        if self.slow:
            parts.append("SLOW")

        line = " ".join(parts)

        if self.ssl_days is not None:
            line += f"\n   SSL: valid, {self.ssl_days} days remaining"

        return line


def _http_reason(code: int) -> str:
    """Return a short reason phrase for common HTTP status codes."""
    reasons = {
        200: "OK", 201: "Created", 204: "No Content",
        301: "Moved", 302: "Found", 304: "Not Modified",
        400: "Bad Request", 401: "Unauthorized", 403: "Forbidden",
        404: "Not Found", 500: "Server Error", 502: "Bad Gateway",
        503: "Service Unavailable",
    }
    return reasons.get(code, "")


# ---------------------------------------------------------------------------
# SSL certificate check
# ---------------------------------------------------------------------------


def check_ssl_expiry(hostname: str, port: int = 443, timeout: int = 5) -> Optional[int]:
    """Return days until SSL cert expiry, or None if check fails.

    Args:
        hostname: The hostname to check.
        port: Port to connect on (default 443).
        timeout: Connection timeout in seconds.

    Returns:
        Days remaining until certificate expiry, or None on error.
    """
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((hostname, port), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=hostname) as ssock:
                cert = ssock.getpeercert()
                if not cert:
                    return None
                not_after = cert.get("notAfter", "")
                if not not_after:
                    return None
                # Format: "Sep 15 12:00:00 2025 GMT"
                expiry = datetime.datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z")
                now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
                delta = (expiry - now).days
                return delta
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Endpoint checking
# ---------------------------------------------------------------------------


def check_endpoint(
    url: str,
    expected_status: int = 200,
    timeout: int = 10,
    response_warn_ms: int = 2000,
    ssl_warn_days: int = 14,
    ssl_crit_days: int = 7,
) -> EndpointResult:
    """Check a single endpoint for status, response time, and SSL health.

    Args:
        url: The URL to check.
        expected_status: Expected HTTP status code.
        timeout: Request timeout in seconds.
        response_warn_ms: Response time warning threshold in ms.
        ssl_warn_days: SSL certificate warning threshold in days.
        ssl_crit_days: SSL certificate critical threshold in days.

    Returns:
        EndpointResult with check outcome.
    """
    start = time.time()
    try:
        req = urllib.request.Request(url, method="GET")
        req.add_header("User-Agent", f"yburn-watch/{__version__}")
        resp = urllib.request.urlopen(req, timeout=timeout)
        elapsed_ms = int((time.time() - start) * 1000)
        code = resp.getcode()
    except urllib.error.HTTPError as exc:
        elapsed_ms = int((time.time() - start) * 1000)
        code = exc.code
    except Exception as exc:
        error_msg = str(exc)
        # Simplify common errors
        if "Connection refused" in error_msg or "111" in error_msg:
            error_msg = "Connection refused"
        elif "timed out" in error_msg.lower() or "timeout" in error_msg.lower():
            error_msg = "Connection timed out"
        return EndpointResult(url=url, status=CRITICAL, error=error_msg)

    slow = elapsed_ms > response_warn_ms

    # Determine base status from HTTP code and speed
    if code != expected_status:
        status = CRITICAL
    elif slow:
        status = WARN
    else:
        status = OK

    # SSL check for HTTPS URLs
    ssl_days = None
    if url.lower().startswith("https://"):
        try:
            hostname = url.split("://")[1].split("/")[0].split(":")[0]
            ssl_days = check_ssl_expiry(hostname)
            if ssl_days is not None:
                if ssl_days < ssl_crit_days:
                    status = CRITICAL
                elif ssl_days < ssl_warn_days and status != CRITICAL:
                    status = WARN
        except Exception:
            pass

    return EndpointResult(
        url=url,
        status=status,
        status_code=code,
        response_ms=elapsed_ms,
        ssl_days=ssl_days,
        slow=slow,
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def run_checks(cfg: Optional[Dict[str, Any]] = None) -> Tuple[List[EndpointResult], int]:
    """Run all endpoint checks and return results with exit code.

    Args:
        cfg: Configuration dict. Loaded automatically if None.

    Returns:
        Tuple of (list of EndpointResult, exit_code).
    """
    if cfg is None:
        cfg = load_config()

    endpoints = cfg.get("endpoints", [])
    timeout = cfg.get("timeout", 10)
    response_warn_ms = cfg.get("response_warn_ms", 2000)
    ssl_warn_days = cfg.get("ssl_warn_days", 14)
    ssl_crit_days = cfg.get("ssl_crit_days", 7)

    results: List[EndpointResult] = []
    for ep in endpoints:
        if isinstance(ep, str):
            url = ep
            expected = 200
        else:
            url = ep.get("url", "")
            expected = ep.get("expected_status", 200)

        if not url:
            continue

        result = check_endpoint(
            url=url,
            expected_status=expected,
            timeout=timeout,
            response_warn_ms=response_warn_ms,
            ssl_warn_days=ssl_warn_days,
            ssl_crit_days=ssl_crit_days,
        )
        results.append(result)

    # Exit code
    has_critical = any(r.status == CRITICAL for r in results)
    has_warn = any(r.status == WARN for r in results)
    if has_critical:
        exit_code = 2
    elif has_warn:
        exit_code = 1
    else:
        exit_code = 0

    return results, exit_code


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------


def format_pretty(results: List[EndpointResult], exit_code: int) -> str:
    """Format results as pretty colored terminal output."""
    lines: List[str] = []
    lines.append(f"yburn-watch v{__version__}")
    lines.append("\u2501" * 32)

    if not results:
        lines.append("No endpoints configured.")
        lines.append("Set YBURN_WATCH_URLS or create ~/.yburn/watch.yaml")
    else:
        for r in results:
            lines.append(r.pretty())

    lines.append("\u2501" * 32)

    down = sum(1 for r in results if r.status == CRITICAL)
    slow = sum(1 for r in results if r.slow and r.status != CRITICAL)

    if exit_code == 0:
        if results:
            lines.append(f"Status: ALL UP ({len(results)} endpoints)")
        else:
            lines.append("Status: NO ENDPOINTS")
    elif exit_code == 1:
        parts = []
        if slow:
            parts.append(f"{slow} slow")
        lines.append(f"Status: WARNING ({', '.join(parts) if parts else 'alerts'})")
    else:
        parts = []
        if down:
            parts.append(f"{down} down")
        if slow:
            parts.append(f"{slow} slow")
        lines.append(f"Status: CRITICAL ({', '.join(parts)})")

    return "\n".join(lines)


def format_json(results: List[EndpointResult], exit_code: int) -> str:
    """Format results as JSON."""
    status_map = {0: "healthy", 1: "warning", 2: "critical"}
    output = {
        "version": __version__,
        "status": status_map.get(exit_code, "unknown"),
        "exit_code": exit_code,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "endpoints": [r.to_dict() for r in results],
    }
    return json.dumps(output, indent=2)


# ---------------------------------------------------------------------------
# Alert dispatch
# ---------------------------------------------------------------------------


def send_alert(text: str, cfg: Dict[str, Any]) -> None:
    """Send alert text via configured channel."""
    method = cfg.get("alert", "stdout")
    if method == "stdout":
        return

    try:
        if method == "telegram":
            token = cfg.get("telegram_token", "")
            chat_id = cfg.get("telegram_chat", "")
            if token and chat_id:
                url = f"https://api.telegram.org/bot{token}/sendMessage"
                payload = json.dumps({"chat_id": chat_id, "text": text}).encode()
                req = urllib.request.Request(
                    url, data=payload,
                    headers={"Content-Type": "application/json"},
                )
                urllib.request.urlopen(req, timeout=10)

        elif method == "discord":
            webhook = cfg.get("discord_webhook", "")
            if webhook:
                payload = json.dumps({"content": text}).encode()
                req = urllib.request.Request(
                    webhook, data=payload,
                    headers={"Content-Type": "application/json"},
                )
                urllib.request.urlopen(req, timeout=10)

        elif method == "slack":
            webhook = cfg.get("slack_webhook", "")
            if webhook:
                payload = json.dumps({"text": text}).encode()
                req = urllib.request.Request(
                    webhook, data=payload,
                    headers={"Content-Type": "application/json"},
                )
                urllib.request.urlopen(req, timeout=10)
    except Exception as exc:
        logger.warning("Alert dispatch to %s failed: %s", method, exc)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="yburn-watch",
        description="Endpoint and uptime monitor.",
    )
    parser.add_argument(
        "urls", nargs="*",
        help="URLs to monitor (overrides config if provided)",
    )
    parser.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output in JSON format",
    )
    parser.add_argument(
        "--timeout", type=int, default=None,
        help="Request timeout in seconds (default: 10)",
    )
    parser.add_argument(
        "--version", action="version", version=f"yburn-watch {__version__}",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point.

    Args:
        argv: Command-line arguments. Uses sys.argv if None.

    Returns:
        Exit code (0 = all up, 1 = warnings, 2 = critical).
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    cfg = load_config()

    # CLI URLs override config
    if args.urls:
        cfg["endpoints"] = [{"url": u} for u in args.urls]

    if args.timeout is not None:
        cfg["timeout"] = args.timeout

    results, exit_code = run_checks(cfg=cfg)

    if args.json_output:
        output = format_json(results, exit_code)
    else:
        output = format_pretty(results, exit_code)

    print(output)

    # Send alert if configured and not healthy
    if exit_code > 0 and cfg.get("alert", "stdout") != "stdout":
        alert_text = format_pretty(results, exit_code)
        send_alert(alert_text, cfg)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
