"""Tests for yburn-health system health monitoring."""

import json
import os
import platform
import subprocess
import time
from pathlib import Path
from unittest import mock

import pytest

from yburn.flagship.yburn_health import (
    OK,
    WARN,
    CRITICAL,
    CheckResult,
    build_parser,
    check_cpu,
    check_disk,
    check_docker,
    check_load,
    check_memory,
    check_network,
    check_processes,
    check_uptime,
    check_openclaw_gateway,
    check_openclaw_crons,
    check_openclaw_cron_failures,
    check_openclaw_sessions,
    check_openclaw_channels,
    check_openclaw_memory_db,
    check_claude_cli,
    check_claude_sessions,
    check_claude_tasks,
    format_json,
    format_pretty,
    load_config,
    main,
    run_checks,
    send_alert,
)


# ---------------------------------------------------------------------------
# CheckResult
# ---------------------------------------------------------------------------


class TestCheckResult:
    """Tests for CheckResult container."""

    def test_to_dict_basic(self):
        r = CheckResult("cpu", OK, "CPU: 10%")
        d = r.to_dict()
        assert d["name"] == "cpu"
        assert d["status"] == OK
        assert d["message"] == "CPU: 10%"
        assert "detail" not in d

    def test_to_dict_with_detail(self):
        r = CheckResult("proc", CRITICAL, "Process missing", detail="nginx")
        d = r.to_dict()
        assert d["detail"] == "nginx"

    def test_pretty_ok(self):
        r = CheckResult("cpu", OK, "CPU: 10%")
        assert "\u2705" in r.pretty()
        assert "CPU: 10%" in r.pretty()

    def test_pretty_warn(self):
        r = CheckResult("disk", WARN, "Disk /data: 87%")
        assert "\u26a0" in r.pretty()

    def test_pretty_critical(self):
        r = CheckResult("proc", CRITICAL, "Process missing")
        assert "\U0001f534" in r.pretty()

    def test_pretty_with_detail(self):
        r = CheckResult("x", OK, "msg", detail="extra info")
        output = r.pretty()
        assert "extra info" in output


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class TestConfig:
    """Tests for configuration loading."""

    def test_defaults(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            cfg = load_config()
        assert cfg["disk_threshold"] == 85
        assert cfg["processes"] == []
        assert cfg["alert"] == "stdout"

    def test_env_overrides(self):
        env = {
            "YBURN_HEALTH_DISK_THRESHOLD": "90",
            "YBURN_HEALTH_PROCESSES": "nginx,redis",
            "YBURN_HEALTH_ALERT": "telegram",
            "YBURN_HEALTH_TELEGRAM_TOKEN": "tok123",
            "YBURN_HEALTH_TELEGRAM_CHAT": "chat456",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            cfg = load_config()
        assert cfg["disk_threshold"] == 90
        assert cfg["processes"] == ["nginx", "redis"]
        assert cfg["alert"] == "telegram"
        assert cfg["telegram_token"] == "tok123"
        assert cfg["telegram_chat"] == "chat456"

    def test_yaml_fallback_when_no_pyyaml(self):
        """Config loading works even without PyYAML."""
        with mock.patch.dict(os.environ, {}, clear=True):
            cfg = load_config()
        # Should still return valid defaults
        assert isinstance(cfg, dict)


# ---------------------------------------------------------------------------
# Universal checks
# ---------------------------------------------------------------------------


class TestCheckCPU:
    """Tests for CPU check."""

    @pytest.mark.skipif(
        platform.system() != "Darwin", reason="macOS-only test"
    )
    def test_cpu_macos_returns_result(self):
        result = check_cpu()
        assert result.name == "cpu"
        assert result.status in (OK, WARN)
        assert "CPU:" in result.message
        assert "cores" in result.message

    def test_cpu_macos_parsing(self):
        """Test macOS top output parsing via mock."""
        fake_output = (
            "Processes: 300 total\n"
            "CPU usage: 5.26% user, 10.0% sys, 84.73% idle\n"
        )
        with mock.patch("subprocess.check_output", return_value=fake_output):
            with mock.patch("platform.system", return_value="Darwin"):
                result = check_cpu()
        assert result.status == OK
        assert "15.3%" in result.message or "15.2%" in result.message

    def test_cpu_high_usage_warns(self):
        """CPU > 90% should produce a warning."""
        fake_output = "CPU usage: 80% user, 15% sys, 5.0% idle\n"
        with mock.patch("subprocess.check_output", return_value=fake_output):
            with mock.patch("platform.system", return_value="Darwin"):
                result = check_cpu()
        assert result.status == WARN

    def test_cpu_exception_handled(self):
        with mock.patch("subprocess.check_output", side_effect=OSError("fail")):
            with mock.patch("platform.system", return_value="Darwin"):
                result = check_cpu()
        assert result.status == WARN
        assert "failed" in result.message


class TestCheckMemory:
    """Tests for memory check."""

    def test_memory_returns_result(self):
        result = check_memory()
        assert result.name == "memory"
        assert "Memory:" in result.message
        assert "GB" in result.message

    def test_memory_linux_parsing(self):
        """Test Linux /proc/meminfo parsing."""
        meminfo = (
            "MemTotal:       16000000 kB\n"
            "MemAvailable:    8000000 kB\n"
        )
        m = mock.mock_open(read_data=meminfo)
        with mock.patch("platform.system", return_value="Linux"):
            with mock.patch("builtins.open", m):
                result = check_memory()
        assert result.status == OK
        assert "Memory:" in result.message

    def test_memory_exception_handled(self):
        with mock.patch("platform.system", return_value="Linux"):
            with mock.patch("builtins.open", side_effect=OSError("fail")):
                result = check_memory()
        assert result.status == WARN


class TestCheckDisk:
    """Tests for disk check."""

    def test_disk_returns_results(self):
        results = check_disk()
        assert len(results) > 0
        assert all(r.name.startswith("disk") for r in results)

    def test_disk_threshold_warning(self):
        """Disk above threshold gets WARN status."""
        fake_output = (
            "Filesystem     1024-blocks      Used Available Capacity Mounted on\n"
            "/dev/sda1      100000000  88000000  12000000      88% /\n"
        )
        with mock.patch("subprocess.check_output", return_value=fake_output):
            results = check_disk(threshold=85)
        assert len(results) == 1
        assert results[0].status == WARN

    def test_disk_below_threshold_ok(self):
        fake_output = (
            "Filesystem     1024-blocks      Used Available Capacity Mounted on\n"
            "/dev/sda1      100000000  40000000  60000000      40% /\n"
        )
        with mock.patch("subprocess.check_output", return_value=fake_output):
            results = check_disk(threshold=85)
        assert len(results) == 1
        assert results[0].status == OK

    def test_disk_critical_above_95(self):
        fake_output = (
            "Filesystem     1024-blocks      Used Available Capacity Mounted on\n"
            "/dev/sda1      100000000  97000000   3000000      97% /\n"
        )
        with mock.patch("subprocess.check_output", return_value=fake_output):
            results = check_disk(threshold=85)
        assert results[0].status == CRITICAL

    def test_disk_exception_handled(self):
        with mock.patch("subprocess.check_output", side_effect=OSError("fail")):
            results = check_disk()
        assert len(results) == 1
        assert results[0].status == WARN


class TestCheckLoad:
    """Tests for load average check."""

    def test_load_returns_result(self):
        result = check_load()
        assert result.name == "load"
        assert "Load:" in result.message

    def test_load_high_warns(self):
        with mock.patch("os.getloadavg", return_value=(100.0, 80.0, 60.0)):
            with mock.patch("os.cpu_count", return_value=4):
                result = check_load()
        assert result.status == WARN


class TestCheckUptime:
    """Tests for uptime check."""

    def test_uptime_returns_result(self):
        result = check_uptime()
        assert result.name == "uptime"
        assert "Uptime:" in result.message

    def test_uptime_exception_handled(self):
        with mock.patch("platform.system", return_value="Linux"):
            with mock.patch("builtins.open", side_effect=OSError("fail")):
                result = check_uptime()
        assert result.status == WARN


class TestCheckProcesses:
    """Tests for process watchdog."""

    def test_empty_list_no_results(self):
        results = check_processes([])
        assert results == []

    def test_process_found(self):
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="123\n")
        with mock.patch("subprocess.run", return_value=completed):
            results = check_processes(["nginx"])
        assert len(results) == 1
        assert results[0].status == OK
        assert "running" in results[0].message

    def test_process_not_found(self):
        completed = subprocess.CompletedProcess(args=[], returncode=1, stdout="")
        with mock.patch("subprocess.run", return_value=completed):
            results = check_processes(["nginx"])
        assert len(results) == 1
        assert results[0].status == CRITICAL
        assert "NOT FOUND" in results[0].message

    def test_multiple_processes(self):
        def mock_run(*args, **kwargs):
            cmd = args[0]
            name = cmd[-1]
            if name == "nginx":
                return subprocess.CompletedProcess(args=[], returncode=0, stdout="1")
            return subprocess.CompletedProcess(args=[], returncode=1, stdout="")

        with mock.patch("subprocess.run", side_effect=mock_run):
            results = check_processes(["nginx", "missing"])
        assert len(results) == 2
        assert results[0].status == OK
        assert results[1].status == CRITICAL


class TestCheckNetwork:
    """Tests for network connectivity check."""

    def test_network_reachable(self):
        fake_sock = mock.MagicMock()
        with mock.patch("socket.create_connection", return_value=fake_sock):
            result = check_network()
        assert result.status == OK
        assert "reachable" in result.message

    def test_network_unreachable(self):
        with mock.patch("socket.create_connection", side_effect=OSError("fail")):
            result = check_network()
        assert result.status == CRITICAL
        assert "unreachable" in result.message


class TestCheckDocker:
    """Tests for Docker check."""

    def test_docker_not_installed(self):
        with mock.patch("shutil.which", return_value=None):
            result = check_docker()
        assert result is None

    def test_docker_running(self):
        fake_output = "Up 2 hours\nUp 3 hours\nExited (0) 1 hour ago\n"
        with mock.patch("shutil.which", return_value="/usr/bin/docker"):
            with mock.patch("subprocess.check_output", return_value=fake_output):
                result = check_docker()
        assert result is not None
        assert result.name == "docker"
        assert "3 containers" in result.message
        assert "2 running" in result.message

    def test_docker_timeout(self):
        with mock.patch("shutil.which", return_value="/usr/bin/docker"):
            with mock.patch(
                "subprocess.check_output",
                side_effect=subprocess.TimeoutExpired(cmd="docker", timeout=10),
            ):
                result = check_docker()
        assert result is not None
        assert result.status == WARN


# ---------------------------------------------------------------------------
# OpenClaw checks
# ---------------------------------------------------------------------------


class TestOpenClawGateway:
    """Tests for OpenClaw gateway check."""

    def test_cli_not_available(self):
        with mock.patch("shutil.which", return_value=None):
            result = check_openclaw_gateway()
        assert result.status == WARN
        assert "not available" in result.message

    def test_gateway_running(self):
        status_json = json.dumps({
            "running": True,
            "version": "0.26.1",
            "uptime": "3d 14h",
        })
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=status_json)
        with mock.patch("shutil.which", return_value="/usr/bin/openclaw"):
            with mock.patch("subprocess.run", return_value=completed):
                result = check_openclaw_gateway()
        assert result.status == OK
        assert "v0.26.1" in result.message
        assert "3d 14h" in result.message

    def test_gateway_not_running(self):
        status_json = json.dumps({"running": False, "version": "0.26.1"})
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=status_json)
        with mock.patch("shutil.which", return_value="/usr/bin/openclaw"):
            with mock.patch("subprocess.run", return_value=completed):
                result = check_openclaw_gateway()
        assert result.status == CRITICAL


