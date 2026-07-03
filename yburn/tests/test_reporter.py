"""Tests for conversion reporting."""

import json
from pathlib import Path

from yburn.classifier import ClassificationResult, Classification
from yburn.converter import MatchResult, TemplateManifest
from yburn.reporter import ConversionReport
from yburn.scanner import CronJob


def make_job(name="test-job", payload_text="", schedule_expr="0 3 * * * (America/New_York)"):
    return CronJob(
        id=f"{name.lower().replace(' ', '-')}-id",
        name=name,
        schedule={"kind": "cron", "expr": "0 3 * * *", "tz": "America/New_York"},
        schedule_expr=schedule_expr,
        payload_kind="agentTurn",
        payload_text=payload_text,
        delivery_config={},
        enabled=True,
        last_run_status="ok",
        consecutive_errors=0,
        session_target="isolated",
        model="haiku",
    )


def make_result(classification, signals=None, confidence=0.9):
    return ClassificationResult(
        classification=classification,
        mechanical_score=4,
        reasoning_score=1,
        confidence=confidence,
        signals_found=signals or [],
    )


def make_match(name="system-diagnostics"):
    return MatchResult(
        template=TemplateManifest(
            name=name,
            description="",
            version="1.0",
            match_keywords=["health"],
            replaces_patterns=["system diagnostic"],
            parameters=[],
            output_format="plain",
            requires=[],
            path=Path("."),
        ),
        score=5,
        matched_keywords=["health"],
        matched_patterns=["system diagnostic"],
    )


class TestConversionReport:
    def test_markdown_report_sections(self, monkeypatch, tmp_path):
        monkeypatch.setattr("yburn.reporter.REPORTS_DIR", tmp_path / "reports")
        monkeypatch.setattr("yburn.reporter.script_path_for_job", lambda job: tmp_path / f"{job.name}.py")
        converted_script = tmp_path / "Converted Job.py"
        converted_script.write_text("#!/usr/bin/env python3\n")

        report = ConversionReport()
        report.add_job(make_job("Converted Job", "check health"), make_result(Classification.MECHANICAL), make_match())
        report.add_job(make_job("Skipped Job", "check health"), make_result(Classification.MECHANICAL), None)
        report.add_job(make_job("Ambiguous Job", "review health report"), make_result(Classification.UNSURE, ["mechanical:check(+2)", "reasoning:review(+1)"]), None)
        report.add_job(make_job("Reasoning Job", "analyze and recommend"), make_result(Classification.REASONING), None)

        output = report.render("markdown")
        assert "## AUDIT SUMMARY" in output
        assert "## CONVERTED" in output
        assert "## SKIPPED" in output
        assert "## AMBIGUOUS" in output
        assert "## KEPT" in output
        assert "## TOKEN SAVINGS ESTIMATE" in output

        saved = report.auto_save_markdown()
        assert saved.exists()

    def test_json_report_contains_entries(self, monkeypatch, tmp_path):
        monkeypatch.setattr("yburn.reporter.script_path_for_job", lambda job: tmp_path / f"{job.id}.py")
        report = ConversionReport()
        report.add_job(make_job("JSON Job", "check health"), make_result(Classification.MECHANICAL), make_match(), conversion_status="convertible")
        data = json.loads(report.render("json"))
        assert data["total_jobs"] == 1
        assert data["entries"][0]["name"] == "JSON Job"
        assert data["entries"][0]["conversion_status"] == "convertible"
