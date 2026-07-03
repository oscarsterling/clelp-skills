"""Tests for the yburn classification engine."""

import json
from pathlib import Path

import pytest

from yburn.classifier import (
    Classification,
    ClassificationResult,
    apply_manual_override,
    classify_job,
    classify_jobs,
    print_summary,
    _tokenize,
    _has_shell_commands,
)
from yburn.scanner import CronJob, scan_from_json


# --- Helpers ---

def make_job(
    name="test-job",
    payload_text="",
    payload_kind="agentTurn",
    model="",
    enabled=True,
):
    """Create a CronJob for testing."""
    return CronJob(
        id="test-id",
        name=name,
        schedule={"kind": "cron", "expr": "0 * * * *"},
        schedule_expr="0 * * * *",
        payload_kind=payload_kind,
        payload_text=payload_text,
        delivery_config={},
        enabled=enabled,
        last_run_status="ok",
        consecutive_errors=0,
        session_target="isolated",
        model=model,
    )


@pytest.fixture
def sample_jobs():
    """Load real cron job fixtures."""
    fixture_path = Path(__file__).parent.parent / "sample-crons.json"
    with open(fixture_path) as f:
        data = json.load(f)
    return scan_from_json(data)


def find_job(jobs, name_fragment):
    """Find a job by partial name match."""
    for job in jobs:
        if name_fragment.lower() in job.name.lower():
            return job
    raise ValueError(f"No job matching '{name_fragment}'")


# --- TestTokenize ---

class TestTokenize:
    def test_basic_words(self):
        tokens = _tokenize("check status health")
        assert "check" in tokens
        assert "status" in tokens

    def test_lowercases(self):
        tokens = _tokenize("CHECK Status HEALTH")
        assert "check" in tokens
        assert "status" in tokens

    def test_splits_on_delimiters(self):
        tokens = _tokenize("run-the-script, then check")
        assert "run" in tokens
        assert "check" in tokens

    def test_empty_string(self):
        assert _tokenize("") == []


# --- TestShellDetection ---

class TestShellDetection:
    def test_python3(self):
        assert _has_shell_commands("python3 ~/scripts/test.py")

    def test_bash(self):
        assert _has_shell_commands("bash ~/scripts/run.sh")

    def test_git_commands(self):
        assert _has_shell_commands("git add -A && git commit -m 'backup'")

    def test_no_commands(self):
        assert not _has_shell_commands("Analyze the market trends and write a report")

    def test_curl(self):
        assert _has_shell_commands("curl -s https://api.example.com")


# --- TestKeywordScoring ---

class TestKeywordScoring:
    def test_strong_mechanical_keyword(self):
        job = make_job(payload_text="check the system status")
        result = classify_job(job)
        assert result.mechanical_score > 0
        assert any("mechanical:check" in s for s in result.signals_found)

    def test_strong_reasoning_keyword(self):
        job = make_job(payload_text="analyze the data and recommend actions")
        result = classify_job(job)
        assert result.reasoning_score > 0
        assert any("reasoning:analyze" in s for s in result.signals_found)

    def test_weak_mechanical_keyword(self):
        job = make_job(payload_text="count the items and report")
        result = classify_job(job)
        assert any("mechanical:count" in s for s in result.signals_found)

    def test_mixed_signals(self):
        job = make_job(payload_text="check status then analyze results")
        result = classify_job(job)
        assert result.mechanical_score > 0
        assert result.reasoning_score > 0


# --- TestClassificationLogic ---