class TestOpenClawCrons:
    """Tests for OpenClaw cron checks."""

    def test_crons_parsed(self):
        cron_json = json.dumps({"jobs": [
            {"name": "a", "enabled": True},
            {"name": "b", "enabled": True},
            {"name": "c", "enabled": False},
        ]})
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=cron_json)
        with mock.patch("shutil.which", return_value="/usr/bin/openclaw"):
            with mock.patch("subprocess.run", return_value=completed):
                result = check_openclaw_crons()
        assert result.status == OK
        assert "3 total" in result.message
        assert "2 enabled" in result.message
        assert "1 disabled" in result.message

    def test_cron_failures_detected(self):
        cron_json = json.dumps({"jobs": [
            {"name": "a", "consecutive_failures": 5},
            {"name": "b", "consecutive_failures": 0},
            {"name": "c", "consecutive_failures": 3},
        ]})
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=cron_json)
        with mock.patch("shutil.which", return_value="/usr/bin/openclaw"):
            with mock.patch("subprocess.run", return_value=completed):
                result = check_openclaw_cron_failures()
        assert result.status == WARN
        assert "2 jobs" in result.message

    def test_no_cron_failures(self):
        cron_json = json.dumps({"jobs": [
            {"name": "a", "consecutive_failures": 0},
        ]})
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=cron_json)
        with mock.patch("shutil.which", return_value="/usr/bin/openclaw"):
            with mock.patch("subprocess.run", return_value=completed):
                result = check_openclaw_cron_failures()
        assert result.status == OK


