#!/usr/bin/env python3
"""
Self-Audit Layer 3: External Mirror
Feeds an AI assistant's recent session summaries to a different model for blind spot detection.

This is the core of The Reasoning Loop - using a different AI architecture to review
the primary AI's work and find patterns it can't see from inside its own reasoning.

Usage:
  python3 mirror-script.py [--days 7]

Requirements:
  - Gemini API key (free tier works)
  - Session logs or daily summary files

Output:
  - Writes findings to a staging file for review
  - Optionally sends summary via notification (Telegram, Slack, etc.)

Zero primary-model tokens burned. External model API only.

Adapt the paths, API endpoints, and notification methods to your setup.
"""

import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

# ============================================================
# CONFIGURATION - Adapt these to your setup
# ============================================================

# How many days of logs to review
DAYS = int(sys.argv[sys.argv.index('--days') + 1]) if '--days' in sys.argv else 7

# Where your daily logs live
DAILY_LOG_DIR = Path("./logs/daily")  # Adapt to your path

# Where your corrections/mistakes log lives
CORRECTIONS_FILE = Path("./logs/corrections.md")  # Adapt to your path

# Where to write the output
OUTPUT_FILE = Path("./staging/mirror-report.md")

# Your Gemini API key (or any external model API)
# Store securely - don't hardcode in production
API_KEY = os.environ.get("GEMINI_API_KEY", "")

# Gemini model to use (free tier)
GEMINI_MODEL = "gemini-2.5-flash"

# ============================================================
# DATA COLLECTION
# ============================================================

def collect_daily_logs(days):
    """Collect daily log content from the last N days."""
    logs = []
    today = datetime.now()
    for i in range(days):
        date = today - timedelta(days=i)
        date_str = date.strftime("%Y-%m-%d")
        log_path = DAILY_LOG_DIR / f"{date_str}.md"
        if log_path.exists():
            content = log_path.read_text()
            # Truncate very long logs to avoid token limits
            if len(content) > 8000:
                content = content[:8000] + "\n\n[TRUNCATED - full log is longer]"
            logs.append(f"## {date_str}\n{content}")
    return "\n\n---\n\n".join(logs)


def collect_corrections():
    """Get recent corrections/mistakes."""
    if CORRECTIONS_FILE.exists():
        content = CORRECTIONS_FILE.read_text()
        if len(content) > 4000:
            content = content[-4000:]  # Last 4000 chars (most recent)
        return content
    return "No corrections file found."


# ============================================================
# EXTERNAL MODEL CALL
# ============================================================

def call_gemini(prompt, api_key):
    """Call Gemini for analysis. Replace with any external model API."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={api_key}"

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.3,
            "maxOutputTokens": 8000
        }
    }

    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})

    try:
        response = urllib.request.urlopen(req, timeout=60)
        result = json.loads(response.read())
        return result["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as e:
        return f"API error: {e}"


# ============================================================
# THE AUDIT PROMPT
# ============================================================

def build_prompt(daily_logs, corrections, days):
    """Build the external review prompt. This is the core of the system."""
    return f"""You are an external auditor reviewing an AI assistant's work from the past {days} days.

RULES:
- Do NOT praise good work. Only flag problems.
- Be specific. Name the pattern, cite the evidence (dates, examples), suggest the fix.
- Look for gaps between what the assistant says it'll do and what it actually does.
- Look for the same type of mistake appearing multiple times.
- Look for behaviors that seem confident but might be wrong.
- Look for things the assistant should be catching but isn't.
- If you find nothing concerning, say so briefly. Don't manufacture issues.

Here are the assistant's daily logs from the last {days} days:

{daily_logs}

Here are recent corrections and mistakes:

{corrections}

YOUR ANALYSIS:
For each pattern you find, provide:
1. Pattern name
2. Evidence (specific dates and examples from the logs)
3. Why the assistant likely can't see this itself
4. Concrete fix (what rule, checklist, or directive change would prevent it)

Focus on operational patterns, not philosophical observations."""


# ============================================================
# MAIN
# ============================================================

def main():
    print(f"Self-Audit Mirror: analyzing last {DAYS} days...")

    if not API_KEY:
        print("ERROR: Set GEMINI_API_KEY environment variable")
        sys.exit(1)

    # Collect data
    daily_logs = collect_daily_logs(DAYS)
    corrections = collect_corrections()

    if not daily_logs:
        print("No daily logs found for the period. Nothing to audit.")
        sys.exit(0)

    # Build and send the prompt
    prompt = build_prompt(daily_logs, corrections, DAYS)

    print("Calling external model for analysis...")
    analysis = call_gemini(prompt, API_KEY)

    # Write output
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    report = f"""# Self-Audit Mirror Report
# Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}
# Reviewer: {GEMINI_MODEL}
# Period: Last {DAYS} days

{analysis}
"""
    OUTPUT_FILE.write_text(report)
    print(f"Report written to {OUTPUT_FILE}")

    # Optional: send notification
    # Adapt this to your notification system (Telegram, Slack, email, etc.)
    # send_notification(f"Self-Audit Mirror: {DAYS}-day review complete. Check {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
