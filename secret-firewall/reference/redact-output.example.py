#!/usr/bin/env python3
"""redact-output.example.py - PostToolUse hook that redacts secret patterns from
a tool's OUTPUT before the model sees it.

Defense in depth. The egress gate stops secrets going OUT; this stops a secret
that surfaced in tool output (a command that echoed an environment variable, a
config dump) from entering the model's context in the clear, where it could then
be paraphrased back out past the gate.

It replaces each matched token with a fixed, type-labeled sentinel that reveals
nothing but tells the model what was redacted. If nothing matches, the hook
emits nothing (the original output is preserved). Any parse error exits 0: it
can never block a tool call.

CONTRACT (a PostToolUse-style hook)
  stdin : JSON envelope { tool_name, tool_response, ... }.
  stdout: JSON { hookSpecificOutput: { updatedToolOutput: <string> } } when a
          redaction happened, otherwise nothing.

Patterns are the high-precision provider shapes, anchored so they do not
false-positive on prose. Add patterns conservatively; over-matching turns useful
output into noise. This is a pattern-only pass by design (no secret-value index):
a PostToolUse redactor runs on every tool result and must stay cheap.
"""

import json
import re
import sys

# (compiled regex, replacement). Replacement keeps the token TYPE so the model
# still understands what was removed.
PATTERNS = [
    (re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"), "sk-ant-REDACTED"),
    (re.compile(r"\bsk-proj-[A-Za-z0-9_-]{20,}"), "sk-proj-REDACTED"),
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"), "sk-REDACTED"),
    (re.compile(r"\bgh[psour]_[A-Za-z0-9]{36,}\b"), "gh_REDACTED"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{60,}\b"), "github_pat_REDACTED"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), "AIza_REDACTED"),
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "AWS_KEY_REDACTED"),
    (re.compile(r"\b(?:sk|rk)_(?:live|test)_[0-9A-Za-z]{20,}\b"), "stripe_REDACTED"),
    (re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b"), "BOTTOKEN_REDACTED"),
]


def redact(text):
    """Apply all patterns. Returns (redacted_text, count)."""
    if not isinstance(text, str):
        return text, 0
    count = 0
    for pat, rep in PATTERNS:
        text, n = pat.subn(rep, text)
        count += n
    return text, count


def main():
    try:
        envelope = json.load(sys.stdin)
    except Exception:
        return 0

    response = envelope.get("tool_response")
    if not isinstance(response, dict):
        return 0

    # Redact whichever common string output fields are present.
    total = 0
    for field in ("stdout", "stderr", "output", "content"):
        if field in response and isinstance(response[field], str):
            new_text, n = redact(response[field])
            if n:
                response[field] = new_text
                total += n

    if total == 0:
        return 0

    sys.stdout.write(json.dumps({
        "hookSpecificOutput": {"updatedToolOutput": json.dumps(response)}
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