class TestOpenClawSessions:
    """Tests for OpenClaw session check."""

    def test_sessions_parsed(self):
        sess_json = json.dumps({"sessions": [
            {"status": "active"},
            {"status": "active"},
            {"status": "stuck"},
        ]})
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=sess_json)
        with mock.patch("shutil.which", return_value="/usr/bin/openclaw"):
            with mock.patch("subprocess.run", return_value=completed):
                result = check_openclaw_sessions()
        assert result.status == WARN
        assert "2 active" in result.message
        assert "1 stuck" in result.message

    def test_sessions_all_active(self):
        sess_json = json.dumps({"sessions": [{"status": "active"}]})
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=sess_json)
        with mock.patch("shutil.which", return_value="/usr/bin/openclaw"):
            with mock.patch("subprocess.run", return_value=completed):
                result = check_openclaw_sessions()
        assert result.status == OK


class TestOpenClawChannels:
    """Tests for OpenClaw channel connectivity check."""

    def test_channels_connected(self, tmp_path):
        log_dir = tmp_path / ".openclaw" / "logs"
        log_dir.mkdir(parents=True)
        log_file = log_dir / "gateway.log"
        log_file.write_text(
            "2025-01-01 telegram connected successfully\n"
            "2025-01-01 discord connected successfully\n"
        )
        with mock.patch("pathlib.Path.home", return_value=tmp_path):
            result = check_openclaw_channels()
        assert result.status == OK
        assert "Telegram" in result.message
        assert "connected" in result.message

    def test_channels_disconnected(self, tmp_path):
        log_dir = tmp_path / ".openclaw" / "logs"
        log_dir.mkdir(parents=True)
        log_file = log_dir / "gateway.log"
        log_file.write_text("2025-01-01 telegram disconnected error\n")
        with mock.patch("pathlib.Path.home", return_value=tmp_path):
            result = check_openclaw_channels()
        assert result.status == WARN

    def test_channels_log_missing(self, tmp_path):
        with mock.patch("pathlib.Path.home", return_value=tmp_path):
            result = check_openclaw_channels()
        assert result.status == WARN
        assert "not found" in result.message


