"""Tests for yburn-watch endpoint and uptime monitor."""

import datetime
import json
import os
import ssl
import urllib.error
from pathlib import Path
from unittest import mock

import pytest

from yburn.flagship.yburn_watch import (
    OK,
    WARN,
    CRITICAL,
    EndpointResult,
    build_parser,
    check_endpoint,
    check_ssl_expiry,
    format_json,
    format_pretty,
    load_config,
    main,
    run_checks,
    send_alert,
    _http_reason,
)


# ---------------------------------------------------------------------------
# EndpointResult
# ---------------------------------------------------------------------------


class TestEndpointResult:
    """Tests for EndpointResult container."""

    def test_to_dict_success(self):
        r = EndpointResult(
            url="https://example.com", status=OK,
            status_code=200, response_ms=150, ssl_days=45,
        )
        d = r.to_dict()
        assert d["url"] == "https://example.com"
        assert d["status"] == OK
        assert d["status_code"] == 200
        assert d["response_ms"] == 150
        assert d["ssl_days_remaining"] == 45
        assert d["slow"] is False

    def test_to_dict_error(self):
        r = EndpointResult(
            url="https://broken.com", status=CRITICAL,
            error="Connection refused",
        )
        d = r.to_dict()
        assert d["error"] == "Connection refused"
        assert "status_code" not in d

    def test_pretty_success(self):
        r = EndpointResult(
            url="https://example.com", status=OK,
            status_code=200, response_ms=150, ssl_days=45,
        )
        output = r.pretty()
        assert "\u2705" in output
        assert "https://example.com" in output
        assert "200 OK" in output
        assert "150ms" in output
        assert "45 days" in output

    def test_pretty_slow(self):
        r = EndpointResult(
            url="https://slow.com", status=WARN,
            status_code=200, response_ms=3400, slow=True,
        )
        output = r.pretty()
        assert "SLOW" in output
        assert "\u26a0" in output

    def test_pretty_error(self):
        r = EndpointResult(
            url="https://down.com", status=CRITICAL,
            error="Connection refused",
        )
        output = r.pretty()
        assert "\U0001f534" in output
        assert "Connection refused" in output


# ---------------------------------------------------------------------------
# HTTP reason helper
# ---------------------------------------------------------------------------


