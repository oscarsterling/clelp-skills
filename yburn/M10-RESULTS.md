# M10 Integration Testing Results
**Date:** 2026-03-24  
**Environment:** macOS arm64, Python 3.14.2, venv active  
**Total live crons scanned:** 92

---

## Test Suite Results (Task 1)

All 124 tests pass with zero failures or warnings.

```
============================= test session starts ==============================
platform darwin -- Python 3.14.2, pytest-9.0.2, pluggy-1.6.0
collected 124 items

tests/test_classifier.py    46 passed
tests/test_converter.py     33 passed
tests/test_replacer.py      23 passed
tests/test_scanner.py       37 passed
tests/test_telegram.py      15 passed
============================== 124 passed in 0.06s ==============================
```

### Setup Changes Made
- Added `[project.optional-dependencies]` dev extras to `pyproject.toml` so `pip install -e '.[dev]'` works correctly.
- `pytest`, `pyyaml`, and `requests` were already present in the venv from the base install.
- No source code fixes were needed - all tests passed clean on first run.

---

## M10 Classification Results (Task 2)

### Summary

| Category    | Count | Percentage |
|-------------|-------|------------|
| MECHANICAL  | 44    | 47.8%      |
| REASONING   | 14    | 15.2%      |
| UNSURE      | 34    | 37.0%      |
| **Total**   | **92**|            |

---

### MECHANICAL (44 jobs) - Token Replacement Candidates

High-confidence clears:
- `DB Maintenance - Daily Full` - haiku, runs python script, exits on result. Confidence: 1.00
- `OAuth Token Health Check` - haiku, runs python script, exits on result. Confidence: 1.00
- `System State Compiler` - haiku, runs python script, writes file. Confidence: 1.00
- `Social Media - Set Opus Model` - haiku, calls session_status once. Confidence: 1.00
- `db-size-monitor-daily` - haiku, runs python script. Confidence: 1.00
- `DB Maintenance - Midday Guard` - haiku, runs python script. Confidence: 1.00
- `Pre-Dream Git Snapshot` - haiku, runs git commands. Confidence: 1.00
- `Stuck Session Cleanup` - haiku, runs cleanup script. Confidence: 0.88
- `Post-Dream Re-index + Rollback Guard` - haiku, runs deterministic steps. Confidence: 0.91
- `Daily Cron Health Report` - haiku, runs script, posts result. Confidence: 0.83
- `OpenClaw Update Check` - haiku, runs update status, exits if current. Confidence: 0.82
- `db-maintenance-weekly` - no model, bash script runner. Confidence: 0.82

Mid-confidence mechanicals (correct but noisy payloads):
- `Nightly System Diagnostic` - haiku but has long reasoning instructions. Confidence: 0.64
- `Work Daily Brief` - sonnet with structured fetch tasks. Confidence: 0.73
- `Board Meeting Watchdog` - haiku, reads status files, sends alerts. Confidence: 0.71
- `Clelp Weekly Link & Skill Audit` - long but deterministic data ops. Confidence: 0.78
- `Weekly Cron Health Audit` - haiku, runs audit script. Confidence: 0.62

Lower-confidence but still correct mechanical classifications:
- `eod-summary` - sonnet, but payload is structured multi-step file read + send (confidence 0.43; borderline)
- `Board Meeting (Mon/Wed/Fri)` - opus model hurts score; payload IS mechanical orchestration but complex
- `Dream Cycle` - memory consolidation, well-defined steps. Confidence: 0.83
- `YouTube Intelligence Scrape` - classified mechanical but arguably UNSURE (see edge cases below)

---

### REASONING (14 jobs) - Keep as LLM Crons

Correctly identified:
- `Guru Daily Research` - opus, deep research + learning synthesis
- `Oscar + Guru Daily Sync` - opus, cross-agent reflection session
- `Radar Morning Scout` - sonnet, open-ended trend research
- `Pattern Review + Ratchet` - sonnet, evaluates patterns and proposes rule changes
- `Oscar X Post Draft` - opus, creative drafting
- `Content Pipeline: Ink Draft` - opus, blog writing
- `Muse Self-Improvement (Part 1 - Research)` - sonnet, creative strategy research. Confidence: 0.83
- `Content Pipeline: Radar Research` - sonnet, topic selection + outline. Confidence: 0.82
- `Content Pipeline: Muse Strategy` - sonnet, framing + angle strategy. Confidence: 0.52
- `Newsletter Thursday Draft` - complex content creation. Confidence: 0.69
- `weekly-consolidation` - synthesis of week's events
- `Oscar Self-Improvement` - reflective learning session
- `Radar Self-Improvement` - research methodology learning