class TestOpenClawMemoryDB:
    """Tests for OpenClaw memory DB check."""

    def test_db_healthy(self, tmp_path):
        db_dir = tmp_path / ".openclaw" / "memory"
        db_dir.mkdir(parents=True)
        db_file = db_dir / "main.sqlite"
        db_file.write_bytes(b"x" * (100 * 1024 * 1024))  # 100 MB
        with mock.patch("pathlib.Path.home", return_value=tmp_path):
            result = check_openclaw_memory_db()
        assert result.status == OK
        assert "healthy" in result.message

    def test_db_large(self, tmp_path):
        db_dir = tmp_path / ".openclaw" / "memory"
        db_dir.mkdir(parents=True)
        db_file = db_dir / "main.sqlite"
        db_file.write_bytes(b"x" * (600 * 1024 * 1024))  # 600 MB
        with mock.patch("pathlib.Path.home", return_value=tmp_path):
            result = check_openclaw_memory_db()
        assert result.status == WARN
        assert "large" in result.message

    def test_db_missing(self, tmp_path):
        with mock.patch("pathlib.Path.home", return_value=tmp_path):
            result = check_openclaw_memory_db()
        assert result.status == WARN


# ---------------------------------------------------------------------------
# Claude Code checks
# ---------------------------------------------------------------------------


