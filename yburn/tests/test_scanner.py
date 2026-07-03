"""Tests for the cron job scanner."""

import json
from pathlib import Path

import pytest

from yburn.scanner import CronJob, scan_from_json

FIXTURE_PATH = Path(__file__).resolve().parent.parent / "sample-crons.json"


@pytest.fixture
def sample_jobs():
    """Load and parse the sample cron jobs fixture."""
    with open(FIXTURE_PATH) as f:
        data = json.load(f)
    return scan_from_json(data)


@pytest.fixture
def raw_data():
    """Load the raw JSON fixture data."""
    with open(FIXTURE_PATH) as f:
        return json.load(f)


class TestScanFromJson:
    """Tests for scan_from_json parsing."""

    def test_parses_all_jobs(self, sample_jobs):
        """All 12 jobs in the fixture should be parsed."""
        assert len(sample_jobs) == 12

    def test_returns_cronjob_instances(self, sample_jobs):
        """Every parsed item should be a CronJob dataclass."""
        for job in sample_jobs:
            assert isinstance(job, CronJob)

    def test_empty_input(self):
        """An empty list should return an empty list."""
        assert scan_from_json([]) == []

    def test_skips_unparseable_jobs(self):
        """Jobs that fail to parse should be skipped with a log warning."""
        # A job with None payload should still parse (defaults to empty dict)
        data = [{"id": "test-1", "payload": None}]
        jobs = scan_from_json(data)
        assert len(jobs) == 1


class TestCronJobFields:
    """Tests for individual CronJob field extraction."""

    def test_id_extracted(self, sample_jobs):
        """Job IDs should be extracted."""
        ids = [j.id for j in sample_jobs]
        assert "08ebb6b7-8230-424f-bdd5-56b965e7fda0" in ids

    def test_name_extracted(self, sample_jobs):
        """Job names should be extracted."""
        names = [j.name for j in sample_jobs]
        assert "Pre-Dream Session Flush" in names
        assert "eod-summary" in names

    def test_name_falls_back_to_id(self):
        """Jobs without a name should fall back to using the ID."""
        data = [{"id": "fallback-id-123", "payload": {"kind": "agentTurn"}}]
        jobs = scan_from_json(data)
        assert jobs[0].name == "fallback-id-123"

    def test_enabled_field(self, sample_jobs):
        """All sample jobs are enabled."""
        assert all(j.enabled for j in sample_jobs)

    def test_disabled_job_flagged(self):
        """A job with enabled=False should be flagged."""
        data = [{"id": "disabled-1", "name": "Off Job", "enabled": False, "payload": {}}]
        jobs = scan_from_json(data)
        assert jobs[0].enabled is False

    def test_session_target(self, sample_jobs):
        """Session target should be extracted."""
        flush = next(j for j in sample_jobs if j.name == "Pre-Dream Session Flush")
        assert flush.session_target == "main"

        eod = next(j for j in sample_jobs if j.name == "eod-summary")
        assert eod.session_target == "isolated"

    def test_last_run_status(self, sample_jobs):
        """All sample jobs have lastRunStatus of 'ok'."""
        for job in sample_jobs:
            assert job.last_run_status == "ok"

    def test_consecutive_errors(self, sample_jobs):
        """All sample jobs have 0 consecutive errors."""
        for job in sample_jobs:
            assert job.consecutive_errors == 0

    def test_consecutive_errors_missing_state(self):
        """Missing state should default to 0 errors and 'unknown' status."""
        data = [{"id": "no-state", "payload": {}}]
        jobs = scan_from_json(data)
        assert jobs[0].consecutive_errors == 0
        assert jobs[0].last_run_status == "unknown"