class TestHTTPReason:
    """Tests for HTTP reason phrase lookup."""

    def test_known_codes(self):
        assert _http_reason(200) == "OK"
        assert _http_reason(404) == "Not Found"
        assert _http_reason(500) == "Server Error"

    def test_unknown_code(self):
        assert _http_reason(418) == ""


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class TestConfig:
    """Tests for configuration loading."""

    def test_defaults(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            cfg = load_config()
        assert cfg["endpoints"] == []
        assert cfg["timeout"] == 10
        assert cfg["response_warn_ms"] == 2000
        assert cfg["ssl_warn_days"] == 14
        assert cfg["ssl_crit_days"] == 7
        assert cfg["alert"] == "stdout"

    def test_env_url_override(self):
        env = {"YBURN_WATCH_URLS": "https://a.com, https://b.com"}
        with mock.patch.dict(os.environ, env, clear=False):
            cfg = load_config()
        assert len(cfg["endpoints"]) == 2
        assert cfg["endpoints"][0]["url"] == "https://a.com"
        assert cfg["endpoints"][1]["url"] == "https://b.com"

    def test_env_numeric_overrides(self):
        env = {
            "YBURN_WATCH_TIMEOUT": "30",
            "YBURN_WATCH_RESPONSE_WARN": "5000",
            "YBURN_WATCH_SSL_WARN_DAYS": "21",
            "YBURN_WATCH_SSL_CRIT_DAYS": "3",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            cfg = load_config()
        assert cfg["timeout"] == 30
        assert cfg["response_warn_ms"] == 5000
        assert cfg["ssl_warn_days"] == 21
        assert cfg["ssl_crit_days"] == 3


# ---------------------------------------------------------------------------
# SSL expiry check
# ---------------------------------------------------------------------------


class TestSSLExpiry:
    """Tests for SSL certificate expiry checking."""

    def test_ssl_expiry_returns_days(self):
        future = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) + datetime.timedelta(days=30)
        cert = {"notAfter": future.strftime("%b %d %H:%M:%S %Y GMT")}

        mock_ssock = mock.MagicMock()
        mock_ssock.getpeercert.return_value = cert
        mock_ssock.__enter__ = mock.MagicMock(return_value=mock_ssock)
        mock_ssock.__exit__ = mock.MagicMock(return_value=False)

        mock_sock = mock.MagicMock()
        mock_sock.__enter__ = mock.MagicMock(return_value=mock_sock)
        mock_sock.__exit__ = mock.MagicMock(return_value=False)

        with mock.patch("socket.create_connection", return_value=mock_sock):
            with mock.patch("ssl.create_default_context") as mock_ctx:
                mock_ctx.return_value.wrap_socket.return_value = mock_ssock
                days = check_ssl_expiry("example.com")

        assert days is not None
        assert 29 <= days <= 31

    def test_ssl_expiry_connection_error(self):
        with mock.patch("socket.create_connection", side_effect=OSError("fail")):
            days = check_ssl_expiry("nonexistent.example.com")
        assert days is None

    def test_ssl_expiry_no_cert(self):
        mock_ssock = mock.MagicMock()
        mock_ssock.getpeercert.return_value = None
        mock_ssock.__enter__ = mock.MagicMock(return_value=mock_ssock)
        mock_ssock.__exit__ = mock.MagicMock(return_value=False)

        mock_sock = mock.MagicMock()
        mock_sock.__enter__ = mock.MagicMock(return_value=mock_sock)
        mock_sock.__exit__ = mock.MagicMock(return_value=False)

        with mock.patch("socket.create_connection", return_value=mock_sock):
            with mock.patch("ssl.create_default_context") as mock_ctx:
                mock_ctx.return_value.wrap_socket.return_value = mock_ssock
                days = check_ssl_expiry("example.com")

        assert days is None


# ---------------------------------------------------------------------------
# Endpoint checking
# ---------------------------------------------------------------------------


class TestCheckEndpoint:
    """Tests for endpoint checking logic."""

    def test_successful_check(self):
        mock_resp = mock.MagicMock()
        mock_resp.getcode.return_value = 200
        with mock.patch("urllib.request.urlopen", return_value=mock_resp):
            with mock.patch(
                "yburn.flagship.yburn_watch.check_ssl_expiry", return_value=45
            ):
                result = check_endpoint("https://example.com")
        assert result.status == OK
        assert result.status_code == 200
        assert result.ssl_days == 45

    def test_connection_refused(self):
        with mock.patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("Connection refused"),
        ):
            result = check_endpoint("http://localhost:9999")
        assert result.status == CRITICAL
        assert result.error is not None

    def test_wrong_status_code(self):
        mock_exc = urllib.error.HTTPError(
            url="https://example.com", code=500,
            msg="Server Error", hdrs=None, fp=None,
        )
        with mock.patch("urllib.request.urlopen", side_effect=mock_exc):
            result = check_endpoint("http://example.com", expected_status=200)
        assert result.status == CRITICAL
        assert result.status_code == 500

    def test_slow_response_warning(self):
        mock_resp = mock.MagicMock()
        mock_resp.getcode.return_value = 200

        def slow_open(*args, **kwargs):
            # Simulate slow by adjusting time
            return mock_resp

        with mock.patch("urllib.request.urlopen", side_effect=slow_open):
            with mock.patch("time.time", side_effect=[0.0, 3.5]):
                result = check_endpoint(
                    "http://example.com", response_warn_ms=2000,
                )
        assert result.slow is True
        assert result.status == WARN

    def test_ssl_cert_expiring_soon_critical(self):
        mock_resp = mock.MagicMock()
        mock_resp.getcode.return_value = 200
        with mock.patch("urllib.request.urlopen", return_value=mock_resp):
            with mock.patch(
                "yburn.flagship.yburn_watch.check_ssl_expiry", return_value=3
            ):
                result = check_endpoint(
                    "https://example.com",
                    ssl_warn_days=14, ssl_crit_days=7,
                )
        assert result.status == CRITICAL
        assert result.ssl_days == 3

    def test_ssl_cert_expiring_warning(self):
        mock_resp = mock.MagicMock()
        mock_resp.getcode.return_value = 200
        with mock.patch("urllib.request.urlopen", return_value=mock_resp):
            with mock.patch(
                "yburn.flagship.yburn_watch.check_ssl_expiry", return_value=10
            ):
                result = check_endpoint(
                    "https://example.com",
                    ssl_warn_days=14, ssl_crit_days=7,
                )
        assert result.status == WARN

    def test_http_url_skips_ssl(self):
        mock_resp = mock.MagicMock()
        mock_resp.getcode.return_value = 200
        with mock.patch("urllib.request.urlopen", return_value=mock_resp):
            result = check_endpoint("http://example.com")
        assert result.ssl_days is None

    def test_custom_expected_status(self):
        mock_exc = urllib.error.HTTPError(
            url="http://example.com", code=301,
            msg="Moved", hdrs=None, fp=None,
        )
        with mock.patch("urllib.request.urlopen", side_effect=mock_exc):
            result = check_endpoint("http://example.com", expected_status=301)
        assert result.status == OK
        assert result.status_code == 301


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


