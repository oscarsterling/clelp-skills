#!/usr/bin/env python3
"""yburn-health - System health monitoring with universal, OpenClaw, and Claude Code modes.

One tool, three audiences. Runs on any machine with Python 3.9+ stdlib only.
Exit codes: 0 = healthy, 1 = warnings, 2 = critical.
"""

import argparse
import datetime
import json
import logging
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
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
        YBURN_HEALTH_DISK_THRESHOLD  - integer percent (default 85)
        YBURN_HEALTH_PROCESSES       - comma-separated process names
        YBURN_HEALTH_ALERT           - stdout | telegram | discord | slack
        YBURN_HEALTH_TELEGRAM_TOKEN  - Telegram bot token
        YBURN_HEALTH_TELEGRAM_CHAT   - Telegram chat ID
        YBURN_HEALTH_DISCORD_WEBHOOK - Discord webhook URL
        YBURN_HEALTH_SLACK_WEBHOOK   - Slack webhook URL
    """
    cfg = _load_yaml_config(Path.home() / ".yburn" / "health.yaml")

    if os.getenv("YBURN_HEALTH_DISK_THRESHOLD"):
        cfg["disk_threshold"] = int(os.environ["YBURN_HEALTH_DISK_THRESHOLD"])
    if os.getenv("YBURN_HEALTH_PROCESSES"):
        cfg["processes"] = [
            p.strip() for p in os.environ["YBURN_HEALTH_PROCESSES"].split(",") if p.strip()
        ]
    if os.getenv("YBURN_HEALTH_ALERT"):
        cfg["alert"] = os.environ["YBURN_HEALTH_ALERT"]
    for key in ("TELEGRAM_TOKEN", "TELEGRAM_CHAT", "DISCORD_WEBHOOK", "SLACK_WEBHOOK"):
        env = f"YBURN_HEALTH_{key}"
        if os.getenv(env):
            cfg[key.lower()] = os.environ[env]

    cfg.setdefault("disk_threshold", 85)
    cfg.setdefault("processes", [])
    cfg.setdefault("alert", "stdout")
    return cfg


# ---------------------------------------------------------------------------
# Check result container
# ---------------------------------------------------------------------------


class CheckResult:
    """Single health-check result."""

    def __init__(self, name: str, status: str, message: str, detail: str = ""):
        self.name = name
        self.status = status
        self.message = message
        self.detail = detail

    def to_dict(self) -> Dict[str, str]:
        d: Dict[str, str] = {
            "name": self.name,
            "status": self.status,
            "message": self.message,
        }
        if self.detail:
            d["detail"] = self.detail
        return d

    def pretty(self) -> str:
        icon = ICON.get(self.status, "?")
        line = f"{icon} {self.message}"
        if self.detail:
            line += f"\n   {self.detail}"
        return line


# ---------------------------------------------------------------------------
# Universal checks
# ---------------------------------------------------------------------------


def check_cpu() -> CheckResult:
    """CPU usage via top (macOS) or /proc/stat (Linux)."""
    try:
        if platform.system() == "Darwin":
            try:
                out = subprocess.check_output(
                    ["top", "-l", "1", "-n", "0", "-stats", "cpu"],
                    text=True, timeout=10, stderr=subprocess.DEVNULL,
                )
            except OSError as exc:
                if getattr(exc, "errno", None) != 1:
                    raise
                cores = os.cpu_count() or 1
                load1 = os.getloadavg()[0]
                used = round(min(100.0, (load1 / max(cores, 1)) * 100), 1)
                status = WARN if used > 90 else OK
                return CheckResult("cpu", status, f"CPU: {used}% ({cores} cores) [fallback]")
            for line in out.splitlines():
                if "CPU usage" in line:
                    # "CPU usage: 5.26% user, 10.0% sys, 84.73% idle"
                    parts = line.split(",")
                    for part in parts:
                        if "idle" in part:
                            idle = float(part.strip().split("%")[0].split()[-1])
                            used = round(100 - idle, 1)
                            break
                    else:
                        used = 0.0
                    break
            else:
                return CheckResult("cpu", OK, "CPU: unable to parse", "")
        else:
            # Linux: read /proc/stat twice
            def _read_stat() -> List[int]:
                with open("/proc/stat") as f:
                    line = f.readline()
                return [int(x) for x in line.split()[1:]]

            s1 = _read_stat()
            time.sleep(0.5)
            s2 = _read_stat()
            delta = [b - a for a, b in zip(s1, s2)]
            total = sum(delta)
            idle = delta[3] if len(delta) > 3 else 0
            used = round(100 * (1 - idle / max(total, 1)), 1) if total else 0.0

        cores = os.cpu_count() or 1
        status = WARN if used > 90 else OK
        return CheckResult("cpu", status, f"CPU: {used}% ({cores} cores)")
    except Exception as exc:
        return CheckResult("cpu", WARN, f"CPU: check failed ({exc})")


def check_memory() -> CheckResult:
    """Memory usage via vm_stat (macOS) or /proc/meminfo (Linux)."""
    try:
        if platform.system() == "Darwin":
            out = subprocess.check_output(["vm_stat"], text=True, timeout=5)
            stats: Dict[str, int] = {}
            page_size = 16384  # default
            for line in out.splitlines():
                if "page size of" in line:
                    page_size = int(line.split()[-2])
                if ":" in line:
                    key, val = line.split(":", 1)
                    val_clean = val.strip().rstrip(".")
                    if val_clean.isdigit():
                        stats[key.strip()] = int(val_clean)

            pages_free = stats.get("Pages free", 0)
            pages_active = stats.get("Pages active", 0)
            pages_inactive = stats.get("Pages inactive", 0)
            pages_speculative = stats.get("Pages speculative", 0)
            pages_wired = stats.get("Pages wired down", 0)
            pages_compressed = stats.get("Pages occupied by compressor", 0)

            total_pages = (
                pages_free + pages_active + pages_inactive
                + pages_speculative + pages_wired + pages_compressed
            )
            used_pages = pages_active + pages_wired + pages_compressed
            total_gb = round(total_pages * page_size / (1024 ** 3), 1)
            used_gb = round(used_pages * page_size / (1024 ** 3), 1)
        else:
            info: Dict[str, int] = {}
            with open("/proc/meminfo") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2:
                        info[parts[0].rstrip(":")] = int(parts[1])
            total_kb = info.get("MemTotal", 0)
            avail_kb = info.get("MemAvailable", total_kb)
            total_gb = round(total_kb / (1024 ** 2), 1)
            used_gb = round((total_kb - avail_kb) / (1024 ** 2), 1)

        pct = round(100 * used_gb / max(total_gb, 0.1), 0)
        status = CRITICAL if pct > 95 else (WARN if pct > 85 else OK)
        return CheckResult(
            "memory", status,
            f"Memory: {used_gb}/{total_gb} GB ({int(pct)}%)",
        )
    except Exception as exc:
        return CheckResult("memory", WARN, f"Memory: check failed ({exc})")


def check_disk(threshold: int = 85) -> List[CheckResult]:
    """Disk usage per mounted volume."""
    results: List[CheckResult] = []
    try:
        if platform.system() == "Darwin":
            out = subprocess.check_output(
                ["df", "-P", "-k"], text=True, timeout=5,
            )
        else:
            out = subprocess.check_output(
                ["df", "-P", "-k", "-x", "tmpfs", "-x", "devtmpfs"],
                text=True, timeout=5,
            )
        lines = out.strip().splitlines()[1:]  # skip header
        seen: set = set()
        for line in lines:
            # df -P uses fixed columns: Filesystem, 1024-blocks, Used, Available, Capacity, Mounted on
            # Mount point is everything from column 6 onward (handles spaces in fs names).
            # We parse from the RIGHT: mount is last token(s), pct is the token ending with %.
            parts = line.split()
            if len(parts) < 6:
                continue
            # Find the percentage column (ends with %)
            pct_idx = -1
            for i, p in enumerate(parts):
                if p.endswith("%"):
                    pct_idx = i
                    break
            if pct_idx < 3:
                continue
            mount = " ".join(parts[pct_idx + 1:])
            if not mount or mount in seen:
                continue
            seen.add(mount)
            # Skip pseudo-filesystems
            if mount.startswith(("/dev", "/sys", "/proc", "/run")):
                continue
            try:
                total_kb = int(parts[pct_idx - 3])
            except (ValueError, IndexError):
                continue
            if total_kb == 0:
                continue
            try:
                avail_kb = int(parts[pct_idx - 1])
                used_pct = int(parts[pct_idx].rstrip("%"))
            except (ValueError, IndexError):
                continue
            free_gb = round(avail_kb / (1024 ** 2), 1)
            status = CRITICAL if used_pct > 95 else (WARN if used_pct >= threshold else OK)
            results.append(CheckResult(
                f"disk:{mount}", status,
                f"Disk {mount}: {used_pct}% ({free_gb} GB free)",
            ))
    except Exception as exc:
        results.append(CheckResult("disk", WARN, f"Disk: check failed ({exc})"))
    return results


def check_load() -> CheckResult:
    """System load average."""
    try:
        load1, load5, load15 = os.getloadavg()
        cores = os.cpu_count() or 1
        status = WARN if load1 > cores * 2 else OK
        return CheckResult(
            "load", status,
            f"Load: {load1:.1f}, {load5:.1f}, {load15:.1f}",
        )
    except Exception as exc:
        return CheckResult("load", WARN, f"Load: check failed ({exc})")


def check_uptime() -> CheckResult:
    """System uptime."""
    try:
        if platform.system() == "Darwin":
            sysctl = "/usr/sbin/sysctl" if os.path.exists("/usr/sbin/sysctl") else "sysctl"
            out = subprocess.check_output(
                [sysctl, "-n", "kern.boottime"], text=True, timeout=5,
            )
            # Output like: { sec = 1700000000, usec = 0 } ...
            sec_str = out.split("sec =")[1].split(",")[0].strip()
            boot = int(sec_str)
        else:
            with open("/proc/uptime") as f:
                boot_delta = float(f.read().split()[0])
            boot = int(time.time() - boot_delta)

        delta = int(time.time()) - boot
        days = delta // 86400
        hours = (delta % 86400) // 3600
        return CheckResult("uptime", OK, f"Uptime: {days} days, {hours} hours")
    except Exception as exc:
        return CheckResult("uptime", WARN, f"Uptime: check failed ({exc})")


def check_processes(names: List[str]) -> List[CheckResult]:
    """Check if named processes are running."""
    results: List[CheckResult] = []
    for name in names:
        try:
            if platform.system() == "Darwin":
                out = subprocess.run(
                    ["pgrep", "-x", name],
                    capture_output=True, text=True, timeout=5,
                )
            else:
                out = subprocess.run(
                    ["pgrep", "-x", name],
                    capture_output=True, text=True, timeout=5,
                )
            if out.returncode == 0:
                results.append(CheckResult(
                    f"process:{name}", OK,
                    f"Process '{name}' running",
                ))
            else:
                results.append(CheckResult(
                    f"process:{name}", CRITICAL,
                    f"Process '{name}' NOT FOUND",
                ))
        except Exception as exc:
            results.append(CheckResult(
                f"process:{name}", WARN,
                f"Process '{name}': check failed ({exc})",
            ))
    return results


def check_network() -> CheckResult:
    """Check internet connectivity by connecting to a well-known host."""
    try:
        start = time.time()
        sock = socket.create_connection(("1.1.1.1", 53), timeout=5)
        elapsed_ms = int((time.time() - start) * 1000)
        sock.close()
        return CheckResult("network", OK, f"Network: reachable ({elapsed_ms}ms)")
    except Exception:
        return CheckResult("network", CRITICAL, "Network: unreachable")


def check_docker() -> Optional[CheckResult]:
    """Docker health if installed, None if not present."""
    if not shutil.which("docker"):
        return None
    try:
        out = subprocess.check_output(
            ["docker", "ps", "--format", "{{.Status}}"],
            text=True, timeout=10, stderr=subprocess.DEVNULL,
        )
        lines = [l for l in out.strip().splitlines() if l]
        total = len(lines)
        running = sum(1 for l in lines if l.lower().startswith("up"))
        status = WARN if running < total else OK
        return CheckResult(
            "docker", status,
            f"Docker: {total} containers ({running} running)",
        )
    except subprocess.TimeoutExpired:
        return CheckResult("docker", WARN, "Docker: check timed out")
    except Exception as exc:
        return CheckResult("docker", WARN, f"Docker: check failed ({exc})")


# ---------------------------------------------------------------------------
# OpenClaw checks
# ---------------------------------------------------------------------------


def _run_openclaw(*args: str, timeout: int = 10) -> Optional[str]:
    """Run an openclaw CLI command, return stdout or None on failure."""
    if not shutil.which("openclaw"):
        return None
    try:
        result = subprocess.run(
            ["openclaw"] + list(args),
            capture_output=True, text=True, timeout=timeout,
        )
        if result.returncode == 0:
            return result.stdout
        return None
    except Exception:
        return None


def check_openclaw_gateway() -> CheckResult:
    """Check if OpenClaw gateway is running, its version, and uptime."""
    out = _run_openclaw("status", "--json")
    if out is None:
        return CheckResult(
            "openclaw:gateway", WARN,
            "Gateway: openclaw CLI not available or not responding",
        )
    try:
        data = json.loads(out)
        running = data.get("running", False)
        version = data.get("version", "unknown")
        uptime_str = data.get("uptime", "unknown")
        if running:
            return CheckResult(
                "openclaw:gateway", OK,
                f"Gateway: running (v{version}, up {uptime_str})",
            )
        return CheckResult("openclaw:gateway", CRITICAL, "Gateway: not running")
    except (json.JSONDecodeError, KeyError):
        return CheckResult("openclaw:gateway", WARN, "Gateway: unable to parse status")


def check_openclaw_crons() -> CheckResult:
    """Check OpenClaw cron job health."""
    out = _run_openclaw("cron", "list", "--json")
    if out is None:
        return CheckResult(
            "openclaw:crons", WARN,
            "Crons: openclaw CLI not available",
        )
    try:
        data = json.loads(out)
        jobs = data if isinstance(data, list) else data.get("jobs", [])
        total = len(jobs)
        enabled = sum(1 for j in jobs if j.get("enabled", False))
        disabled = total - enabled
        return CheckResult(
            "openclaw:crons", OK,
            f"Crons: {total} total ({enabled} enabled, {disabled} disabled)",
        )
    except (json.JSONDecodeError, KeyError):
        return CheckResult("openclaw:crons", WARN, "Crons: unable to parse")


def check_openclaw_cron_failures() -> CheckResult:
    """Check for cron jobs with consecutive failures."""
    out = _run_openclaw("cron", "list", "--json")
    if out is None:
        return CheckResult(
            "openclaw:cron_failures", WARN,
            "Cron failures: openclaw CLI not available",
        )
    try:
        data = json.loads(out)
        jobs = data if isinstance(data, list) else data.get("jobs", [])
        failing = []
        for j in jobs:
            consec = j.get("consecutive_failures", 0)
            if consec >= 3:
                failing.append(j.get("name", "unknown"))
        if failing:
            return CheckResult(
                "openclaw:cron_failures", WARN,
                f"Cron failures: {len(failing)} jobs with 3+ consecutive failures",
                detail=", ".join(failing),
            )
        return CheckResult(
            "openclaw:cron_failures", OK,
            "Cron failures: none with 3+ consecutive failures",
        )
    except (json.JSONDecodeError, KeyError):
        return CheckResult("openclaw:cron_failures", WARN, "Cron failures: unable to parse")


def check_openclaw_sessions() -> CheckResult:
    """Check for zombie or stuck sessions."""
    out = _run_openclaw("session", "list", "--json")
    if out is None:
        return CheckResult(
            "openclaw:sessions", WARN,
            "Sessions: openclaw CLI not available",
        )
    try:
        data = json.loads(out)
        sessions = data if isinstance(data, list) else data.get("sessions", [])
        active = sum(1 for s in sessions if s.get("status") == "active")
        stuck = sum(1 for s in sessions if s.get("status") in ("stuck", "zombie"))
        status = WARN if stuck > 0 else OK
        return CheckResult(
            "openclaw:sessions", status,
            f"Sessions: {active} active, {stuck} stuck",
        )
    except (json.JSONDecodeError, KeyError):
        return CheckResult("openclaw:sessions", WARN, "Sessions: unable to parse")


def check_openclaw_channels() -> CheckResult:
    """Parse recent logs for channel connectivity."""
    log_path = Path.home() / ".openclaw" / "logs" / "gateway.log"
    if not log_path.is_file():
        return CheckResult(
            "openclaw:channels", WARN,
            "Channels: log file not found",
        )
    try:
        # Read last 200 lines
        with open(log_path) as f:
            lines = f.readlines()[-200:]

        connected: List[str] = []
        disconnected: List[str] = []
        for channel in ("Telegram", "Discord", "Slack"):
            chan_lower = channel.lower()
            # Find most recent status line for this channel
            last_status = None
            for line in reversed(lines):
                if chan_lower in line.lower():
                    if "connected" in line.lower() and "disconnect" not in line.lower():
                        last_status = "connected"
                    elif "disconnect" in line.lower() or "error" in line.lower():
                        last_status = "disconnected"
                    if last_status:
                        break
            if last_status == "connected":
                connected.append(channel)
            elif last_status == "disconnected":
                disconnected.append(channel)

        parts = []
        if connected:
            parts.append(", ".join(connected) + " connected")
        if disconnected:
            parts.append(", ".join(disconnected) + " disconnected")
        if not parts:
            parts.append("no channel activity found")

        status = WARN if disconnected else OK
        return CheckResult(
            "openclaw:channels", status,
            f"Channels: {'; '.join(parts)}",
        )
    except Exception as exc:
        return CheckResult("openclaw:channels", WARN, f"Channels: check failed ({exc})")


def check_openclaw_memory_db() -> CheckResult:
    """Check memory DB size."""
    db_path = Path.home() / ".openclaw" / "memory" / "main.sqlite"
    if not db_path.is_file():
        return CheckResult(
            "openclaw:memory_db", WARN,
            "Memory DB: file not found",
        )
    try:
        size_mb = round(db_path.stat().st_size / (1024 * 1024), 1)
        status = WARN if size_mb > 500 else OK
        return CheckResult(
            "openclaw:memory_db", status,
            f"Memory DB: {size_mb} MB {'(large)' if size_mb > 500 else '(healthy)'}",
        )
    except Exception as exc:
        return CheckResult("openclaw:memory_db", WARN, f"Memory DB: check failed ({exc})")


# ---------------------------------------------------------------------------
# Claude Code checks
# ---------------------------------------------------------------------------


def check_claude_cli() -> CheckResult:
    """Check if claude CLI is available."""
    if not shutil.which("claude"):
        return CheckResult(
            "claude:cli", WARN,
            "Claude CLI: not found in PATH",
        )
    try:
        result = subprocess.run(
            ["claude", "--version"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            version = result.stdout.strip().split("\n")[0]
            return CheckResult("claude:cli", OK, f"Claude CLI: {version}")
        return CheckResult("claude:cli", WARN, "Claude CLI: found but not responding")
    except Exception as exc:
        return CheckResult("claude:cli", WARN, f"Claude CLI: check failed ({exc})")


def check_claude_sessions() -> CheckResult:
    """Check Claude Code session files in ~/.claude/."""
    claude_dir = Path.home() / ".claude"
    if not claude_dir.is_dir():
        return CheckResult(
            "claude:sessions", WARN,
            "Sessions: ~/.claude/ directory not found",
        )
    try:
        # Count project directories and session-like files
        projects_dir = claude_dir / "projects"
        project_count = 0
        if projects_dir.is_dir():
            project_count = sum(1 for p in projects_dir.iterdir() if p.is_dir())

        # Calculate total size
        total_size = 0
        file_count = 0
        for f in claude_dir.rglob("*"):
            if f.is_file():
                total_size += f.stat().st_size
                file_count += 1

        size_mb = round(total_size / (1024 * 1024), 1)
        return CheckResult(
            "claude:sessions", OK,
            f"Sessions: {file_count} files, {size_mb} MB, {project_count} projects",
        )
    except Exception as exc:
        return CheckResult("claude:sessions", WARN, f"Sessions: check failed ({exc})")


def check_claude_tasks() -> CheckResult:
    """Check for scheduled/active Claude tasks."""
    claude_dir = Path.home() / ".claude"
    tasks_dir = claude_dir / "tasks"
    if not tasks_dir.is_dir():
        return CheckResult("claude:tasks", OK, "Tasks: no tasks directory found")
    try:
        task_files = list(tasks_dir.rglob("*.json"))
        active = 0
        expired = 0
        now = time.time()
        for tf in task_files:
            try:
                with open(tf) as f:
                    data = json.loads(f.read())
                expires = data.get("expires_at", 0)
                if expires and expires < now:
                    expired += 1
                else:
                    active += 1
            except Exception:
                active += 1  # count unparseable as active

        status = WARN if expired > 10 else OK
        return CheckResult(
            "claude:tasks", status,
            f"Tasks: {active} active, {expired} expired",
        )
    except Exception as exc:
        return CheckResult("claude:tasks", WARN, f"Tasks: check failed ({exc})")


# ---------------------------------------------------------------------------
# Alert dispatch
# ---------------------------------------------------------------------------


def send_alert(text: str, cfg: Dict[str, Any]) -> None:
    """Send alert text via configured channel."""
    method = cfg.get("alert", "stdout")
    if method == "stdout":
        return  # already printed

    import urllib.request
    import urllib.error

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
# Runner
# ---------------------------------------------------------------------------


def run_checks(
    mode: str = "universal",
    cfg: Optional[Dict[str, Any]] = None,
) -> Tuple[List[CheckResult], int]:
    """Run all checks for the given mode and return results with exit code.

    Args:
        mode: One of "universal", "openclaw", "claude-code".
        cfg: Configuration dict. Loaded automatically if None.

    Returns:
        Tuple of (list of CheckResult, exit_code).
    """
    if cfg is None:
        cfg = load_config()

    results: List[CheckResult] = []

    # Universal checks (always run)
    results.append(check_cpu())
    results.append(check_memory())
    results.extend(check_disk(cfg.get("disk_threshold", 85)))
    results.append(check_load())
    results.append(check_uptime())

    docker_result = check_docker()
    if docker_result:
        results.append(docker_result)

    results.append(check_network())
    results.extend(check_processes(cfg.get("processes", [])))

    # OpenClaw checks
    if mode == "openclaw":
        results.append(check_openclaw_gateway())
        results.append(check_openclaw_crons())
        results.append(check_openclaw_cron_failures())
        results.append(check_openclaw_sessions())
        results.append(check_openclaw_channels())
        results.append(check_openclaw_memory_db())

    # Claude Code checks
    if mode == "claude-code":
        results.append(check_claude_cli())
        results.append(check_claude_sessions())
        results.append(check_claude_tasks())

    # Determine exit code
    has_critical = any(r.status == CRITICAL for r in results)
    has_warn = any(r.status == WARN for r in results)
    if has_critical:
        exit_code = 2
    elif has_warn:
        exit_code = 1
    else:
        exit_code = 0

    return results, exit_code


def format_pretty(results: List[CheckResult], mode: str, exit_code: int) -> str:
    """Format results as pretty colored terminal output."""
    lines: List[str] = []
    lines.append(f"yburn-health v{__version__}")
    lines.append("\u2501" * 32)

    # Universal section
    universal_results = [
        r for r in results if not r.name.startswith(("openclaw:", "claude:"))
    ]
    for r in universal_results:
        lines.append(r.pretty())

    # OpenClaw section
    if mode == "openclaw":
        oc_results = [r for r in results if r.name.startswith("openclaw:")]
        if oc_results:
            lines.append("")
            lines.append("--- OpenClaw ---")
            for r in oc_results:
                lines.append(r.pretty())

    # Claude Code section
    if mode == "claude-code":
        cc_results = [r for r in results if r.name.startswith("claude:")]
        if cc_results:
            lines.append("")
            lines.append("--- Claude Code ---")
            for r in cc_results:
                lines.append(r.pretty())

    lines.append("\u2501" * 32)

    # Summary
    crits = sum(1 for r in results if r.status == CRITICAL)
    warns = sum(1 for r in results if r.status == WARN)
    alerts = crits + warns
    if exit_code == 0:
        lines.append("Status: HEALTHY")
    elif exit_code == 1:
        lines.append(f"Status: WARNING ({alerts} alert{'s' if alerts != 1 else ''})")
    else:
        lines.append(f"Status: CRITICAL ({crits} critical, {warns} warning)")

    return "\n".join(lines)


def format_json(results: List[CheckResult], mode: str, exit_code: int) -> str:
    """Format results as JSON."""
    status_map = {0: "healthy", 1: "warning", 2: "critical"}
    output = {
        "version": __version__,
        "mode": mode,
        "status": status_map.get(exit_code, "unknown"),
        "exit_code": exit_code,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "checks": [r.to_dict() for r in results],
    }
    return json.dumps(output, indent=2)


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="yburn-health",
        description="System health monitoring - one tool, three audiences.",
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--openclaw", action="store_true",
        help="Include OpenClaw-specific checks",
    )
    mode_group.add_argument(
        "--claude-code", action="store_true",
        help="Include Claude Code-specific checks",
    )
    parser.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output in JSON format",
    )
    parser.add_argument(
        "--version", action="version", version=f"yburn-health {__version__}",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point.

    Args:
        argv: Command-line arguments. Uses sys.argv if None.

    Returns:
        Exit code (0 = healthy, 1 = warnings, 2 = critical).
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.openclaw:
        mode = "openclaw"
    elif args.claude_code:
        mode = "claude-code"
    else:
        mode = "universal"

    cfg = load_config()
    results, exit_code = run_checks(mode=mode, cfg=cfg)

    if args.json_output:
        output = format_json(results, mode, exit_code)
    else:
        output = format_pretty(results, mode, exit_code)

    print(output)

    # Send alert if configured and not healthy
    if exit_code > 0 and cfg.get("alert", "stdout") != "stdout":
        alert_text = format_pretty(results, mode, exit_code)
        send_alert(alert_text, cfg)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
