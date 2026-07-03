"""Cron replacement logic for yburn.

Handles the 'last mile': generating zero-LLM cron replacements,
disabling originals, tracking replacements, and rollback.
"""

import json
import logging
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

STATE_DIR = Path.home() / ".yburn" / "state"
REPLACEMENTS_FILE = "replacements.json"


@dataclass
class Replacement:
    """Tracks a single cron job replacement."""
    original_job_id: str
    original_job_name: str
    original_schedule: dict
    script_path: str
    template_name: str
    replaced_at: str
    status: str  # "active", "rolled_back"
    new_cron_id: Optional[str] = None
    original_payload: Optional[dict] = None
    original_enabled: bool = True


def load_replacements() -> List[Replacement]:
    """Load replacement tracking data."""
    state_path = STATE_DIR / REPLACEMENTS_FILE
    if not state_path.exists():
        return []
    try:
        with open(state_path) as f:
            data = json.load(f)
        replacements = []
        for r in data:
            replacements.append(Replacement(
                original_job_id=r["original_job_id"],
                original_job_name=r["original_job_name"],
                original_schedule=r["original_schedule"],
                script_path=r["script_path"],
                template_name=r["template_name"],
                replaced_at=r["replaced_at"],
                status=r["status"],
                new_cron_id=r.get("new_cron_id"),
                original_payload=r.get("original_payload"),
                original_enabled=r.get("original_enabled", True),
            ))
        return replacements
    except Exception:
        logger.exception("Failed to load replacements")
        return []