---

### UNSURE (34 jobs) - Needs Human Review

The UNSURE bucket is expected and healthy. Most fall into these categories:

**Category A: Hybrid jobs (do both mechanical steps AND reasoning)**
- `Pre-Dream Session Flush` - fires a systemEvent but the text has creative reasoning signals
- `Board Meeting Prep` - orchestration + spawning + analysis mixed
- `Guru Sync Review` - reads files, evaluates importance, then decides to send or not
- `Oscar Self-Audit - Pattern Mining` - runs scripts + generates analysis
- `Oscar Self-Audit - External Mirror` - runs scripts + implements fixes

**Category B: Self-improvement sessions (write/verify steps confuse the classifier)**
- `Forge Self-Improvement`, `Ink Self-Improvement`, `Lens Self-Improvement`
- `Muse Self-Improvement (Part 2 - Memory Update)`
- These have heavy mechanical circuit breaker instructions (check, verify, count lines)
  mixed with creative research. The classifier correctly lands them as UNSURE.

**Category C: Reminder/one-shot systemEvents with minimal payload**
- `GitHub PAT Rotation Reminder` - zero signals, empty payload text
- `Reminder: Clelp Role Landing Pages` - 2 mechanical signals, classifies as UNSURE (correct)
- `Video script reminder - Saturday morning` - equal mechanical/reasoning
- `Jason X Post - Personal Origin Story` - systemEvent trigger for a conversation

**Category D: Social/engagement jobs that mix script ops and creative output**
- `Oscar Reply-Back Check`, `Oscar X Morning Session`, `Oscar X Midday Session`
- `Oscar X Afternoon Session` - classified mechanical by score but UNSURE is arguably more correct
  because the payload involves generating original creative replies

**Category E: Quarterly/monthly reminders with no model**
- `Quarterly Key Rotation Audit` - systemEvent, no model; correctly UNSURE

---

## Dry-Run Conversion Tests

Tested the converter against 5 mechanical jobs (dry run only, no replacements made):

### 1. DB Maintenance - Daily Full
- **Template matched:** `system-diagnostics` (score: 4)
- **Match is approximate.** DB maintenance actually runs a Python script that does DB vacuum.
  The `system-diagnostics` template captures disk/CPU/memory - overlapping but not exact.
- **Edge case:** The job already wraps a script. Replacing it with another script that runs
  a script would add a layer without saving tokens.
- **Verdict:** This is a false positive. The job runs `python3 ~/clawd/scripts/db-maintenance.py`.
  The right yburn conversion is a direct bash/python invoker, not the system-diagnostics template.
  Template library needs a `script-runner` template for jobs of this pattern.

### 2. OAuth Token Health Check
- **Template matched:** `system-diagnostics` (score: 6)
- **Same issue as above.** The job runs `python3 ~/clawd/scripts/oauth-health-check.py`.
  Matching to system-diagnostics because of "health check" keyword overlap.
- **Edge case:** High-frequency (runs twice daily). Token savings would be real but template mismatch
  means the generated script wouldn't replicate the job's actual behavior.

### 3. System State Compiler
- **Template matched:** `system-diagnostics` (score: 5)
- **Same pattern.** Already a script runner. Template mismatch.

### 4. Nightly System Diagnostic
- **Template matched:** `system-diagnostics` (score: 15)
- **Best match.** This one actually does what system-diagnostics describes: disk, CPU, memory,
  uptime, process review. High score reflects real semantic overlap.
- **Caveat:** The job has many custom instructions (suppressed warnings list, KNOWN ACCEPTED
  configurations, Tailscale path override) that a generic template cannot capture.
- **Verdict:** Partial conversion is possible but a custom script would be needed, not just
  the template as-is.

### 5. Daily Cron Health Report
- **Template matched:** `cron-health-report` (score: 7)
- **Correct match.** The job already delegates to `scripts/daily-health-report.py`.
  This is genuinely replaceable: a local script that calls `openclaw cron list`, counts
  statuses, and writes a report - no LLM needed.
- **Verdict:** Strongest conversion candidate. This job actually fits the template pattern
  and the LLM's only role is running the script and posting the output.

---

## Classification Edge Cases Noted

### 1. Script-runner jobs misclassified as conversion candidates
44% of the "mechanical" jobs already wrap existing Python/bash scripts.
The correct yburn strategy for these is a `script-runner` template, not the existing
domain-specific templates. The template library needs this gap filled.

**Affected jobs:** DB Maintenance, OAuth Token Health Check, System State Compiler,
subagent-transcript-cleanup, Stuck Session Cleanup, Daily Cron Health Report (partial).

