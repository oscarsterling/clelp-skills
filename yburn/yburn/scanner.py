"""Cron job scanner for yburn.

Scans cron jobs from the openclaw CLI or from pre-parsed JSON data,
and normalizes them into CronJob dataclass instances.
"""

import json
import logging
import subprocess
from dataclasses import dataclass, field
from typing import Any, List

logger = logging.getLogger(__name__)


@dataclass
class CronJob:
    """Normalized representation of an openclaw cron job."""

    id: str
    name: str
    schedule: dict
    schedule_expr: str
    payload_kind: str
    payload_text: str
    delivery_config: dict
    enabled: bool
    last_run_status: str
    consecutive_errors: int
    session_target: str
    model: str


def scan_crons() -> List[CronJob]:
    """Scan cron jobs by invoking `openclaw cron list`.

    Runs the openclaw CLI as a subprocess, captures JSON output,
    and parses each job into a CronJob dataclass.

    Returns:
        List of parsed CronJob instances.

    Raises:
        RuntimeError: If the openclaw command fails.
    """
    logger.info("Scanning cron jobs via openclaw CLI")
    try:
        # Try JSON format first
        result = subprocess.run(
            ["openclaw", "cron", "list", "--json"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0 or not result.stdout.strip().startswith(("{", "[")):
            # Fallback: try without --json flag and parse structured output
            result = subprocess.run(
                ["openclaw", "cron", "list"],
                capture_output=True,
                text=True,
            )
    except FileNotFoundError:
        raise RuntimeError(
            "openclaw CLI not found. Ensure it is installed and on your PATH."
        )

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(
            "openclaw cron list did not return JSON. "
            "Try exporting cron jobs manually: openclaw cron list > crons.json"
        )

    # Handle both {"jobs": [...]} and [...] formats
    if isinstance(data, dict) and "jobs" in data:
        data = data["jobs"]
    if not isinstance(data, list):
        raise RuntimeError(
            f"Expected a JSON array from openclaw, got {type(data).__name__}"
        )

    return scan_from_json(data)


def scan_from_json(json_data: List[dict]) -> List[CronJob]:
    """Parse a list of raw cron job dicts into CronJob instances.

    This is the shared parsing logic used by both scan_crons() and
    test code that loads fixtures directly.

    Args:
        json_data: List of cron job dictionaries (as returned by openclaw).

    Returns:
        List of parsed CronJob instances.
    """
    jobs = []
    for raw in json_data:
        try:
            job = _parse_job(raw)
            jobs.append(job)
        except Exception:
            job_id = raw.get("id", "<unknown>")
            logger.exception("Failed to parse cron job %s, skipping", job_id)
    logger.info("Parsed %d cron jobs", len(jobs))
    return jobs


def _parse_job(raw: dict) -> CronJob:
    """Parse a single raw job dict into a CronJob.

    Args:
        raw: A single cron job dictionary from openclaw output.

    Returns:
        A populated CronJob instance.
    """
    job_id = raw.get("id", "")
    name = raw.get("name") or job_id

    schedule = raw.get("schedule", {})
    schedule_expr = _extract_schedule_expr(schedule)

    payload = raw.get("payload") or {}
    payload_kind = payload.get("kind", "")
    payload_text = _extract_payload_text(payload)
    model = payload.get("model", "")

    delivery_config = raw.get("deliveryConfig", {})

    state = raw.get("state", {})
    last_run_status = state.get("lastRunStatus", "unknown")
    consecutive_errors = state.get("consecutiveErrors", 0)

    return CronJob(
        id=job_id,
        name=name,
        schedule=schedule,
        schedule_expr=schedule_expr,
        payload_kind=payload_kind,
        payload_text=payload_text,
        delivery_config=delivery_config,
        enabled=raw.get("enabled", True),
        last_run_status=last_run_status,
        consecutive_errors=consecutive_errors,
        session_target=raw.get("sessionTarget", ""),
        model=model,
    )


def _extract_schedule_expr(schedule: dict) -> str:
    """Extract a human-readable schedule expression.

    For cron-kind schedules, returns the cron expression with timezone
    if available. For interval-kind, returns the interval description.

    Args:
        schedule: The raw schedule dictionary.

    Returns:
        A string describing the schedule.
    """
    kind = schedule.get("kind", "")
    if kind == "cron":
        expr = schedule.get("expr", "")
        tz = schedule.get("tz", "")
        return f"{expr} ({tz})" if tz else expr
    elif kind == "interval":
        return schedule.get("interval", str(schedule))
    return str(schedule)


def _extract_payload_text(payload: dict) -> str:
    """Extract the text content from a payload.

    For agentTurn payloads, the text is in 'message'.
    For systemEvent payloads, the text is in 'text'.

    Args:
        payload: The raw payload dictionary.

    Returns:
        The extracted text, or empty string if not found.
    """
    kind = payload.get("kind", "")
    if kind == "agentTurn":
        return payload.get("message", "")
    elif kind == "systemEvent":
        return payload.get("text", "")
    logger.warning("Unknown payload kind '%s', no text extracted", kind)
    return ""