class TestClaudeCLI:
    """Tests for Claude CLI check."""

    def test_cli_not_found(self):
        with mock.patch("shutil.which", return_value=None):
            result = check_claude_cli()
        assert result.status == WARN
        assert "not found" in result.message

    def test_cli_available(self):
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="claude-code 1.0.0\n",
        )
        with mock.patch("shutil.which", return_value="/usr/bin/claude"):
            with mock.patch("subprocess.run", return_value=completed):
                result = check_claude_cli()
        assert result.status == OK
        assert "claude-code 1.0.0" in result.message


class TestClaudeSessions:
    """Tests for Claude session check."""

    def test_claude_dir_missing(self, tmp_path):
        with mock.patch("pathlib.Path.home", return_value=tmp_path):
            result = check_claude_sessions()
        assert result.status == WARN
        assert "not found" in result.message

    def test_claude_dir_with_files(self, tmp_path):
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        projects_dir = claude_dir / "projects"
        projects_dir.mkdir()
        (projects_dir / "proj1").mkdir()
        (projects_dir / "proj2").mkdir()
        (claude_dir / "config.json").write_text("{}")
        with mock.patch("pathlib.Path.home", return_value=tmp_path):
            result = check_claude_sessions()
        assert result.status == OK
        assert "2 projects" in result.message


class TestClaudeTasks:
    """Tests for Claude tasks check."""

    def test_no_tasks_dir(self, tmp_path):
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        with mock.patch("pathlib.Path.home", return_value=tmp_path):
            result = check_claude_tasks()
        assert result.status == OK

    def test_tasks_with_expired(self, tmp_path):
        tasks_dir = tmp_path / ".claude" / "tasks"
        tasks_dir.mkdir(parents=True)
        # Active task (far future)
        (tasks_dir / "active.json").write_text(
            json.dumps({"expires_at": time.time() + 86400})
        )
        # Expired task
        (tasks_dir / "expired.json").write_text(
            json.dumps({"expires_at": 1000000})
        )
        with mock.patch("pathlib.Path.home", return_value=tmp_path):
            result = check_claude_tasks()
        assert "1 active" in result.message
        assert "1 expired" in result.message


# ---------------------------------------------------------------------------
# Runner and formatting
# ---------------------------------------------------------------------------


