"""Classification engine for yburn.

Classifies cron jobs as MECHANICAL, REASONING, or UNSURE using
weighted keyword scoring. Zero API calls - pure pattern matching.
"""

import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Tuple

from yburn.scanner import CronJob

logger = logging.getLogger(__name__)


class Classification(Enum):
    """Classification result for a cron job."""
    MECHANICAL = "mechanical"
    REASONING = "reasoning"
    UNSURE = "unsure"


# Keyword signal lists with weights
STRONG_MECHANICAL = {
    "check", "status", "health", "ping", "monitor", "backup", "cleanup",
    "rotate", "prune", "archive", "kill", "restart", "vacuum", "verify",
    "diagnostic", "diagnostics", "snapshot", "rollback", "reindex",
    "sync", "deploy", "terminate", "compress", "heartbeat", "spawn",
    "fetch", "validate", "build", "script", "poll", "watch", "purge", "flush",
}
WEAK_MECHANICAL = {
    "count", "list", "report", "disk", "uptime", "memory", "git", "push",
    "commit", "copy", "delete", "move", "execute", "cron",
    "index", "sweep", "flag", "clean", "reset", "size", "maintenance",
    "alert", "notify", "send", "port", "firewall", "process", "pid",
    "configure", "queue", "load", "parse", "lock", "unlock", "scan",
    "collect", "download", "upload", "trigger", "retry", "daily", "run", "job",
}
STRONG_REASONING = {
    "analyze", "recommend", "draft", "write", "evaluate", "decide",
    "strategy", "synthesize", "create", "compose", "research", "advise",
    "reflect", "propose", "generate", "counsel", "creative",
    "interpret", "brainstorm", "optimize", "forecast", "predict",
    "assess", "critique", "strategize", "insights", "plan", "coach",
}
WEAK_REASONING = {
    "review", "summarize", "compare", "prioritize", "brief", "morning",
    "trends", "competitive", "weekly",
    "learning", "improvement", "audit", "intelligence", "mentor",
    "self-improvement", "debrief", "agenda", "pipeline", "content",
    "publish", "newsletter", "blog", "tweet", "engagement",
    "outline", "explore", "curate", "ideate", "detect", "facilitate",
    "imagine", "update",
}

# Shell command patterns that indicate mechanical work
SHELL_PATTERNS = [
    r'\bpython3?\s+', r'\bbash\s+', r'\bgit\s+(add|commit|push|pull|clone)',
    r'\bcp\s+', r'\bmv\s+', r'\brm\s+', r'\bcurl\s+', r'\bnpm\s+',
    r'\bopenclaw\s+(cron|memory|update)', r'\bsecurity\s+find-generic-password',
    r'\bwc\s+-', r'\bgrep\s+', r'\bsession_status\s+tool',
]


@dataclass
class ClassificationResult:
    """Result of classifying a single cron job."""
    classification: Classification
    mechanical_score: int
    reasoning_score: int
    confidence: float
    signals_found: List[str]


def _tokenize(text: str) -> List[str]:
    """Tokenize text into lowercase words, splitting on hyphens too."""
    # First split hyphenated words, then extract tokens
    expanded = text.lower().replace("-", " ").replace("_", " ")
    return re.findall(r'[a-z][a-z0-9]*', expanded)


def _has_shell_commands(text: str) -> bool:
    """Check if text contains shell command patterns."""
    for pattern in SHELL_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False