def save_replacements(replacements: List[Replacement]) -> None:
    """Save replacement tracking data."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state_path = STATE_DIR / REPLACEMENTS_FILE
    data = []
    for r in replacements:
        entry = {
            "original_job_id": r.original_job_id,
            "original_job_name": r.original_job_name,
            "original_schedule": r.original_schedule,
            "script_path": r.script_path,
            "template_name": r.template_name,
            "replaced_at": r.replaced_at,
            "status": r.status,
            "new_cron_id": r.new_cron_id,
            "original_payload": r.original_payload,
            "original_enabled": r.original_enabled,
        }
        data.append(entry)
    with open(state_path, "w") as f:
        json.dump(data, f, indent=2)
    logger.info("Saved %d replacement records", len(data))


def build_replacement_command(
    original_job_id: str,
    original_name: str,
    schedule: dict,
    script_path: str,
) -> Dict:
    """Build the replacement cron job specification.

    Args:
        original_job_id: ID of the original cron job.
        original_name: Name of the original job.
        schedule: Original schedule dict.
        script_path: Path to the generated script.

    Returns:
        Dict with crontab guidance and disable command.
    """
    cron_expr = _schedule_to_crontab(schedule)
    safe_name = _sanitize_job_name(original_name)
    log_path = f"~/.yburn/logs/{safe_name}.log"

    return {
        "crontab_entry": (
            "# one-time job - set up manually"
            if cron_expr == "# one-time job - set up manually"
            else f"{cron_expr} python3 {script_path} >> {log_path} 2>&1"
        ),
        "disable_command": f"openclaw cron update {original_job_id} --disable",
        "original_job_id": original_job_id,
        "script_path": script_path,
    }


def _sanitize_job_name(name: str) -> str:
    """Create a filesystem-safe job name."""
    return "".join(c.lower() if c.isalnum() or c in "-_" else "-" for c in name)


def _schedule_to_crontab(schedule: dict) -> str:
    """Convert an OpenClaw schedule dict to a crontab expression."""
    kind = schedule.get("kind")

    if kind == "cron":
        return schedule.get("expr", "0 * * * *")

    if kind == "at":
        return "# one-time job - set up manually"

    if kind == "every":
        every_ms = schedule.get("everyMs")
        if not isinstance(every_ms, (int, float)) or every_ms <= 0:
            return "0 * * * *"

        minutes = max(1, round(every_ms / 60000))
        if minutes >= 1440 and minutes % 1440 == 0:
            days = max(1, minutes // 1440)
            if days == 1:
                return "0 0 * * *"
            return f"0 0 */{days} * *"
        if minutes >= 60 and minutes % 60 == 0:
            hours = max(1, minutes // 60)
            if hours == 1:
                return "0 * * * *"
            if 24 % hours == 0:
                return f"0 */{hours} * * *"
            return "0 * * * *"
        if minutes <= 59:
            return f"*/{minutes} * * * *"
        return "0 * * * *"

    return "0 * * * *"


def preview_replacement(
    original_job_id: str,
    original_name: str,
    schedule: dict,
    schedule_expr: str,
    script_path: str,
) -> str:
    """Generate a preview of the replacement.

    Args:
        original_job_id: ID of the original cron job.
        original_name: Name of the original job.
        schedule: Original schedule dict.
        schedule_expr: Human-readable schedule.
        script_path: Path to the generated script.

    Returns:
        Formatted preview string.
    """
    spec = build_replacement_command(original_job_id, original_name, schedule, script_path)

    lines = [
        "=== Replacement Preview ===",
        "",
        "OLD JOB:",
        f"  Name: {original_name}",
        f"  ID: {original_job_id}",
        f"  Schedule: {schedule_expr}",
        f"  Action: Will be DISABLED (not deleted)",
        "",
        "NEW LOCAL CRON:",
        f"  Script: {spec['script_path']}",
        f"  Crontab: {spec['crontab_entry']}",
        f"  Disable: {spec['disable_command']}",
        "",
        "ROLLBACK: yburn rollback {original_job_id}",
        "===========================",
    ]
    return "\n".join(lines)


def record_replacement(
    original_job_id: str,
    original_name: str,
    original_schedule: dict,
    script_path: str,
    template_name: str,
    new_cron_id: Optional[str] = None,
    original_payload: Optional[dict] = None,
    original_enabled: bool = True,
) -> Replacement:
    """Record a replacement in the tracking file.

    Args:
        original_job_id: ID of the original cron job.
        original_name: Name of the original job.
        original_schedule: Original schedule configuration.
        script_path: Path to the generated script.
        template_name: Name of the template used.
        new_cron_id: ID of the new cron job (if created).
        original_payload: Full original job object as a dict.
        original_enabled: Whether the original job was enabled.

    Returns:
        The recorded Replacement.
    """
    replacements = load_replacements()

    # Check for existing replacement
    for r in replacements:
        if r.original_job_id == original_job_id and r.status == "active":
            logger.warning(
                "Job %s already has an active replacement", original_job_id
            )
            return r

    replacement = Replacement(
        original_job_id=original_job_id,
        original_job_name=original_name,
        original_schedule=original_schedule,
        script_path=script_path,
        template_name=template_name,
        replaced_at=datetime.now(timezone.utc).isoformat(),
        status="active",
        new_cron_id=new_cron_id,
        original_payload=original_payload,
        original_enabled=original_enabled,
    )

    replacements.append(replacement)
    save_replacements(replacements)
    return replacement


def rollback_replacement(original_job_id: str) -> dict:
    """Roll back a replacement: re-enable original cron, disable replacement cron.

    Args:
        original_job_id: ID of the original cron job.

    Returns:
        Dict with keys: success (bool), actions (list), errors (list).
        Returns {"success": False, "actions": [], "errors": ["No active replacement found"]}
        if no active replacement exists.
    """
    replacements = load_replacements()
    result = {"success": False, "actions": [], "errors": []}
    target = None

    for r in replacements:
        if r.original_job_id == original_job_id and r.status == "active":
            target = r
            break

    if not target:
        logger.warning("No active replacement found for %s", original_job_id)
        result["errors"].append(f"No active replacement found for {original_job_id}")
        return result

    # Re-enable original cron if it was enabled before
    if target.original_enabled:
        try:
            proc = subprocess.run(
                ["openclaw", "cron", "update", original_job_id, "--enable"],
                capture_output=True, text=True, timeout=30,
            )
            if proc.returncode == 0:
                result["actions"].append(f"Re-enabled original cron {original_job_id}")
            else:
                result["errors"].append(
                    f"Failed to re-enable {original_job_id}: {proc.stderr.strip()}"
                )
        except FileNotFoundError:
            result["errors"].append("openclaw CLI not found")
        except subprocess.TimeoutExpired:
            result["errors"].append(f"Timeout re-enabling {original_job_id}")

    # Disable yburn replacement cron if one exists
    if target.new_cron_id:
        try:
            proc = subprocess.run(
                ["openclaw", "cron", "update", target.new_cron_id, "--disable"],
                capture_output=True, text=True, timeout=30,
            )
            if proc.returncode == 0:
                result["actions"].append(f"Disabled replacement cron {target.new_cron_id}")
            else:
                result["errors"].append(
                    f"Failed to disable {target.new_cron_id}: {proc.stderr.strip()}"
                )
        except FileNotFoundError:
            result["errors"].append("openclaw CLI not found")
        except subprocess.TimeoutExpired:
            result["errors"].append(f"Timeout disabling {target.new_cron_id}")

    # Update tracking state only on success
    if len(result["errors"]) == 0:
        target.status = "rolled_back"
        save_replacements(replacements)
        result["actions"].append(f"Marked {target.original_job_name} as rolled_back")
        logger.info("Rolled back replacement for %s", target.original_job_name)
    result["success"] = len(result["errors"]) == 0

    return result


def get_active_replacements() -> List[Replacement]:
    """Get all active (non-rolled-back) replacements."""
    return [r for r in load_replacements() if r.status == "active"]


def get_replacement_for_job(original_job_id: str) -> Optional[Replacement]:
    """Get the active replacement for a specific job, if any."""
    for r in load_replacements():
        if r.original_job_id == original_job_id and r.status == "active":
            return r
    return None