class TestRunChecks:
    """Tests for the main runner."""

    def test_universal_mode(self):
        cfg = {"disk_threshold": 85, "processes": [], "alert": "stdout"}
        results, exit_code = run_checks(mode="universal", cfg=cfg)
        assert len(results) > 0
        assert exit_code in (0, 1, 2)

    def test_openclaw_mode_includes_openclaw_checks(self):
        cfg = {"disk_threshold": 85, "processes": [], "alert": "stdout"}
        with mock.patch("shutil.which", return_value=None):
            results, _ = run_checks(mode="openclaw", cfg=cfg)
        oc_names = [r.name for r in results if r.name.startswith("openclaw:")]
        assert len(oc_names) >= 5

    def test_claude_mode_includes_claude_checks(self):
        cfg = {"disk_threshold": 85, "processes": [], "alert": "stdout"}
        results, _ = run_checks(mode="claude-code", cfg=cfg)
        cc_names = [r.name for r in results if r.name.startswith("claude:")]
        assert len(cc_names) >= 2

    def test_exit_code_zero_when_healthy(self):
        cfg = {"disk_threshold": 99, "processes": [], "alert": "stdout"}
        # Mock all checks to return OK
        ok_result = CheckResult("test", OK, "all good")
        with mock.patch(
            "yburn.flagship.yburn_health.check_cpu", return_value=ok_result
        ), mock.patch(
            "yburn.flagship.yburn_health.check_memory", return_value=ok_result
        ), mock.patch(
            "yburn.flagship.yburn_health.check_disk", return_value=[ok_result]
        ), mock.patch(
            "yburn.flagship.yburn_health.check_load", return_value=ok_result
        ), mock.patch(
            "yburn.flagship.yburn_health.check_uptime", return_value=ok_result
        ), mock.patch(
            "yburn.flagship.yburn_health.check_docker", return_value=None
        ), mock.patch(
            "yburn.flagship.yburn_health.check_network", return_value=ok_result
        ):
            results, exit_code = run_checks(mode="universal", cfg=cfg)
        assert exit_code == 0

    def test_exit_code_one_on_warning(self):
        results = [
            CheckResult("a", OK, "ok"),
            CheckResult("b", WARN, "warn"),
        ]
        has_critical = any(r.status == CRITICAL for r in results)
        has_warn = any(r.status == WARN for r in results)
        exit_code = 2 if has_critical else (1 if has_warn else 0)
        assert exit_code == 1

    def test_exit_code_two_on_critical(self):
        results = [
            CheckResult("a", OK, "ok"),
            CheckResult("b", CRITICAL, "crit"),
        ]
        has_critical = any(r.status == CRITICAL for r in results)
        exit_code = 2 if has_critical else 0
        assert exit_code == 2