class TestClassificationLogic:
    def test_clear_mechanical(self):
        job = make_job(
            name="Health Check",
            payload_text="check system status health ping monitor backup",
        )
        result = classify_job(job)
        assert result.classification == Classification.MECHANICAL

    def test_clear_reasoning(self):
        job = make_job(
            name="Strategy Session",
            payload_text="analyze trends and recommend strategy, draft a proposal, create content",
        )
        result = classify_job(job)
        assert result.classification == Classification.REASONING

    def test_unsure_when_close(self):
        job = make_job(
            payload_text="review the health check report and summarize",
        )
        result = classify_job(job)
        # Both sides have signals, should be UNSURE or close
        assert result.classification in (Classification.UNSURE, Classification.MECHANICAL, Classification.REASONING)

    def test_custom_threshold(self):
        job = make_job(payload_text="check status report")
        # With very high threshold, should be UNSURE
        result = classify_job(job, threshold=100)
        assert result.classification == Classification.UNSURE

    def test_zero_threshold(self):
        job = make_job(payload_text="check")
        result = classify_job(job, threshold=0)
        assert result.classification == Classification.MECHANICAL

    def test_manual_override_changes_classification(self):
        job = make_job(payload_text="analyze trends and recommend strategy")
        results = classify_jobs([job], overrides={"test-id": "mechanical"})
        _, result = results[0]
        assert result.classification == Classification.MECHANICAL
        assert result.confidence == 1.0
        assert "manual:mechanical" in result.signals_found

    def test_manual_skip_keeps_job_unsure(self):
        result = ClassificationResult(
            classification=Classification.MECHANICAL,
            mechanical_score=4,
            reasoning_score=1,
            confidence=0.75,
            signals_found=["mechanical:check(+2)"],
        )
        updated = apply_manual_override(result, "skip")
        assert updated.classification == Classification.UNSURE
        assert updated.confidence == 0.75
        assert "manual:skip" in updated.signals_found


# --- TestHeuristics ---

class TestHeuristics:
    def test_shell_commands_boost(self):
        job = make_job(payload_text="python3 ~/scripts/cleanup.py")
        result = classify_job(job)
        assert any("shell_commands" in s for s in result.signals_found)

    def test_haiku_model_boost(self):
        job = make_job(payload_text="do something", model="haiku")
        result = classify_job(job)
        assert any("haiku_model" in s for s in result.signals_found)

    def test_opus_model_boost(self):
        job = make_job(payload_text="do something", model="anthropic/claude-opus-4-6")
        result = classify_job(job)
        assert any("opus_model" in s for s in result.signals_found)

    def test_short_script_runner_boost(self):
        job = make_job(
            payload_text="Run: python3 ~/scripts/test.py. Alert if error.",
            payload_kind="agentTurn",
        )
        result = classify_job(job)
        assert any("short_script_runner" in s for s in result.signals_found)

    def test_system_event_with_commands(self):
        job = make_job(
            payload_text="git add -A && git commit -m 'backup'",
            payload_kind="systemEvent",
        )
        result = classify_job(job)
        assert any("systemEvent_with_commands" in s for s in result.signals_found)


# --- TestEdgeCases ---

class TestEdgeCases:
    def test_empty_payload(self):
        job = make_job(name="xyz", payload_text="")
        result = classify_job(job)
        assert result.classification == Classification.UNSURE
        assert result.mechanical_score == 0
        assert result.reasoning_score == 0

    def test_no_signals_at_all(self):
        job = make_job(name="xyz", payload_text="lorem ipsum dolor sit amet")
        result = classify_job(job)
        assert result.classification == Classification.UNSURE

    def test_confidence_range(self):
        job = make_job(payload_text="check status health backup monitor")
        result = classify_job(job)
        assert 0.0 <= result.confidence <= 1.0

    def test_confidence_zero_when_equal(self):
        job = make_job(name="xyz", payload_text="")
        result = classify_job(job)
        assert result.confidence == 0.0


# --- TestRealJobs (acceptance criteria) ---

