"""Tests for yburn CLI helpers and new audit/report behavior."""

import json
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from yburn.classifier import Classification, ClassificationResult
from yburn.cli import (
    _classify_with_manual_overrides,
    _write_manual_classification,
    cmd_replace,
    cmd_rollback,
    cmd_report,
)
from yburn.replacer import record_replacement, get_active_replacements
from yburn.scanner import CronJob


def make_job(name="test-job", payload_text="", job_id="job-1"):
    return CronJob(
        id=job_id,
        name=name,
        schedule={"kind": "cron", "expr": "0 * * * *", "tz": "America/New_York"},
        schedule_expr="0 * * * * (America/New_York)",
        payload_kind="agentTurn",
        payload_text=payload_text,
        delivery_config={},
        enabled=True,
        last_run_status="ok",
        consecutive_errors=0,
        session_target="isolated",
        model="haiku",
    )


class TestManualClassifications:
    def test_persisted_override_applies(self, monkeypatch, tmp_path):
        path = tmp_path / "manual-classifications.json"
        monkeypatch.setattr("yburn.cli.MANUAL_CLASSIFICATIONS_FILE", path)

        job = make_job(name="Manual Job", payload_text="analyze strategy", job_id="abc")
        _write_manual_classification(job, "mechanical")
        results = _classify_with_manual_overrides([job], threshold=3)

        _, result = results[0]
        assert result.classification == Classification.MECHANICAL
        payload = json.loads(path.read_text())
        assert payload["jobs"][0]["decision"] == "mechanical"


class TestReportCommand:
    def test_report_command_writes_output(self, monkeypatch, tmp_path, capsys):
        jobs = [make_job(name="Report Job", payload_text="check health", job_id="report-1")]
        output_path = tmp_path / "report.md"
        auto_report_path = tmp_path / "auto.md"

        monkeypatch.setattr("yburn.cli.Config.load", staticmethod(lambda: Namespace(classification_threshold=3)))
        monkeypatch.setattr("yburn.cli._load_jobs", lambda args: jobs)
        monkeypatch.setattr(
            "yburn.cli._classify_with_manual_overrides",
            lambda jobs, threshold: [(
                jobs[0],
                ClassificationResult(
                    classification=Classification.MECHANICAL,
                    mechanical_score=4,
                    reasoning_score=0,
                    confidence=1.0,
                    signals_found=["manual:mechanical"],
                ),
            )],
        )

        class StubReport:
            def render(self, fmt):
                return "stub-output"

            def auto_save_markdown(self):
                auto_report_path.write_text("auto")
                return auto_report_path

            def save(self, path, fmt):
                path.write_text("explicit")
                return path

        monkeypatch.setattr("yburn.cli.load_templates", lambda: [])
        monkeypatch.setattr("yburn.cli._build_report", lambda jobs, results, templates: StubReport())

        args = Namespace(format="markdown", output=str(output_path), threshold=None, file=None)
        code = cmd_report(args)

        assert code == 0
        assert output_path.read_text() == "explicit"
        assert auto_report_path.read_text() == "auto"
        assert "Auto-saved markdown report" in capsys.readouterr().out


class TestReplaceDryRunDefault:
    def test_replace_dry_run_by_default(self, monkeypatch, tmp_path, capsys):
        """replace shows preview and exits without --execute."""
        job = make_job(name="Test Job", job_id="job-1")
        script_path = tmp_path / "test-job.py"
        script_path.write_text("print('hello')")

        monkeypatch.setattr("yburn.cli.scan_crons", lambda: [job])
        monkeypatch.setattr("yburn.cli.get_replacement_for_job", lambda jid: None)
        monkeypatch.setattr("yburn.converter.SCRIPTS_DIR", tmp_path)

        args = Namespace(job_id="job-1", execute=False, yes=False, strict=False)
        code = cmd_replace(args)

        assert code == 0
        out = capsys.readouterr().out
        assert "DRY RUN" in out
        assert "--execute" in out

    def test_replace_with_execute_records(self, monkeypatch, tmp_path, capsys):
        """replace --execute actually records the replacement."""
        job = make_job(name="Test Job", job_id="job-1")
        script_path = tmp_path / "test-job.py"
        script_path.write_text("print('hello')")

        monkeypatch.setattr("yburn.cli.scan_crons", lambda: [job])
        monkeypatch.setattr("yburn.cli.get_replacement_for_job", lambda jid: None)
        monkeypatch.setattr("yburn.converter.SCRIPTS_DIR", tmp_path)

        recorded = []
        def fake_record(*a, **kw):
            from yburn.replacer import Replacement
            r = Replacement(
                original_job_id="job-1", original_job_name="Test Job",
                original_schedule={}, script_path=str(script_path),
                template_name="manual", replaced_at="now", status="active",
            )
            recorded.append(r)
            return r
        monkeypatch.setattr("yburn.cli.record_replacement", fake_record)

        args = Namespace(job_id="job-1", execute=True, yes=True, strict=False)
        code = cmd_replace(args)

        assert code == 0
        assert len(recorded) == 1
        out = capsys.readouterr().out
        assert "Replacement recorded" in out


class TestRollbackAll:
    def test_rollback_all(self, monkeypatch, tmp_path, capsys):
        """rollback --all rolls back all active replacements."""
        with patch("yburn.replacer.STATE_DIR", tmp_path):
            record_replacement("job-1", "Test 1", {}, "/s1.py", "tmpl")
            record_replacement("job-2", "Test 2", {}, "/s2.py", "tmpl")

            with patch("yburn.replacer.subprocess.run") as mock_run:
                mock_run.return_value.returncode = 0
                mock_run.return_value.stderr = ""
                args = Namespace(job_id=None, all=True, yes=True)
                code = cmd_rollback(args)

            assert code == 0
            assert len(get_active_replacements()) == 0
            out = capsys.readouterr().out
            assert "Test 1" in out
            assert "Test 2" in out

    def test_rollback_all_empty(self, monkeypatch, tmp_path, capsys):
        """rollback --all with no active replacements."""
        with patch("yburn.replacer.STATE_DIR", tmp_path):
            args = Namespace(job_id=None, all=True, yes=True)
            code = cmd_rollback(args)
            assert code == 0
            assert "No active replacements" in capsys.readouterr().out

    def test_rollback_requires_job_id_or_all(self, capsys):
        """rollback without job_id or --all shows error."""
        args = Namespace(job_id=None, all=False)
        code = cmd_rollback(args)
        assert code == 1