class TestRunChecks:
    """Tests for the main runner."""

    def test_empty_endpoints(self):
        cfg = {"endpoints": [], "timeout": 10, "response_warn_ms": 2000,
               "ssl_warn_days": 14, "ssl_crit_days": 7, "alert": "stdout"}
        results, exit_code = run_checks(cfg=cfg)
        assert results == []
        assert exit_code == 0

    def test_all_up(self):
        cfg = {
            "endpoints": [{"url": "http://a.com"}, {"url": "http://b.com"}],
            "timeout": 10, "response_warn_ms": 2000,
            "ssl_warn_days": 14, "ssl_crit_days": 7, "alert": "stdout",
        }
        ok_result = EndpointResult(url="http://a.com", status=OK, status_code=200)
        with mock.patch(
            "yburn.flagship.yburn_watch.check_endpoint", return_value=ok_result,
        ):
            results, exit_code = run_checks(cfg=cfg)
        assert exit_code == 0

    def test_one_down_is_critical(self):
        cfg = {
            "endpoints": [{"url": "http://a.com"}],
            "timeout": 10, "response_warn_ms": 2000,
            "ssl_warn_days": 14, "ssl_crit_days": 7, "alert": "stdout",
        }
        crit = EndpointResult(url="http://a.com", status=CRITICAL, error="refused")
        with mock.patch(
            "yburn.flagship.yburn_watch.check_endpoint", return_value=crit,
        ):
            results, exit_code = run_checks(cfg=cfg)
        assert exit_code == 2

    def test_string_endpoints(self):
        """Endpoints can be plain strings instead of dicts."""
        cfg = {
            "endpoints": ["http://a.com", "http://b.com"],
            "timeout": 10, "response_warn_ms": 2000,
            "ssl_warn_days": 14, "ssl_crit_days": 7, "alert": "stdout",
        }
        ok_result = EndpointResult(url="http://a.com", status=OK, status_code=200)
        with mock.patch(
            "yburn.flagship.yburn_watch.check_endpoint", return_value=ok_result,
        ):
            results, exit_code = run_checks(cfg=cfg)
        assert len(results) == 2


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------


