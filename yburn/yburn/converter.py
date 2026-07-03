"""Converter for yburn - matches classified jobs to templates and generates scripts.

Takes a classified mechanical job, finds the best matching template,
fills in parameters, and generates a standalone Python script.
"""

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from yburn.classifier import Classification, ClassificationResult
from yburn.scanner import CronJob

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"
SCRIPTS_DIR = Path.home() / ".yburn" / "scripts"


def get_scripts_dir() -> Path:
    """Return the configured scripts output directory."""
    override = os.environ.get("YBURN_SCRIPTS_DIR")
    if override:
        return Path(override).expanduser()
    return SCRIPTS_DIR


def check_output_config() -> Tuple[bool, List[str]]:
    """Validate optional output channel environment configuration."""
    token_set = bool(os.environ.get("YBURN_TELEGRAM_TOKEN"))
    chat_id_set = bool(os.environ.get("YBURN_TELEGRAM_CHAT_ID"))
    discord_set = bool(os.environ.get("YBURN_DISCORD_WEBHOOK"))
    slack_set = bool(os.environ.get("YBURN_SLACK_WEBHOOK"))
    warnings = []

    output_configured = (token_set and chat_id_set) or discord_set or slack_set

    if not output_configured and not token_set and not chat_id_set:
        warnings.append(
            "No notification channel configured - script will print to stdout. "
            "To add Telegram alerts, set YBURN_TELEGRAM_TOKEN and YBURN_TELEGRAM_CHAT_ID. "
            "(This is optional - scripts work without it.)"
        )
    elif token_set and not chat_id_set:
        warnings.append("Missing YBURN_TELEGRAM_CHAT_ID - script will output to stdout only")
    elif chat_id_set and not token_set:
        warnings.append("Missing YBURN_TELEGRAM_TOKEN - script will output to stdout only")

    return output_configured, warnings


@dataclass
class TemplateManifest:
    """Parsed template manifest."""
    name: str
    description: str
    version: str
    match_keywords: List[str]
    replaces_patterns: List[str]
    parameters: List[Dict]
    output_format: str
    requires: List[str]
    path: Path  # Directory containing the template


@dataclass
class MatchResult:
    """Result of matching a job against templates."""
    template: Optional[TemplateManifest]
    score: int
    matched_keywords: List[str]
    matched_patterns: List[str]


@dataclass
class ConversionResult:
    """Result of converting a job to a standalone script."""
    job: CronJob
    template: TemplateManifest
    script_path: Path
    script_content: str
    success: bool
    error: Optional[str] = None


def script_path_for_job(job: CronJob, output_dir: Optional[Path] = None) -> Path:
    """Return the generated script path for a job."""
    out_dir = output_dir or get_scripts_dir()
    safe_name = re.sub(r'[^a-z0-9_-]', '-', job.name.lower())
    return out_dir / f"{safe_name}.py"


def load_templates(templates_dir: Optional[Path] = None) -> List[TemplateManifest]:
    """Load all template manifests from the templates directory.

    Args:
        templates_dir: Override templates directory (for testing).

    Returns:
        List of parsed TemplateManifest objects.
    """
    tdir = templates_dir or TEMPLATES_DIR
    templates = []

    for entry in sorted(tdir.iterdir()):
        manifest_path = entry / "manifest.json"
        if entry.is_dir() and manifest_path.exists():
            try:
                with open(manifest_path) as f:
                    data = json.load(f)
                templates.append(TemplateManifest(
                    name=data["name"],
                    description=data.get("description", ""),
                    version=data.get("version", "1.0"),
                    match_keywords=data.get("match_keywords", []),
                    replaces_patterns=data.get("replaces_patterns", []),
                    parameters=data.get("parameters", []),
                    output_format=data.get("output_format", "plain"),
                    requires=data.get("requires", []),
                    path=entry,
                ))
                logger.debug("Loaded template: %s", data["name"])
            except Exception:
                logger.exception("Failed to load template from %s", entry)

    logger.info("Loaded %d templates", len(templates))
    return templates


def match_job_to_template(
    job: CronJob,
    templates: List[TemplateManifest],
    min_score: int = 3,
    require_pattern_match: bool = False,
) -> MatchResult:
    """Find the best matching template for a job.

    Scores templates by keyword overlap between job payload/name
    and template match_keywords + replaces_patterns.

    Args:
        job: The classified CronJob.
        templates: Available templates.
        min_score: Minimum score to consider a match.
        require_pattern_match: When True, only return a match if at least
            one replaces_pattern matched (not just keywords).

    Returns:
        MatchResult with best template (or None if no match).
    """
    job_text = f"{job.name} {job.payload_text}".lower()
    job_tokens = set(re.findall(r'[a-z][a-z0-9]*', job_text.replace("-", " ").replace("_", " ")))

    best_template = None
    best_score = 0
    best_keywords = []
    best_patterns = []

    for template in templates:
        score = 0
        matched_kw = []
        matched_pat = []

        # Keyword matching
        for kw in template.match_keywords:
            if kw.lower() in job_tokens:
                score += 1
                matched_kw.append(kw)

        # Pattern matching (substring in job text)
        for pattern in template.replaces_patterns:
            if pattern.lower() in job_text:
                score += 2  # Patterns are worth more
                matched_pat.append(pattern)

        if score > best_score:
            best_score = score
            best_template = template
            best_keywords = matched_kw
            best_patterns = matched_pat

    if best_score >= min_score and best_template:
        if require_pattern_match and not best_patterns:
            return MatchResult(
                template=None,
                score=best_score,
                matched_keywords=best_keywords,
                matched_patterns=best_patterns,
            )
        return MatchResult(
            template=best_template,
            score=best_score,
            matched_keywords=best_keywords,
            matched_patterns=best_patterns,
        )
    else:
        return MatchResult(
            template=None,
            score=best_score,
            matched_keywords=best_keywords,
            matched_patterns=best_patterns,
        )


