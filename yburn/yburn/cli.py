"""CLI entry point for yburn.

Provides commands: audit, classify, convert, replace, list, test, rollback, version.
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from yburn import __version__
from yburn.classifier import (
    Classification,
    classify_jobs,
)
from yburn.config import Config
from yburn.converter import (
    check_output_config,
    generate_script,
    load_templates,
    match_job_to_template,
    preview_conversion,
    script_path_for_job,
)
from yburn.reporter import ConversionReport
from yburn.replacer import (
    build_replacement_command,
    get_active_replacements,
    get_replacement_for_job,
    preview_replacement,
    record_replacement,
    rollback_replacement,
)
from yburn.scanner import scan_crons, scan_from_json

# ANSI colors
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BLUE = "\033[94m"
BOLD = "\033[1m"
RESET = "\033[0m"
MANUAL_CLASSIFICATIONS_FILE = Path.home() / ".yburn" / "state" / "manual-classifications.json"


def color(text, code):
    """Apply ANSI color if stdout is a terminal."""
    if sys.stdout.isatty():
        return f"{code}{text}{RESET}"
    return text


def _manual_classifications_file():
    """Return the configured manual classification file path."""
    override = os.environ.get("YBURN_MANUAL_CLASSIFICATIONS_FILE")
    if override:
        return Path(override).expanduser()
    state_dir = os.environ.get("YBURN_STATE_DIR")
    if state_dir:
        return Path(state_dir).expanduser() / "manual-classifications.json"
    return MANUAL_CLASSIFICATIONS_FILE


def _load_jobs(args):
    """Load cron jobs from OpenClaw or a JSON file."""
    if getattr(args, "file", None):
        with open(args.file) as f:
            data = json.load(f)
        if isinstance(data, dict) and "jobs" in data:
            data = data["jobs"]
        return scan_from_json(data)
    return scan_crons()


def _load_manual_classifications():
    """Load persisted manual classification decisions."""
    manual_file = _manual_classifications_file()
    if not manual_file.exists():
        return {}

    with open(manual_file) as f:
        data = json.load(f)

    decisions = {}
    for entry in data.get("jobs", []):
        decision = entry.get("decision")
        if not decision:
            continue
        if entry.get("job_id"):
            decisions[entry["job_id"]] = decision
        if entry.get("job_name"):
            decisions[f"name:{entry['job_name'].strip().lower()}"] = decision
    return decisions


def _write_manual_classification(job, decision):
    """Persist a manual audit decision."""
    manual_file = _manual_classifications_file()
    manual_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {"jobs": []}
    if manual_file.exists():
        with open(manual_file) as f:
            payload = json.load(f)

    jobs = payload.get("jobs", [])
    updated = False
    record = {
        "job_id": job.id,
        "job_name": job.name,
        "decision": decision,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    for idx, existing in enumerate(jobs):
        if existing.get("job_id") == job.id or existing.get("job_name", "").strip().lower() == job.name.strip().lower():
            jobs[idx] = record
            updated = True
            break
    if not updated:
        jobs.append(record)

    with open(manual_file, "w") as f:
        json.dump({"jobs": jobs}, f, indent=2)


def _classify_with_manual_overrides(jobs, threshold):
    """Classify jobs while honoring persisted human decisions."""
    overrides = _load_manual_classifications()
    return classify_jobs(jobs, threshold=threshold, overrides=overrides)


def _find_job(jobs, job_id):
    """Find a job by ID or case-insensitive name."""
    for job in jobs:
        if job.id == job_id or job.name.lower() == job_id.lower():
            return job
    return None


def _build_report(jobs, results, templates):
    """Create a conversion report from audit results."""
    report = ConversionReport()
    result_map = {job.id: result for job, result in results}
    for job in jobs:
        result = result_map[job.id]
        match = match_job_to_template(job, templates)
        report.add_job(job, result, match=match)
    return report


def _prompt_ambiguous_jobs(unsure, templates, config):
    """Prompt the user to resolve ambiguous jobs."""
    for job, result in unsure:
        snippet = job.payload_text[:100]
        signals = ", ".join(result.signals_found[:8]) or "none"
        print(color(f"\nUNSURE: {job.name}", YELLOW))
        print(f"  Schedule: {job.schedule_expr}")
        print(f"  Payload: {snippet}")
        print(f"  Signals: {signals}")

        decision = None
        while decision is None:
            resp = input("Is this job [M]echanical, [R]easoning, or [S]kip? ").strip().lower()
            if resp in ("m", "mechanical"):
                decision = "mechanical"
            elif resp in ("r", "reasoning"):
                decision = "reasoning"
            elif resp in ("s", "skip"):
                decision = "skip"
            else:
                print(color("Enter M, R, or S.", RED))

        try:
            _write_manual_classification(job, decision)
            print(color(f"  Saved manual decision: {decision}", BLUE))
        except PermissionError as exc:
            print(color(f"  Warning: could not save manual decision to {_manual_classifications_file()}: {exc}", YELLOW))
        if decision == "mechanical":
            convert = input("Convert it now? [y/N] ").strip().lower()
            if convert in ("y", "yes"):
                _convert_single(job, templates, config, dry_run=False, strict=False)


def cmd_audit(args):
    """Scan and classify all cron jobs."""
    config = Config.load()
    threshold = args.threshold or config.classification_threshold

    print(color("Scanning cron jobs...", BLUE))
    try:
        jobs = _load_jobs(args)
    except RuntimeError as e:
        print(color(f"Error: {e}", RED))
        return 1
    except FileNotFoundError:
        print(color(f"File not found: {args.file}", RED))
        return 1

    if not jobs:
        print(color("No cron jobs found.", YELLOW))
        return 0

    print(f"Found {len(jobs)} jobs. Classifying...\n")
    results = _classify_with_manual_overrides(jobs, threshold=threshold)

    mechanical = [(j, r) for j, r in results if r.classification == Classification.MECHANICAL]
    reasoning = [(j, r) for j, r in results if r.classification == Classification.REASONING]
    unsure = [(j, r) for j, r in results if r.classification == Classification.UNSURE]

    # Summary
    print(color(f"  Mechanical:  {len(mechanical)} jobs (convertible)", GREEN))
    print(color(f"  Reasoning:   {len(reasoning)} jobs (kept as-is)", RED))
    print(color(f"  Unsure:      {len(unsure)} jobs (need your input)", YELLOW))
    print()

    # Detailed listing
    if mechanical:
        print(color("--- MECHANICAL (convertible) ---", GREEN))
        templates = load_templates()
        for job, result in mechanical:
            match = match_job_to_template(job, templates)
            template_str = f" -> {match.template.name}" if match.template else " (no template match)"
            status = "enabled" if job.enabled else "disabled"
            print(f"  {color(job.name, GREEN)} [{status}]")
            print(f"    Score: mech={result.mechanical_score} reason={result.reasoning_score} conf={result.confidence}")
            print(f"    Template{template_str}")
        print()

    if unsure:
        print(color("--- UNSURE (manual review needed) ---", YELLOW))
        for job, result in unsure:
            print(f"  {color(job.name, YELLOW)}")
            print(f"    Score: mech={result.mechanical_score} reason={result.reasoning_score}")
            signals = ", ".join(result.signals_found[:5])
            print(f"    Signals: {signals}")
        print(color("  Tip: run 'yburn audit --interactive' to classify these one by one", BLUE))
        print()

    if reasoning and args.verbose:
        print(color("--- REASONING (kept as-is) ---", RED))
        for job, result in reasoning:
            print(f"  {job.name}")
        print()

    if unsure and args.interactive:
        templates = load_templates()
        _prompt_ambiguous_jobs(unsure, templates, config)

    # Token savings estimate
    if mechanical:
        # Rough estimate: each mechanical job fires ~1-2x/day, uses ~1000 tokens
        daily_fires = len(mechanical) * 1.5
        monthly_tokens = daily_fires * 30 * 1000
        monthly_cost = monthly_tokens / 1_000_000 * 0.25  # ~haiku pricing
        print(f"Estimated monthly savings: ~${monthly_cost:.2f} in tokens")
        print(f"Speed improvement: ~30s avg -> <1s per converted job")
        print()
        print(f"Run {color('yburn convert --all', BOLD)} to convert mechanical jobs.")

    return 0


def cmd_convert(args):
    """Convert a mechanical job to a local script."""
    config = Config.load()
    templates = load_templates()

    if args.job_id:
        # Convert specific job
        jobs = _load_jobs(args)
        job = _find_job(jobs, args.job_id)
        if not job:
            print(color(f"Job not found: {args.job_id}", RED))
            return 1
        return _convert_single(job, templates, config, args.dry_run, args.strict)

    elif args.all:
        # Convert all mechanical jobs
        jobs = _load_jobs(args)
        results = _classify_with_manual_overrides(jobs, threshold=config.classification_threshold)
        mechanical = [(j, r) for j, r in results if r.classification == Classification.MECHANICAL]

        if not mechanical:
            print(color("No mechanical jobs found to convert.", YELLOW))
            return 0

        print(f"Converting {len(mechanical)} mechanical jobs...\n")
        success_count = 0
        for job, _ in mechanical:
            result = _convert_single(job, templates, config, args.dry_run, args.strict)
            if result == 0:
                success_count += 1
            print()

        print(f"\nConverted {success_count}/{len(mechanical)} jobs.")
        return 0
    else:
        print(color("Specify --job-id <id> or --all", RED))
        return 1


def _convert_single(job, templates, config, dry_run=False, strict=False):
    """Convert a single job."""
    match = match_job_to_template(job, templates)
    if not match.template:
        print(color(f"  No template match for: {job.name}", YELLOW))
        print(f"    Matched keywords: {match.matched_keywords}")
        print(f"    This job needs a custom template (Phase 2)")
        return 1

    # Show preview
    preview = preview_conversion(job, match.template)
    print(preview)

    if dry_run:
        print(color("  [DRY RUN] Would generate script but skipping.", BLUE))
        return 0

    configured, warnings = check_output_config()
    for warning in warnings:
        print(color(f"  Warning: {warning}", YELLOW))
    if strict and not configured:
        print(color("  Error: output channel configuration is required in strict mode.", RED))
        return 1

    existing_script = script_path_for_job(job)
    if existing_script.exists():
        print(color(f"  Skipping: script already exists at {existing_script}", YELLOW))
        return 0

    # Generate script
    result = generate_script(job, match.template)
    if result.success:
        print(color(f"  Generated: {result.script_path}", GREEN))
        print(color(f"  Tip: run 'yburn test {job.name}' to verify output before replacing", BLUE))
        return 0
    else:
        print(color(f"  Failed: {result.error}", RED))
        return 1


def cmd_report(args):
    """Run audit and emit a full conversion report."""
    config = Config.load()
    threshold = args.threshold or config.classification_threshold

    print(color("Scanning cron jobs...", BLUE))
    try:
        jobs = _load_jobs(args)
    except RuntimeError as e:
        print(color(f"Error: {e}", RED))
        return 1
    except FileNotFoundError:
        print(color(f"File not found: {args.file}", RED))
        return 1

    if not jobs:
        print(color("No cron jobs found.", YELLOW))
        return 0

    results = _classify_with_manual_overrides(jobs, threshold=threshold)
    templates = load_templates()
    report = _build_report(jobs, results, templates)
    output = report.render(args.format)
    print(output)

    try:
        auto_path = report.auto_save_markdown()
        print(color(f"\nAuto-saved markdown report: {auto_path}", BLUE))
    except PermissionError as exc:
        print(color(f"\nWarning: could not auto-save markdown report: {exc}", YELLOW))

    if args.output:
        try:
            saved = report.save(Path(args.output), args.format)
            print(color(f"Saved {args.format} report: {saved}", GREEN))
        except PermissionError as exc:
            print(color(f"Warning: could not save report to {args.output}: {exc}", RED))
            return 1

    return 0


def cmd_replace(args):
    """Replace an original cron with a script-based cron."""
    configured, warnings = check_output_config()
    for warning in warnings:
        print(color(f"Warning: {warning}", YELLOW))
    if args.strict and not configured:
        print(color("Error: output channel configuration is required in strict mode.", RED))
        return 1

    jobs = scan_crons()
    job = None
    for j in jobs:
        if j.id == args.job_id or j.name.lower() == args.job_id.lower():
            job = j
            break

    if not job:
        print(color(f"Job not found: {args.job_id}", RED))
        return 1

    # Check if already replaced
    existing = get_replacement_for_job(job.id)
    if existing:
        print(color(f"Job already replaced. Script: {existing.script_path}", YELLOW))
        print(f"Use 'yburn rollback {job.id}' to undo.")
        return 1

    # Find script
    from yburn.converter import SCRIPTS_DIR
    import re
    safe_name = re.sub(r'[^a-z0-9_-]', '-', job.name.lower())
    script_path = SCRIPTS_DIR / f"{safe_name}.py"

    if not script_path.exists():
        print(color(f"No generated script found at {script_path}", RED))
        print(f"Run 'yburn convert {job.id}' first.")
        return 1

    # Show preview
    spec = build_replacement_command(job.id, job.name, job.schedule, str(script_path))
    preview = preview_replacement(
        job.id, job.name, job.schedule, job.schedule_expr, str(script_path)
    )
    print(preview)

    if not args.execute:
        print(color("[DRY RUN - pass --execute to apply changes]", BLUE))
        return 0

    # Confirm
    if not args.yes:
        resp = input("\nProceed with replacement? [y/N] ")
        if resp.lower() not in ("y", "yes"):
            print("Cancelled.")
            return 0

    # Capture original payload before replacement (safe fields only)
    original_payload = {
        "id": job.id,
        "name": job.name,
        "schedule": job.schedule,
        "schedule_expr": job.schedule_expr,
        "enabled": job.enabled,
        "classification": getattr(job, "classification", None),
    }

    # Record the replacement (actual cron creation/disabling done via openclaw)
    replacement = record_replacement(
        job.id, job.name, job.schedule,
        str(script_path), "manual",
        original_payload=original_payload,
        original_enabled=job.enabled,
    )
    print(color(f"Replacement recorded. Status: {replacement.status}", GREEN))
    print("\nTo complete the replacement manually:")
    print("  Add this line to your crontab (run: crontab -e)")
    print(f"  {spec['crontab_entry']}")
    print("  Disable the original OpenClaw job")
    print(f"  {spec['disable_command']}")
    return 0


def cmd_list(args):
    """Show converted jobs and their replacement status."""
    from yburn.converter import SCRIPTS_DIR

    replacements = get_active_replacements()
    if not replacements:
        # Check if scripts exist even without replacements
        if SCRIPTS_DIR.exists():
            scripts = list(SCRIPTS_DIR.glob("*.py"))
            if scripts:
                print(f"Found {len(scripts)} generated script(s) in {SCRIPTS_DIR}:")
                for s in sorted(scripts):
                    print(f"  {s.name}")
                print(f"\nNo active replacements tracked. Use 'yburn replace <job-id>' to activate.")
                return 0

        print(color("No converted jobs or replacements found.", YELLOW))
        return 0

    print(f"Active replacements: {len(replacements)}\n")
    for r in replacements:
        status_icon = "✅" if r.status == "active" else "⏪"
        print(f"  {status_icon} {r.original_job_name}")
        print(f"     Original ID: {r.original_job_id}")
        print(f"     Script: {r.script_path}")
        print(f"     Template: {r.template_name}")
        print(f"     Replaced: {r.replaced_at}")
        if r.new_cron_id:
            print(f"     New cron: {r.new_cron_id}")
        print()

    return 0


def cmd_test(args):
    """Run a converted script once and show output."""
    from yburn.converter import SCRIPTS_DIR
    import subprocess
    import re

    # Find the script
    if args.job_id:
        safe_name = re.sub(r'[^a-z0-9_-]', '-', args.job_id.lower())
        script_path = SCRIPTS_DIR / f"{safe_name}.py"
        if not script_path.exists():
            # Try finding by job ID in replacements
            r = get_replacement_for_job(args.job_id)
            if r:
                script_path = Path(r.script_path)
            else:
                print(color(f"No script found for: {args.job_id}", RED))
                return 1
    else:
        print(color("Specify a job ID or name to test.", RED))
        return 1

    if not script_path.exists():
        print(color(f"Script not found: {script_path}", RED))
        return 1

    print(f"Running: {script_path}\n")
    print("--- Output ---")
    result = subprocess.run(
        [sys.executable, str(script_path)],
        capture_output=False,
    )
    print("--- End ---")
    print(f"\nExit code: {result.returncode}")
    return result.returncode


def cmd_rollback(args):
    """Undo a replacement."""
    if not args.all and not args.job_id:
        print(color("Specify a job ID or use --all to roll back all replacements.", RED))
        return 1

    if args.all:
        active = get_active_replacements()
        if not active:
            print(color("No active replacements to roll back.", YELLOW))
            return 0

        if not args.yes:
            resp = input(f"Roll back {len(active)} replacement(s)? [y/N] ")
            if resp.lower() not in ("y", "yes"):
                print("Cancelled.")
                return 0

        print(f"Rolling back {len(active)} replacement(s)...\n")
        any_failed = False
        for r in active:
            result = rollback_replacement(r.original_job_id)
            status = color("OK", GREEN) if result["success"] else color("FAIL", RED)
            actions = ", ".join(result["actions"]) if result["actions"] else "none"
            print(f"  {r.original_job_name}: {status}")
            print(f"    Actions: {actions}")
            if result["errors"]:
                any_failed = True
                for err in result["errors"]:
                    print(f"    Error: {color(err, RED)}")

        print(f"\nRolled back {len(active)} replacement(s).")
        return 1 if any_failed else 0

    result = rollback_replacement(args.job_id)
    if result["success"]:
        print(color(f"Rolled back replacement for {args.job_id}", GREEN))
        for action in result["actions"]:
            print(f"  {action}")
    else:
        if result["errors"]:
            for err in result["errors"]:
                print(color(err, RED))
        else:
            print(color(f"No active replacement found for: {args.job_id}", RED))
    return 0 if result["success"] else 1


def cmd_version(args):
    """Print version."""
    print(f"yburn {__version__}")
    return 0


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="yburn",
        description="Why burn tokens? Audit AI agent cron jobs and replace mechanical ones with local scripts.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # audit
    p_audit = subparsers.add_parser("audit", help="Scan and classify all cron jobs")
    p_audit.add_argument("-t", "--threshold", type=int, help="Classification threshold (default: 3)")
    p_audit.add_argument("--dry-run", action="store_true", help="Show results without making changes")
    p_audit.add_argument("--interactive", action="store_true", help="Prompt for each unsure job and save manual decisions")
    p_audit.add_argument("-f", "--file", type=str, help="Read cron jobs from JSON file instead of CLI")
    p_audit.set_defaults(func=cmd_audit)

    # convert
    p_convert = subparsers.add_parser("convert", help="Convert a mechanical job to a local script")
    p_convert.add_argument("job_id", nargs="?", help="Job ID or name to convert")
    p_convert.add_argument("--all", action="store_true", help="Convert all mechanical jobs")
    p_convert.add_argument("--dry-run", action="store_true", help="Preview without generating")
    p_convert.add_argument("--strict", action="store_true", help="Require full output channel configuration")
    p_convert.add_argument("-f", "--file", type=str, help="Read cron jobs from JSON file instead of CLI")
    p_convert.set_defaults(func=cmd_convert)

    # report
    p_report = subparsers.add_parser("report", help="Run audit and generate a conversion report")
    p_report.add_argument("--format", choices=["markdown", "json", "terminal"], default="terminal")
    p_report.add_argument("--output", type=str, help="Save to a specific file")
    p_report.add_argument("-t", "--threshold", type=int, help="Classification threshold (default: 3)")
    p_report.add_argument("-f", "--file", type=str, help="Read cron jobs from JSON file instead of CLI")
    p_report.set_defaults(func=cmd_report)

    # replace
    p_replace = subparsers.add_parser("replace", help="Replace original cron with script-based cron")
    p_replace.add_argument("job_id", help="Job ID to replace")
    p_replace.add_argument("--execute", action="store_true", help="Actually execute the replacement (default is dry-run)")
    p_replace.add_argument("-y", "--yes", action="store_true", help="Skip confirmation")
    p_replace.add_argument("--strict", action="store_true", help="Require full output channel configuration")
    p_replace.set_defaults(func=cmd_replace)

    # list
    p_list = subparsers.add_parser("list", help="Show converted jobs and replacement status")
    p_list.set_defaults(func=cmd_list)

    # test
    p_test = subparsers.add_parser("test", help="Run a converted script once, show output")
    p_test.add_argument("job_id", help="Job ID or name to test")
    p_test.set_defaults(func=cmd_test)

    # rollback
    p_rollback = subparsers.add_parser("rollback", help="Undo a replacement")
    p_rollback.add_argument("job_id", nargs="?", help="Original job ID to rollback")
    p_rollback.add_argument("--all", action="store_true", help="Roll back all active replacements")
    p_rollback.add_argument("-y", "--yes", action="store_true", help="Skip confirmation")
    p_rollback.set_defaults(func=cmd_rollback)

    # version
    p_version = subparsers.add_parser("version", help="Print version")
    p_version.set_defaults(func=cmd_version)

    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.WARNING)

    if hasattr(args, "func"):
        return args.func(args)
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