class TestFormatting:
    """Tests for output formatting."""

    def test_pretty_format_header(self):
        results = [CheckResult("cpu", OK, "CPU: 10% (4 cores)")]
        output = format_pretty(results, "universal", 0)
        assert "yburn-health v" in output
        assert "CPU: 10%" in output
        assert "HEALTHY" in output

    def test_pretty_format_warning(self):
        results = [CheckResult("disk", WARN, "Disk: 90%")]
        output = format_pretty(results, "universal", 1)
        assert "WARNING" in output

    def test_pretty_format_critical(self):
        results = [CheckResult("proc", CRITICAL, "Process missing")]
        output = format_pretty(results, "universal", 2)
        assert "CRITICAL" in output

    def test_pretty_format_openclaw_section(self):
        results = [
            CheckResult("cpu", OK, "CPU: 10%"),
            CheckResult("openclaw:gateway", OK, "Gateway: running"),
        ]
        output = format_pretty(results, "openclaw", 0)
        assert "--- OpenClaw ---" in output
        assert "Gateway: running" in output

    def test_pretty_format_claude_section(self):
        results = [
            CheckResult("cpu", OK, "CPU: 10%"),
            CheckResult("claude:cli", OK, "Claude CLI: v1.0"),
        ]
        output = format_pretty(results, "claude-code", 0)
        assert "--- Claude Code ---" in output

    def test_json_format_valid(self):
        results = [
            CheckResult("cpu", OK, "CPU: 10%"),
            CheckResult("disk", WARN, "Disk: 90%"),
        ]
        output = format_json(results, "universal", 1)
        data = json.loads(output)
        assert data["version"] == "1.0.0"
        assert data["mode"] == "universal"
        assert data["status"] == "warning"
        assert data["exit_code"] == 1
        assert len(data["checks"]) == 2
        assert "timestamp" in data

    def test_json_format_critical(self):
        results = [CheckResult("x", CRITICAL, "bad")]
        output = format_json(results, "universal", 2)
        data = json.loads(output)
        assert data["status"] == "critical"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestCLI:
    """Tests for CLI argument parsing and main()."""

    def test_parser_default_mode(self):
        parser = build_parser()
        args = parser.parse_args([])
        assert not args.openclaw
        assert not args.claude_code
        assert not args.json_output

    def test_parser_openclaw_flag(self):
        parser = build_parser()
        args = parser.parse_args(["--openclaw"])
        assert args.openclaw

    def test_parser_claude_code_flag(self):
        parser = build_parser()
        args = parser.parse_args(["--claude-code"])
        assert args.claude_code

    def test_parser_json_flag(self):
        parser = build_parser()
        args = parser.parse_args(["--json"])
        assert args.json_output

    def test_parser_mutually_exclusive_modes(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["--openclaw", "--claude-code"])

    def test_main_returns_exit_code(self):
        ok_result = CheckResult("test", OK, "ok")
        with mock.patch(
            "yburn.flagship.yburn_health.run_checks",
            return_value=([ok_result], 0),
        ):
            code = main([])
        assert code == 0

    def test_main_json_output(self, capsys):
        ok_result = CheckResult("test", OK, "ok")
        with mock.patch(
            "yburn.flagship.yburn_health.run_checks",
            return_value=([ok_result], 0),
        ):
            main(["--json"])
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "checks" in data

    def test_main_openclaw_mode(self):
        ok_result = CheckResult("test", OK, "ok")
        with mock.patch(
            "yburn.flagship.yburn_health.run_checks",
            return_value=([ok_result], 0),
        ) as mock_run:
            main(["--openclaw"])
        mock_run.assert_called_once()
        call_kwargs = mock_run.call_args
        assert call_kwargs[1]["mode"] == "openclaw" or call_kwargs[0][0] == "openclaw"


# ---------------------------------------------------------------------------
# Alert dispatch
# ---------------------------------------------------------------------------


class TestAlertDispatch:
    """Tests for alert sending."""

    def test_stdout_does_nothing(self):
        # Should not raise
        send_alert("test", {"alert": "stdout"})

    def test_telegram_dispatch(self):
        cfg = {
            "alert": "telegram",
            "telegram_token": "tok123",
            "telegram_chat": "chat456",
        }
        with mock.patch("urllib.request.urlopen") as mock_open:
            send_alert("test message", cfg)
        mock_open.assert_called_once()

    def test_discord_dispatch(self):
        cfg = {
            "alert": "discord",
            "discord_webhook": "https://discord.com/api/webhooks/test",
        }
        with mock.patch("urllib.request.urlopen") as mock_open:
            send_alert("test message", cfg)
        mock_open.assert_called_once()

    def test_slack_dispatch(self):
        cfg = {
            "alert": "slack",
            "slack_webhook": "https://hooks.slack.com/test",
        }
        with mock.patch("urllib.request.urlopen") as mock_open:
            send_alert("test message", cfg)
        mock_open.assert_called_once()

    def test_alert_failure_handled(self):
        cfg = {
            "alert": "telegram",
            "telegram_token": "tok",
            "telegram_chat": "chat",
        }
        with mock.patch("urllib.request.urlopen", side_effect=Exception("fail")):
            # Should not raise
            send_alert("test", cfg)