def generate_script(
    job: CronJob,
    template: TemplateManifest,
    config_overrides: Optional[Dict] = None,
    output_dir: Optional[Path] = None,
) -> ConversionResult:
    """Generate a standalone script from a template for a specific job.

    Reads the template's script.py, customizes the CONFIG dict with
    job-specific values, and writes the standalone script.

    Args:
        job: The cron job being converted.
        template: The matched template.
        config_overrides: Optional parameter overrides.
        output_dir: Override output directory (default: ~/.yburn/scripts/).

    Returns:
        ConversionResult with the generated script path and content.
    """
    script_path = template.path / "script.py"
    if not script_path.exists():
        return ConversionResult(
            job=job, template=template, script_path=Path(""),
            script_content="", success=False,
            error=f"Template script not found: {script_path}",
        )

    try:
        with open(script_path) as f:
            script_content = f.read()
    except Exception as e:
        return ConversionResult(
            job=job, template=template, script_path=Path(""),
            script_content="", success=False,
            error=f"Failed to read template: {e}",
        )

    # Apply config overrides to the CONFIG dict in the script
    if config_overrides:
        script_content = _apply_config_overrides(script_content, config_overrides)

    # Add header comment
    header = (
        f"# Generated by yburn from template: {template.name}\n"
        f"# Original cron job: {job.name} (ID: {job.id})\n"
        f"# Schedule: {job.schedule_expr}\n"
        f"#\n"
    )
    script_content = header + script_content

    # Determine output path
    out_dir = output_dir or get_scripts_dir()
    out_path = script_path_for_job(job, out_dir)

    if out_path.exists():
        return ConversionResult(
            job=job, template=template, script_path=out_path,
            script_content=script_content, success=False,
            error=f"Script already exists: {out_path}",
        )

    # Write the script
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w") as f:
            f.write(script_content)
        os.chmod(out_path, 0o755)
        logger.info("Generated script: %s", out_path)
    except Exception as e:
        return ConversionResult(
            job=job, template=template, script_path=out_path,
            script_content=script_content, success=False,
            error=f"Failed to write script: {e}",
        )

    return ConversionResult(
        job=job, template=template, script_path=out_path,
        script_content=script_content, success=True,
    )


def _apply_config_overrides(script_content: str, overrides: Dict) -> str:
    """Apply config overrides to the CONFIG dict in a script.

    Simple approach: find CONFIG dict lines and update values.

    Args:
        script_content: The raw script content.
        overrides: Key-value pairs to override.

    Returns:
        Modified script content.
    """
    for key, value in overrides.items():
        # Match pattern: "key": old_value, or "key": old_value
        pattern = rf'("{key}":\s*)(.*?)([,\n}}])'
        replacement_val = json.dumps(value)
        # Try to replace in CONFIG dict
        new_content = re.sub(pattern, rf'\g<1>{replacement_val}\3', script_content)
        if new_content != script_content:
            script_content = new_content
            logger.debug("Applied override: %s = %s", key, replacement_val)
    return script_content


def preview_conversion(
    job: CronJob,
    template: TemplateManifest,
    config_overrides: Optional[Dict] = None,
) -> str:
    """Generate a preview of what the conversion would produce.

    Args:
        job: The cron job.
        template: The matched template.
        config_overrides: Optional parameter overrides.

    Returns:
        Formatted preview string.
    """
    lines = [
        f"=== Conversion Preview ===",
        f"Job:      {job.name} ({job.id})",
        f"Template: {template.name} v{template.version}",
        f"Schedule: {job.schedule_expr}",
        f"",
        f"Description: {template.description}",
        f"",
        f"Parameters:",
    ]

    for param in template.parameters:
        override_val = (config_overrides or {}).get(param["name"])
        if override_val is not None:
            lines.append(f"  {param['name']}: {override_val} (custom)")
        elif param.get("default") is not None:
            lines.append(f"  {param['name']}: {param['default']} (default)")
        elif param.get("required"):
            lines.append(f"  {param['name']}: ⚠️ REQUIRED - not set")
        else:
            lines.append(f"  {param['name']}: (not set)")

    safe_name = re.sub(r'[^a-z0-9_-]', '-', job.name.lower())
    lines.extend([
        f"",
        f"Output: ~/.yburn/scripts/{safe_name}.py",
        f"========================",
    ])

    return "\n".join(lines)