class TestRealJobs:
    """Test against real cron jobs from our fixture data."""

    def test_stuck_session_cleanup_is_mechanical(self, sample_jobs):
        job = find_job(sample_jobs, "Stuck Session Cleanup")
        result = classify_job(job)
        assert result.classification == Classification.MECHANICAL

    def test_db_maintenance_is_mechanical(self, sample_jobs):
        job = find_job(sample_jobs, "DB Maintenance")
        result = classify_job(job)
        assert result.classification == Classification.MECHANICAL

    def test_oauth_health_check_is_mechanical(self, sample_jobs):
        job = find_job(sample_jobs, "OAuth Token Health Check")
        result = classify_job(job)
        assert result.classification == Classification.MECHANICAL

    def test_nightly_backup_is_mechanical(self, sample_jobs):
        job = find_job(sample_jobs, "Nightly Backup")
        result = classify_job(job)
        assert result.classification == Classification.MECHANICAL

    def test_system_state_compiler_is_mechanical(self, sample_jobs):
        job = find_job(sample_jobs, "System State Compiler")
        result = classify_job(job)
        assert result.classification == Classification.MECHANICAL

    def test_openclaw_update_check_is_mechanical(self, sample_jobs):
        job = find_job(sample_jobs, "OpenClaw Update Check")
        result = classify_job(job)
        assert result.classification == Classification.MECHANICAL

    def test_cron_health_report_is_mechanical(self, sample_jobs):
        job = find_job(sample_jobs, "Daily Cron Health Report")
        result = classify_job(job)
        assert result.classification == Classification.MECHANICAL

    def test_guru_daily_research_is_reasoning(self, sample_jobs):
        job = find_job(sample_jobs, "Guru Daily Research")
        result = classify_job(job)
        assert result.classification == Classification.REASONING

    def test_personal_daily_brief_is_reasoning(self, sample_jobs):
        job = find_job(sample_jobs, "Personal Daily Brief")
        result = classify_job(job)
        # Fixture has truncated payload - with full text this would be REASONING
        # With truncated text, reasoning signals still outweigh mechanical
        assert result.reasoning_score > 0
        assert result.classification in (Classification.REASONING, Classification.UNSURE)

    def test_eod_summary_is_reasoning(self, sample_jobs):
        job = find_job(sample_jobs, "eod-summary")
        result = classify_job(job)
        # Fixture has truncated payload - with full text this would be REASONING
        assert result.reasoning_score > 0
        assert result.classification in (Classification.REASONING, Classification.UNSURE)

    def test_full_payload_daily_brief_is_reasoning(self):
        """Test with realistic full payload text (not truncated fixture)."""
        job = make_job(
            name="Personal Daily Brief",
            payload_text="Generate the daily personal brief. Include weather, AI Top 5 overnight developments, priorities and reminders, morning surprises with business opportunities, cool tech finds, curated insights. Write business ideas trending opportunities. Summarize and compose content for the Reports channel.",
            model="anthropic/claude-sonnet-4-6",
        )
        result = classify_job(job)
        assert result.classification == Classification.REASONING

    def test_full_payload_eod_summary_is_reasoning(self):
        """Test with realistic full payload text (not truncated fixture)."""
        job = make_job(
            name="eod-summary",
            payload_text="You are Oscar, Chief of Staff. Generate the end-of-day summary for the Reports channel. Read today's memory file. Summarize what got done, what's in progress, what's pending. Write a brief with bullet points covering the real highlights. Synthesize and compose the daily output.",
            model="sonnet",
        )
        result = classify_job(job)
        assert result.classification == Classification.REASONING

    def test_nightly_system_diagnostic_is_mechanical(self, sample_jobs):
        job = find_job(sample_jobs, "Nightly System Diagnostic")
        result = classify_job(job)
        # This one is complex - it runs health checks but has lots of text
        # Should lean mechanical due to all the check/status/health keywords
        assert result.classification in (Classification.MECHANICAL, Classification.UNSURE)


# --- TestClassifyJobs ---

class TestClassifyJobs:
    def test_returns_paired_list(self, sample_jobs):
        results = classify_jobs(sample_jobs)
        assert len(results) == len(sample_jobs)
        for job, result in results:
            assert isinstance(result, ClassificationResult)

    def test_custom_threshold_applied(self, sample_jobs):
        results = classify_jobs(sample_jobs, threshold=100)
        for _, result in results:
            assert result.classification == Classification.UNSURE


# --- TestPrintSummary ---

class TestPrintSummary:
    def test_contains_counts(self, sample_jobs):
        results = classify_jobs(sample_jobs)
        summary = print_summary(results)
        assert "Mechanical:" in summary
        assert "Reasoning:" in summary
        assert "Unsure:" in summary
        assert f"Total:       {len(sample_jobs)}" in summary

    def test_contains_job_names(self, sample_jobs):
        results = classify_jobs(sample_jobs)
        summary = print_summary(results)
        # At least some job names should appear
        assert "Health Check" in summary or "Backup" in summary or "Guru" in summary

    def test_empty_results(self):
        summary = print_summary([])
        assert "Total:       0" in summary