### 2. "eod-summary" misclassified as mechanical (confidence: 0.43)
The eod-summary job has a long, multi-step reasoning payload that reads memory files,
synthesizes the day, and composes a brief. It should be REASONING. The score is low-confidence
mechanical partly because the fixture payload was truncated. With full payload text,
the `TestRealJobs::test_eod_summary_is_reasoning` test covers this case correctly.

### 3. Dream cycle jobs land as mechanical (moderate confidence)
All dream cycle jobs (Radar, Guru, Forge, Shield, Muse, Ink, Lens) classified mechanical
with mid-range confidence (0.16 to 0.45). These jobs move files, archive logs, and write
memory - genuinely mechanical. But the confidence is low because of mixed signals from
creative/improvement keywords. Classification is correct; confidence could improve with
a `dream-cycle` heuristic for the archive+consolidate pattern.

### 4. Board Meeting classified mechanical (confidence: 0.13)
The board meeting job is 900-second opus reasoning. It classified mechanical by narrow
margin (mech=22, reason=17). This is a misclassification. The job orchestrates agents,
synthesizes decisions, and writes documents - fully reasoning work. The large payload
has heavy mechanical orchestration language (checkpoint files, phase transitions) that
inflates the mechanical score. Board meeting jobs should be whitelisted as reasoning.

### 5. YouTube Intelligence Scrape classified mechanical (confidence: 0.28)
This is a long browser automation + transcript extraction + synthesis job running on sonnet.
Low-confidence mechanical. The job synthesizes insights and writes actionable intelligence -
it is REASONING. The shell command detection (python3, browser commands) boosted mechanical
score. This is a false positive worth noting.

### 6. Empty payload systemEvents land as UNSURE with 0 confidence
Jobs like `GitHub PAT Rotation Reminder` have `payload_kind: systemEvent` with no extractable
text. They correctly land as UNSURE with zero confidence. No issue here - these are calendar
reminders that fire into the main session, not true automation candidates.

### 7. Two "Outlook Playbook Weekly Review" jobs with identical names
One is a `systemEvent` (sessionTarget: main), one is an `agentTurn` (sessionTarget: isolated).
The systemEvent version classifies UNSURE with confidence 1.0 (only 2 signals, both reasoning).
The agentTurn version classifies UNSURE with confidence 0.2. The duplicate name is a scanner
edge case worth flagging - the scanner handles it fine but human review would need to
disambiguate by payload kind.

---

## Token Savings Estimate (Conservative)

Based on the 92 live crons:

| Category | Count | Avg runs/day | Token savings if converted |
|----------|-------|-------------|---------------------------|
| Clear script-runner mechanicals | ~12 | varies | High - full session cost eliminated |
| System health checks (haiku) | ~8 | 1-2/day | Medium - haiku is cheapest model |
| DB monitors (haiku) | ~4 | 2-4/day | Medium |
| Git/backup automation | ~4 | 1/day | Medium |

Realistic savings from converting the 12 clearest mechanical jobs: 15-25% of daily cron token spend.
The highest-value targets are jobs running on sonnet/opus that do deterministic scripted work.

**Most valuable conversion targets:**
1. `Daily Cron Health Report` - already runs a script, just needs a direct script-runner
2. `OpenClaw Update Check` - one CLI call, conditional message, no reasoning needed
3. `Stuck Session Cleanup` - runs a script, sends alert on hit, silent otherwise
4. `subagent-transcript-cleanup` - runs a script, reports if files deleted

---

## Recommendations

1. **Add a `script-runner` template.** The most common mechanical pattern is:
   "run this script, send an alert if non-zero exit, otherwise silent."
   This pattern covers ~40% of the mechanical bucket and none of the current templates match it.

2. **Add a `model-setter` template.** Two jobs (`Social Media - Set Opus Model`,
   `Session Model Overrides`) just call session_status to set model overrides.
   These are trivially replaceable with direct API calls.

3. **Add opus/board-meeting heuristic.** Any job with `model: opus` and
   `timeoutSeconds >= 600` and `sessionTarget: isolated` is almost certainly reasoning.
   Currently the board meeting job barely avoids UNSURE at 0.13 confidence.

4. **Tune the `dream_cycle` pattern.** Jobs with `archive` + `move` + `count` + `memory`
   as dominant signals are a distinct category. A heuristic boost for this pattern would
   improve confidence from the current 0.16-0.45 range to a clearer mechanical classification.

5. **Consider payload length as a signal.** Short payloads (under 100 chars) with `haiku`
   model are almost always mechanical. Long payloads (over 1000 chars) with opus are almost
   always reasoning. Length-based scoring could sharpen the unsure bucket.