class TestFormatting:
    """Tests for output formatting."""

    def test_pretty_all_up(self):
        results = [
            EndpointResult(url="https://a.com", status=OK, status_code=200, response_ms=100),
        ]
        output = format_pretty(results, 0)
        assert "yburn-watch v" in output
        assert "ALL UP" in output

    def test_pretty_no_endpoints(self):
        output = format_pretty([], 0)
        assert "No endpoints configured" in output

    def test_pretty_critical(self):
        results = [
            EndpointResult(url="https://down.com", status=CRITICAL, error="refused"),
        ]
        output = format_pretty(results, 2)
        assert "CRITICAL" in output
        assert "1 down" in output

    def test_pretty_slow_warning(self):
        results = [
            EndpointResult(
                url="https://slow.com", status=WARN,
                status_code=200, response_ms=3000, slow=True,
            ),
        ]
        output = format_pretty(results, 1)
        assert "WARNING" in output
        assert "1 slow" in output

    def test_json_format_valid(self):
        results = [
            EndpointResult(url="https://a.com", status=OK, status_code=200, response_ms=100),
        ]
        output = format_json(results, 0)
        data = json.loads(output)
        assert data["version"] == "1.0.0"
        assert data["status"] == "healthy"
        assert data["exit_code"] == 0
        assert len(data["endpoints"]) == 1
        assert "timestamp" in data

    def test_json_critical(self):
        results = [
            EndpointResult(url="https://x.com", status=CRITICAL, error="down"),
        ]
        output = format_json(results, 2)
        data = json.loads(output)
        assert data["status"] == "critical"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestCLI:
    """Tests for CLI argument parsing and main()."""

    def test_parser_defaults(self):
        parser = build_parser()
        args = parser.parse_args([])
        assert args.urls == []
        assert not args.json_output
        assert args.timeout is None

    def test_parser_with_urls(self):
        parser = build_parser()
        args = parser.parse_args(["https://a.com", "https://b.com"])
        assert args.urls == ["https://a.com", "https://b.com"]

    def test_parser_json_flag(self):
        parser = build_parser()
        args = parser.parse_args(["--json"])
        assert args.json_output

    def test_parser_timeout(self):
        parser = build_parser()
        args = parser.parse_args(["--timeout", "30"])
        assert args.timeout == 30

    def test_main_no_endpoints(self, capsys):
        with mock.patch.dict(os.environ, {}, clear=True):
            code = main([])
        captured = capsys.readouterr()
        assert "No endpoints configured" in captured.out
        assert code == 0

    def test_main_with_urls(self, capsys):
        ok_result = EndpointResult(url="http://a.com", status=OK, status_code=200)
        with mock.patch(
            "yburn.flagship.yburn_watch.check_endpoint", return_value=ok_result,
        ):
            code = main(["http://a.com"])
        assert code == 0

    def test_main_json_output(self, capsys):
        ok_result = EndpointResult(url="http://a.com", status=OK, status_code=200)
        with mock.patch(
            "yburn.flagship.yburn_watch.check_endpoint", return_value=ok_result,
        ):
            main(["--json", "http://a.com"])
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "endpoints" in data

    def test_main_returns_critical_code(self):
        crit = EndpointResult(url="http://a.com", status=CRITICAL, error="down")
        with mock.patch(
            "yburn.flagship.yburn_watch.check_endpoint", return_value=crit,
        ):
            code = main(["http://a.com"])
        assert code == 2


# ---------------------------------------------------------------------------
# Alert dispatch
# ---------------------------------------------------------------------------


class TestAlertDispatch:
    """Tests for alert sending."""

    def test_stdout_does_nothing(self):
        send_alert("test", {"alert": "stdout"})

    def test_telegram_dispatch(self):
        cfg = {
            "alert": "telegram",
            "telegram_token": "tok",
            "telegram_chat": "chat",
        }
        with mock.patch("urllib.request.urlopen") as mock_open:
            send_alert("test", cfg)
        mock_open.assert_called_once()

    def test_discord_dispatch(self):
        cfg = {
            "alert": "discord",
            "discord_webhook": "https://discord.com/test",
        }
        with mock.patch("urllib.request.urlopen") as mock_open:
            send_alert("test", cfg)
        mock_open.assert_called_once()

    def test_slack_dispatch(self):
        cfg = {
            "alert": "slack",
            "slack_webhook": "https://hooks.slack.com/test",
        }
        with mock.patch("urllib.request.urlopen") as mock_open:
            send_alert("test", cfg)
        mock_open.assert_called_once()

    def test_alert_failure_handled(self):
        cfg = {"alert": "telegram", "telegram_token": "t", "telegram_chat": "c"}
        with mock.patch("urllib.request.urlopen", side_effect=Exception("fail")):
            send_alert("test", cfg)  # should not raise