class TestScheduleParsing:
    """Tests for schedule expression extraction."""

    def test_cron_expr_with_timezone(self, sample_jobs):
        """Cron expressions should include timezone when present."""
        flush = next(j for j in sample_jobs if j.name == "Pre-Dream Session Flush")
        assert flush.schedule_expr == "30 22 * * * (America/New_York)"

    def test_cron_expr_without_timezone(self, sample_jobs):
        """Cron expressions without timezone should omit parenthetical."""
        update_check = next(j for j in sample_jobs if j.name == "OpenClaw Update Check")
        assert update_check.schedule_expr == "15 10 * * *"

    def test_schedule_dict_preserved(self, sample_jobs):
        """The raw schedule dict should be preserved."""
        flush = next(j for j in sample_jobs if j.name == "Pre-Dream Session Flush")
        assert flush.schedule["kind"] == "cron"
        assert flush.schedule["tz"] == "America/New_York"


class TestPayloadParsing:
    """Tests for payload text and kind extraction."""

    def test_system_event_text(self, sample_jobs):
        """systemEvent payloads should extract the 'text' field."""
        flush = next(j for j in sample_jobs if j.name == "Pre-Dream Session Flush")
        assert flush.payload_kind == "systemEvent"
        assert flush.payload_text.startswith("PRE-DREAM FLUSH:")

    def test_agent_turn_message(self, sample_jobs):
        """agentTurn payloads should extract the 'message' field."""
        eod = next(j for j in sample_jobs if j.name == "eod-summary")
        assert eod.payload_kind == "agentTurn"
        assert "STEP 1 - STATE VALIDATION" in eod.payload_text

    def test_system_event_backup(self, sample_jobs):
        """The backup job should have systemEvent kind with correct text."""
        backup = next(j for j in sample_jobs if "Backup" in j.name)
        assert backup.payload_kind == "systemEvent"
        assert "NIGHTLY BACKUP" in backup.payload_text

    def test_missing_payload_text(self):
        """Jobs with empty payload should have empty text."""
        data = [{"id": "no-text", "payload": {"kind": "agentTurn"}}]
        jobs = scan_from_json(data)
        assert jobs[0].payload_text == ""

    def test_unknown_payload_kind(self):
        """Unknown payload kinds should result in empty text."""
        data = [{"id": "weird", "payload": {"kind": "webhook", "url": "http://x"}}]
        jobs = scan_from_json(data)
        assert jobs[0].payload_text == ""
        assert jobs[0].payload_kind == "webhook"


class TestModelExtraction:
    """Tests for model field extraction."""

    def test_model_present(self, sample_jobs):
        """Jobs with a model field should have it extracted."""
        eod = next(j for j in sample_jobs if j.name == "eod-summary")
        assert eod.model == "sonnet"

        guru = next(j for j in sample_jobs if j.name == "Guru Daily Research")
        assert guru.model == "anthropic/claude-opus-4-6"

    def test_model_absent(self, sample_jobs):
        """systemEvent jobs without a model should have empty string."""
        flush = next(j for j in sample_jobs if j.name == "Pre-Dream Session Flush")
        assert flush.model == ""

    def test_model_missing_from_payload(self):
        """Jobs with no model key should default to empty string."""
        data = [{"id": "no-model", "payload": {"kind": "agentTurn", "message": "hi"}}]
        jobs = scan_from_json(data)
        assert jobs[0].model == ""


class TestEdgeCases:
    """Tests for edge cases and robustness."""

    def test_minimal_job(self):
        """A job with only an ID and empty payload should parse."""
        data = [{"id": "minimal"}]
        jobs = scan_from_json(data)
        assert len(jobs) == 1
        assert jobs[0].id == "minimal"
        assert jobs[0].name == "minimal"  # falls back to ID
        assert jobs[0].enabled is True  # default
        assert jobs[0].payload_kind == ""
        assert jobs[0].payload_text == ""
        assert jobs[0].model == ""

    def test_delivery_config_default(self, sample_jobs):
        """Jobs without deliveryConfig should get an empty dict."""
        for job in sample_jobs:
            assert isinstance(job.delivery_config, dict)

    def test_all_sample_jobs_have_ids(self, sample_jobs):
        """Every job in the sample should have a non-empty ID."""
        for job in sample_jobs:
            assert job.id
            assert len(job.id) > 0

    def test_payload_kind_values(self, sample_jobs):
        """All jobs should have either systemEvent or agentTurn kind."""
        for job in sample_jobs:
            assert job.payload_kind in ("systemEvent", "agentTurn")
