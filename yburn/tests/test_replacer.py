"""Tests for the yburn cron replacement logic."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from yburn.replacer import (
    Replacement,
    _schedule_to_crontab,
    build_replacement_command,
    get_active_replacements,
    get_replacement_for_job,
    load_replacements,
    preview_replacement,
    record_replacement,
    rollback_replacement,
    save_replacements,
)


@pytest.fixture
def temp_state_dir(tmp_path):
    """Provide a temporary state directory."""
    with patch("yburn.replacer.STATE_DIR", tmp_path):
        yield tmp_path


class TestBuildReplacementCommand:
    def test_has_required_fields(self):
        spec = build_replacement_command(
            "job-123", "My Job", {"kind": "cron", "expr": "0 * * * *"},
            "/path/to/script.py"
        )
        assert spec["crontab_entry"] == (
            "0 * * * * python3 /path/to/script.py >> ~/.yburn/logs/my-job.log 2>&1"
        )
        assert spec["disable_command"] == "openclaw cron update job-123 --disable"
        assert spec["original_job_id"] == "job-123"
        assert spec["script_path"] == "/path/to/script.py"

    def test_every_schedule_hourly(self):
        spec = build_replacement_command(
            "id", "name", {"kind": "every", "everyMs": 3600000}, "/script.py"
        )
        assert spec["crontab_entry"].startswith("0 * * * * python3 /script.py")

    def test_every_schedule_half_hour(self):
        spec = build_replacement_command(
            "id", "name", {"kind": "every", "everyMs": 1800000}, "/script.py"
        )
        assert spec["crontab_entry"].startswith("*/30 * * * * python3 /script.py")

    def test_every_schedule_daily(self):
        spec = build_replacement_command(
            "id", "name", {"kind": "every", "everyMs": 86400000}, "/script.py"
        )
        assert spec["crontab_entry"].startswith("0 0 * * * python3 /script.py")

    def test_every_schedule_five_minutes(self):
        spec = build_replacement_command(
            "id", "name", {"kind": "every", "everyMs": 300000}, "/script.py"
        )
        assert spec["crontab_entry"].startswith("*/5 * * * * python3 /script.py")

    def test_at_schedule_outputs_manual_comment(self):
        spec = build_replacement_command(
            "id", "name", {"kind": "at", "at": "2026-03-30T10:00:00Z"}, "/script.py"
        )
        assert spec["crontab_entry"] == "# one-time job - set up manually"

    def test_unknown_schedule_defaults_hourly(self):
        spec = build_replacement_command("id", "name", {"kind": "weird"}, "/script.py")
        assert spec["crontab_entry"].startswith("0 * * * * python3 /script.py")


class TestScheduleToCrontab:
    def test_zero_every_ms_defaults_hourly(self):
        assert _schedule_to_crontab({"kind": "every", "everyMs": 0}) == "0 * * * *"

    def test_negative_every_ms_defaults_hourly(self):
        assert _schedule_to_crontab({"kind": "every", "everyMs": -60000}) == "0 * * * *"

    def test_non_integer_string_every_ms_defaults_hourly(self):
        assert _schedule_to_crontab({"kind": "every", "everyMs": "60000"}) == "0 * * * *"

    def test_float_every_ms_rounds_to_minutes(self):
        assert _schedule_to_crontab({"kind": "every", "everyMs": 90000.5}) == "*/2 * * * *"


class TestPreviewReplacement:
    def test_contains_job_info(self):
        preview = preview_replacement(
            "job-123", "My Health Check",
            {"kind": "cron", "expr": "0 4 * * *"},
            "0 4 * * * (America/New_York)",
            "/home/user/.yburn/scripts/my-health-check.py",
        )
        assert "My Health Check" in preview
        assert "job-123" in preview
        assert "DISABLED" in preview
        assert "crontab" in preview.lower()
        assert "python3 /home/user/.yburn/scripts/my-health-check.py" in preview
        assert "openclaw cron update job-123 --disable" in preview
        assert "rollback" in preview.lower()


class TestReplacementTracking:
    def test_save_and_load(self, temp_state_dir):
        replacements = [
            Replacement(
                original_job_id="job-1",
                original_job_name="Test Job",
                original_schedule={"kind": "cron", "expr": "0 * * * *"},
                script_path="/path/script.py",
                template_name="system-diagnostics",
                replaced_at="2026-03-19T22:00:00Z",
                status="active",
                new_cron_id="new-job-1",
            )
        ]
        save_replacements(replacements)
        loaded = load_replacements()
        assert len(loaded) == 1
        assert loaded[0].original_job_id == "job-1"
        assert loaded[0].status == "active"

    def test_load_empty(self, temp_state_dir):
        loaded = load_replacements()
        assert loaded == []

    def test_record_replacement(self, temp_state_dir):
        r = record_replacement(
            "job-1", "Test Job",
            {"kind": "cron", "expr": "0 * * * *"},
            "/path/script.py", "system-diagnostics",
            new_cron_id="new-1",
        )
        assert r.status == "active"
        assert r.original_job_id == "job-1"

        # Verify persisted
        loaded = load_replacements()
        assert len(loaded) == 1

    def test_no_duplicate_active(self, temp_state_dir):
        record_replacement("job-1", "Test", {}, "/s.py", "tmpl")
        r2 = record_replacement("job-1", "Test", {}, "/s.py", "tmpl")
        loaded = load_replacements()
        assert len(loaded) == 1  # Should not duplicate

    def test_rollback(self, temp_state_dir):
        record_replacement("job-1", "Test", {}, "/s.py", "tmpl")
        with patch("yburn.replacer.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stderr = ""
            result = rollback_replacement("job-1")
        assert result["success"] is True
        assert any("rolled_back" in a for a in result["actions"])
        loaded = load_replacements()
        assert loaded[0].status == "rolled_back"

    def test_rollback_nonexistent(self, temp_state_dir):
        result = rollback_replacement("nonexistent")
        assert result["success"] is False
        assert len(result["errors"]) > 0

    def test_get_active_replacements(self, temp_state_dir):
        record_replacement("job-1", "Test 1", {}, "/s1.py", "tmpl")
        record_replacement("job-2", "Test 2", {}, "/s2.py", "tmpl")
        with patch("yburn.replacer.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stderr = ""
            rollback_replacement("job-1")

        active = get_active_replacements()
        assert len(active) == 1
        assert active[0].original_job_id == "job-2"

    def test_get_replacement_for_job(self, temp_state_dir):
        record_replacement("job-1", "Test", {}, "/s.py", "tmpl")
        r = get_replacement_for_job("job-1")
        assert r is not None
        assert r.original_job_name == "Test"

    def test_get_replacement_for_nonexistent(self, temp_state_dir):
        r = get_replacement_for_job("nonexistent")
        assert r is None

    def test_rolled_back_not_returned(self, temp_state_dir):
        record_replacement("job-1", "Test", {}, "/s.py", "tmpl")
        with patch("yburn.replacer.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stderr = ""
            rollback_replacement("job-1")
        r = get_replacement_for_job("job-1")
        assert r is None

    def test_original_payload_saved_and_loaded(self, temp_state_dir):
        payload = {"id": "job-1", "name": "Test", "schedule": {"kind": "cron"}}
        r = record_replacement(
            "job-1", "Test", {}, "/s.py", "tmpl",
            original_payload=payload, original_enabled=False,
        )
        assert r.original_payload == payload
        assert r.original_enabled is False

        loaded = load_replacements()
        assert loaded[0].original_payload == payload
        assert loaded[0].original_enabled is False

    def test_backward_compat_load_without_new_fields(self, temp_state_dir):
        """Old JSON files without original_payload/original_enabled load fine."""
        old_data = [{
            "original_job_id": "job-1",
            "original_job_name": "Test",
            "original_schedule": {},
            "script_path": "/s.py",
            "template_name": "tmpl",
            "replaced_at": "2026-01-01T00:00:00Z",
            "status": "active",
            "new_cron_id": None,
        }]
        state_path = temp_state_dir / "replacements.json"
        state_path.write_text(json.dumps(old_data))

        loaded = load_replacements()
        assert len(loaded) == 1
        assert loaded[0].original_payload is None
        assert loaded[0].original_enabled is True

    def test_rollback_calls_openclaw_enable(self, temp_state_dir):
        """Rollback re-enables original cron via openclaw CLI."""
        record_replacement(
            "job-1", "Test", {}, "/s.py", "tmpl",
            new_cron_id="new-1", original_enabled=True,
        )
        with patch("yburn.replacer.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stderr = ""
            result = rollback_replacement("job-1")

        assert result["success"] is True
        calls = mock_run.call_args_list
        # Should call enable on original and disable on replacement
        assert any("--enable" in str(c) for c in calls)
        assert any("--disable" in str(c) for c in calls)

    def test_rollback_skips_enable_if_originally_disabled(self, temp_state_dir):
        """If original was disabled, rollback doesn't re-enable it."""
        record_replacement(
            "job-1", "Test", {}, "/s.py", "tmpl",
            original_enabled=False,
        )
        with patch("yburn.replacer.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stderr = ""
            result = rollback_replacement("job-1")

        # Should NOT have called openclaw with --enable
        for call in mock_run.call_args_list:
            assert "--enable" not in call[0][0]

    def test_rollback_all(self, temp_state_dir):
        """Rolling back multiple replacements works."""
        record_replacement("job-1", "Test 1", {}, "/s1.py", "tmpl")
        record_replacement("job-2", "Test 2", {}, "/s2.py", "tmpl")

        # Roll back all by iterating active replacements
        active = get_active_replacements()
        assert len(active) == 2

        with patch("yburn.replacer.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stderr = ""
            for r in active:
                rollback_replacement(r.original_job_id)

        assert len(get_active_replacements()) == 0
        loaded = load_replacements()
        assert all(r.status == "rolled_back" for r in loaded)
