"""Conversion reporting for yburn audit and conversion flows."""

from __future__ import annotations

import json
import math
import os
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from yburn.classifier import Classification, ClassificationResult
from yburn.converter import MatchResult, script_path_for_job
from yburn.scanner import CronJob

GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BLUE = "\033[94m"
BOLD = "\033[1m"
RESET = "\033[0m"

REPORTS_DIR = Path.home() / ".yburn" / "reports"


def get_reports_dir() -> Path:
    """Return the configured reports directory."""
    override = os.environ.get("YBURN_REPORTS_DIR")
    if override:
        return Path(override).expanduser()
    return REPORTS_DIR


def _color(text: str, code: str, enabled: bool) -> str:
    if enabled and sys.stdout.isatty():
        return f"{code}{text}{RESET}"
    return text


@dataclass
class ConversionReportEntry:
    """Tracked report state for one audited job."""

    name: str
    job_id: str
    schedule: str
    classification: str
    confidence: float
    template_match: Optional[str]
    conversion_status: str
    signals: List[str]
    payload_snippet: str
    script_path: Optional[str]
    script_exists: bool
    enabled: bool


class ConversionReport:
    """Build terminal, markdown, and JSON conversion reports."""

    def __init__(self, generated_at: Optional[datetime] = None):
        self.generated_at = generated_at or datetime.now()
        self.entries: List[ConversionReportEntry] = []

    def add_job(
        self,
        job: CronJob,
        result: ClassificationResult,
        match: Optional[MatchResult] = None,
        conversion_status: Optional[str] = None,
    ) -> None:
        """Track one audited job."""
        script_path = script_path_for_job(job)
        script_exists = script_path.exists()
        template_match = match.template.name if match and match.template else None

        status = conversion_status or self._infer_status(
            result.classification,
            template_match,
            script_exists,
        )

        self.entries.append(
            ConversionReportEntry(
                name=job.name,
                job_id=job.id,
                schedule=job.schedule_expr,
                classification=result.classification.value,
                confidence=result.confidence,
                template_match=template_match,
                conversion_status=status,
                signals=list(result.signals_found),
                payload_snippet=job.payload_text[:100],
                script_path=str(script_path) if script_exists else None,
                script_exists=script_exists,
                enabled=job.enabled,
            )
        )

    def _infer_status(
        self,
        classification: Classification,
        template_match: Optional[str],
        script_exists: bool,
    ) -> str:
        if script_exists and classification == Classification.MECHANICAL:
            return "converted"
        if classification == Classification.MECHANICAL and template_match:
            return "convertible"
        if classification == Classification.MECHANICAL and not template_match:
            return "skipped_no_template"
        if classification == Classification.UNSURE:
            return "ambiguous"
        return "kept"

    def summary(self) -> Dict[str, object]:
        mechanical = [e for e in self.entries if e.classification == Classification.MECHANICAL.value]
        reasoning = [e for e in self.entries if e.classification == Classification.REASONING.value]
        unsure = [e for e in self.entries if e.classification == Classification.UNSURE.value]
        converted = [e for e in mechanical if e.conversion_status == "converted"]
        skipped = [e for e in mechanical if e.conversion_status == "skipped_no_template"]
        convertible = [e for e in mechanical if e.conversion_status == "convertible"]

        per_day = sum(self._estimate_runs_per_day(e.schedule) for e in converted)
        token_savings = {
            "sessions_eliminated_per_day": round(per_day, 2),
            "sessions_eliminated_per_week": round(per_day * 7, 2),
            "sessions_eliminated_per_month": round(per_day * 30, 2),
            "speed_improvement": "30s -> under 1s per converted run",
        }

        return {
            "generated_at": self.generated_at.isoformat(),
            "total_jobs": len(self.entries),
            "mechanical_count": len(mechanical),
            "reasoning_count": len(reasoning),
            "unsure_count": len(unsure),
            "converted_count": len(converted),
            "skipped_count": len(skipped),
            "convertible_pending_count": len(convertible),
            "token_savings_estimate": token_savings,
        }

    def as_dict(self) -> Dict[str, object]:
        """Return report as a JSON-serializable dict."""
        data = self.summary()
        data["entries"] = [asdict(entry) for entry in self.entries]
        return data

    def render(self, fmt: str = "terminal") -> str:
        """Render the report in the requested format."""
        if fmt == "json":
            return json.dumps(self.as_dict(), indent=2)
        if fmt == "markdown":
            return self._render_markdown()
        return self._render_terminal()

    def auto_save_markdown(self) -> Path:
        """Save a markdown report to the default reports directory."""
        reports_dir = get_reports_dir()
        reports_dir.mkdir(parents=True, exist_ok=True)
        path = reports_dir / self.generated_at.strftime("%Y-%m-%d-%H-%M.md")
        path.write_text(self._render_markdown())
        return path

    def save(self, path: Path, fmt: str = "markdown") -> Path:
        """Save the report to a specific path."""
        expanded = path.expanduser()
        expanded.parent.mkdir(parents=True, exist_ok=True)
        expanded.write_text(self.render(fmt))
        return expanded

    def _sectioned_entries(self) -> Dict[str, List[ConversionReportEntry]]:
        return {
            "converted": [e for e in self.entries if e.conversion_status == "converted"],
            "skipped": [e for e in self.entries if e.conversion_status == "skipped_no_template"],
            "ambiguous": [e for e in self.entries if e.conversion_status == "ambiguous"],
            "kept": [e for e in self.entries if e.conversion_status == "kept"],
        }

    def _render_terminal(self) -> str:
        sections = self._sectioned_entries()
        summary = self.summary()
        lines = [
            _color("AUDIT SUMMARY", BOLD, True),
            f"Total jobs: {summary['total_jobs']}",
            _color(f"Mechanical: {summary['mechanical_count']}", GREEN, True),
            _color(f"Reasoning: {summary['reasoning_count']}", RED, True),
            _color(f"Unsure: {summary['unsure_count']}", YELLOW, True),
        ]
        if summary["convertible_pending_count"]:
            lines.append(_color(f"Convertible pending: {summary['convertible_pending_count']}", BLUE, True))

        lines.extend([
            "",
            _color("CONVERTED", GREEN, True),
        ])
        lines.extend(self._format_converted_terminal(sections["converted"]) or ["None"])
        lines.extend(["", _color("SKIPPED", YELLOW, True)])
        lines.extend(self._format_skipped_terminal(sections["skipped"]) or ["None"])
        lines.extend(["", _color("AMBIGUOUS", YELLOW, True)])
        lines.extend(self._format_ambiguous_terminal(sections["ambiguous"]) or ["None"])
        lines.extend(["", _color("KEPT", RED, True)])
        lines.extend(self._format_kept_terminal(sections["kept"]) or ["None"])
        lines.extend(["", _color("TOKEN SAVINGS ESTIMATE", BOLD, True)])
        lines.extend(self._format_token_savings())
        return "\n".join(lines)

    def _render_markdown(self) -> str:
        sections = self._sectioned_entries()
        summary = self.summary()
        lines = [
            "# Conversion Report",
            "",
            f"_Generated: {self.generated_at.isoformat(timespec='minutes')}_",
            "",
            "## AUDIT SUMMARY",
            f"- Total jobs: {summary['total_jobs']}",
            f"- Mechanical: {summary['mechanical_count']}",
            f"- Reasoning: {summary['reasoning_count']}",
            f"- Unsure: {summary['unsure_count']}",
        ]
        if summary["convertible_pending_count"]:
            lines.append(f"- Convertible pending: {summary['convertible_pending_count']}")

        lines.extend(["", "## CONVERTED"])
        lines.extend(self._format_converted_markdown(sections["converted"]) or ["None"])
        lines.extend(["", "## SKIPPED"])
        lines.extend(self._format_skipped_markdown(sections["skipped"]) or ["None"])
        lines.extend(["", "## AMBIGUOUS"])
        lines.extend(self._format_ambiguous_markdown(sections["ambiguous"]) or ["None"])
        lines.extend(["", "## KEPT"])
        lines.extend(self._format_kept_markdown(sections["kept"]) or ["None"])
        lines.extend(["", "## TOKEN SAVINGS ESTIMATE"])
        lines.extend(self._format_token_savings(prefix="- "))
        return "\n".join(lines) + "\n"

    def _format_converted_terminal(self, entries: List[ConversionReportEntry]) -> List[str]:
        lines = []
        for entry in entries:
            lines.append(f"{entry.name}")
            lines.append(f"  before: LLM cron")
            lines.append(f"  after: python3 {entry.script_path}")
            lines.append(f"  schedule: {entry.schedule} (unchanged)")
        return lines

    def _format_converted_markdown(self, entries: List[ConversionReportEntry]) -> List[str]:
        lines = []
        for entry in entries:
            lines.append(f"- **{entry.name}**")
            lines.append(f"  before: LLM cron")
            lines.append(f"  after: `python3 {entry.script_path}`")
            lines.append(f"  schedule: `{entry.schedule}` unchanged")
        return lines

    def _format_skipped_terminal(self, entries: List[ConversionReportEntry]) -> List[str]:
        lines = []
        for entry in entries:
            lines.append(f"{entry.name}")
            lines.append("  no template match; needs Phase 2 custom template")
        return lines

    def _format_skipped_markdown(self, entries: List[ConversionReportEntry]) -> List[str]:
        return [f"- **{entry.name}**: no template match, needs Phase 2 custom template" for entry in entries]

    def _format_ambiguous_terminal(self, entries: List[ConversionReportEntry]) -> List[str]:
        lines = []
        for entry in entries:
            lines.append(f"{entry.name}")
            lines.append(f"  signals: {', '.join(entry.signals[:6]) or 'none'}")
            lines.append(f"  payload: {entry.payload_snippet}")
        return lines

    def _format_ambiguous_markdown(self, entries: List[ConversionReportEntry]) -> List[str]:
        lines = []
        for entry in entries:
            lines.append(f"- **{entry.name}**")
            lines.append(f"  signals: {', '.join(entry.signals[:6]) or 'none'}")
            lines.append(f"  payload: `{entry.payload_snippet}`")
        return lines

    def _format_kept_terminal(self, entries: List[ConversionReportEntry]) -> List[str]:
        return [f"{entry.name}" for entry in entries]

    def _format_kept_markdown(self, entries: List[ConversionReportEntry]) -> List[str]:
        return [f"- {entry.name}" for entry in entries]

    def _format_token_savings(self, prefix: str = "") -> List[str]:
        estimate = self.summary()["token_savings_estimate"]
        daily = round(estimate["sessions_eliminated_per_day"])
        monthly = round(estimate["sessions_eliminated_per_month"])
        return [
            (
                f"{prefix}~{monthly} LLM sessions eliminated per month "
                f"({daily}/day) - Speed: {estimate['speed_improvement']}"
            ),
        ]

    def _estimate_runs_per_day(self, schedule: str) -> float:
        expr = schedule.split(" (", 1)[0].strip()
        parts = expr.split()
        if len(parts) != 5:
            return 1.0

        minute, hour, dom, month, dow = parts
        runs = self._field_count(minute, 60) * self._field_count(hour, 24)
        if dom != "*":
            runs *= self._field_count(dom, 31) / 31
        if month != "*":
            runs *= self._field_count(month, 12) / 12
        if dow != "*":
            runs *= self._field_count(dow, 7) / 7
        return max(round(runs, 4), 1 / 30)

    def _field_count(self, value: str, universe: int) -> int:
        if value == "*":
            return universe
        if "," in value:
            return sum(self._field_count(part, universe) for part in value.split(","))
        if value.startswith("*/"):
            step = int(value[2:]) if value[2:].isdigit() and int(value[2:]) else universe
            return max(math.ceil(universe / step), 1)
        if "-" in value:
            start, end = value.split("-", 1)
            if start.isdigit() and end.isdigit():
                return max(int(end) - int(start) + 1, 1)
        return 1