def classify_job(job: CronJob, threshold: int = 3) -> ClassificationResult:
    """Classify a single cron job as mechanical, reasoning, or unsure.

    Uses weighted keyword scoring on the job's payload text and name.
    Zero API calls.

    Args:
        job: The CronJob to classify.
        threshold: Score gap required for definitive classification.

    Returns:
        ClassificationResult with scores, confidence, and signals.
    """
    text = f"{job.name} {job.payload_text}".lower()
    tokens = set(_tokenize(text))

    mechanical_score = 0
    reasoning_score = 0
    signals = []

    # Score keywords
    for token in tokens:
        if token in STRONG_MECHANICAL:
            mechanical_score += 2
            signals.append(f"mechanical:{token}(+2)")
        elif token in WEAK_MECHANICAL:
            mechanical_score += 1
            signals.append(f"mechanical:{token}(+1)")

        if token in STRONG_REASONING:
            reasoning_score += 2
            signals.append(f"reasoning:{token}(+2)")
        elif token in WEAK_REASONING:
            reasoning_score += 1
            signals.append(f"reasoning:{token}(+1)")

    # Heuristic: shell commands boost mechanical
    if _has_shell_commands(job.payload_text):
        mechanical_score += 2
        signals.append("heuristic:shell_commands(+2)")

    # Heuristic: model-based boosts
    model_lower = job.model.lower()
    if "haiku" in model_lower:
        mechanical_score += 1
        signals.append("heuristic:haiku_model(+1)")
    elif "opus" in model_lower:
        reasoning_score += 1
        signals.append("heuristic:opus_model(+1)")
        if reasoning_score > mechanical_score:
            reasoning_score += 1
            signals.append("heuristic:opus_reasoning_bias(+1)")

    # Heuristic: systemEvent with simple commands is likely mechanical
    if job.payload_kind == "systemEvent":
        if _has_shell_commands(job.payload_text):
            mechanical_score += 1
            signals.append("heuristic:systemEvent_with_commands(+1)")

    # Heuristic: agentTurn that explicitly runs a script and does nothing else
    if job.payload_kind == "agentTurn" and _has_shell_commands(job.payload_text):
        # Check if the prompt is mostly "run this script, report if error"
        text_len = len(job.payload_text)
        if text_len < 300:
            mechanical_score += 2
            signals.append("heuristic:short_script_runner(+2)")

    # Classification decision
    if mechanical_score >= reasoning_score + threshold:
        classification = Classification.MECHANICAL
    elif reasoning_score >= mechanical_score + threshold:
        classification = Classification.REASONING
    else:
        classification = Classification.UNSURE

    # Confidence: how decisive is the gap
    total = mechanical_score + reasoning_score
    confidence = abs(mechanical_score - reasoning_score) / max(total, 1)

    return ClassificationResult(
        classification=classification,
        mechanical_score=mechanical_score,
        reasoning_score=reasoning_score,
        confidence=round(confidence, 2),
        signals_found=signals,
    )


def classify_jobs(
    jobs: List[CronJob], threshold: int = 3, overrides: Optional[Dict[str, str]] = None
) -> List[Tuple[CronJob, ClassificationResult]]:
    """Classify a list of cron jobs.

    Args:
        jobs: List of CronJob instances.
        threshold: Score gap required for definitive classification.

    Returns:
        List of (CronJob, ClassificationResult) tuples.
    """
    results = []
    for job in jobs:
        result = classify_job(job, threshold)
        override = _get_override(job, overrides)
        if override:
            result = apply_manual_override(result, override)
        results.append((job, result))
        logger.debug(
            "Job '%s': %s (mech=%d, reason=%d)",
            job.name, result.classification.value,
            result.mechanical_score, result.reasoning_score,
        )
    return results


def _get_override(job: CronJob, overrides: Optional[Dict[str, str]]) -> Optional[str]:
    """Find a manual override for a job by ID or normalized name."""
    if not overrides:
        return None

    name_key = f"name:{job.name.strip().lower()}"
    if job.id in overrides:
        return overrides[job.id]
    if name_key in overrides:
        return overrides[name_key]
    return None


def apply_manual_override(
    result: ClassificationResult,
    decision: str,
) -> ClassificationResult:
    """Apply a human classification override to a classifier result."""
    normalized = decision.strip().lower()
    if normalized == "mechanical":
        classification = Classification.MECHANICAL
        confidence = 1.0
    elif normalized == "reasoning":
        classification = Classification.REASONING
        confidence = 1.0
    elif normalized == "skip":
        classification = Classification.UNSURE
        confidence = result.confidence
    else:
        return result

    signals = list(result.signals_found)
    signals.append(f"manual:{normalized}")
    return ClassificationResult(
        classification=classification,
        mechanical_score=result.mechanical_score,
        reasoning_score=result.reasoning_score,
        confidence=confidence,
        signals_found=signals,
    )


def print_summary(results: List[Tuple[CronJob, ClassificationResult]]) -> str:
    """Format a summary of classification results.

    Args:
        results: List of (CronJob, ClassificationResult) tuples.

    Returns:
        Formatted summary string.
    """
    mechanical = [(j, r) for j, r in results if r.classification == Classification.MECHANICAL]
    reasoning = [(j, r) for j, r in results if r.classification == Classification.REASONING]
    unsure = [(j, r) for j, r in results if r.classification == Classification.UNSURE]

    lines = [
        f"Classification Results:",
        f"  Mechanical:  {len(mechanical)} jobs",
        f"  Reasoning:   {len(reasoning)} jobs",
        f"  Unsure:      {len(unsure)} jobs",
        f"  Total:       {len(results)} jobs",
        "",
    ]

    for label, group in [("MECHANICAL", mechanical), ("REASONING", reasoning), ("UNSURE", unsure)]:
        if group:
            lines.append(f"--- {label} ---")
            for job, result in group:
                top_signals = result.signals_found[:3]
                signals_str = ", ".join(top_signals)
                lines.append(
                    f"  {job.name}: mech={result.mechanical_score} "
                    f"reason={result.reasoning_score} "
                    f"conf={result.confidence} [{signals_str}]"
                )
            lines.append("")

    return "\n".join(lines)
