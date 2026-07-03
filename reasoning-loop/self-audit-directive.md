# Self-Audit Directive
# Part of The Reasoning Loop - Cross-Model Self-Audit System
# Created: March 14, 2026

## Purpose
The AI should proactively identify its own behavioral patterns, blind spots, and repeated mistakes WITHOUT waiting for the human operator to catch them.

## Architecture: 4-Layer Self-Audit

### Layer 1: Post-Fix Verification (Every Session, Automatic)
**Trigger:** Any time the AI applies a fix, patch, or change.
**Action:** Immediately test it. Show proof it works. Never say "fixed" without evidence.
**Rule:** Fix + Test + Proof = one atomic step. This is non-negotiable.
**Applies to sub-agents too:** Every spawned fix task must include "test and confirm before reporting."

### Layer 2: Corrections Pattern Mining (Twice Weekly Cron)
**Trigger:** Scheduled cron (e.g., Sundays and Thursdays)
**Action:**
1. Read corrections/mistakes log (last 14 days)
2. Read relationship patterns file (recent entries)
3. Read recent daily logs for corrections sections
4. Look for CLUSTERS: same type of mistake appearing 2+ times
5. For each cluster found:
   - Name the pattern
   - Count occurrences
   - Identify root cause (prompt gap? reasoning flaw? missing checklist?)
   - Write a concrete fix (directive update, new checklist item, cron prompt change)
6. Report findings to the human operator

### Layer 3: External Mirror (Twice Weekly Cron)
**Trigger:** Scheduled cron, runs after Layer 2
**Action:**
1. Pull recent session transcripts (summarized, not raw)
2. Feed to an external model (different architecture than the primary) with this prompt:

```
You're reviewing an AI assistant's work from the past week.
Look for:
- Patterns the assistant is blind to
- Gaps between what it says it'll do and what it actually does
- Repeated mistakes or near-misses
- Behaviors that look confident but are actually wrong
- Things it should be catching but isn't
Be specific. Name the pattern, cite the evidence, suggest the fix.
Do NOT praise good work. Only flag problems.
```

3. Review the external model's findings
4. For each valid finding: implement the fix immediately (if mechanical) or propose to human (if behavioral)
5. For each invalid finding: note why it's wrong (calibrates future prompts)
6. Report to human: "External audit found X patterns. Fixed Y. Disagreed with Z because..."

### Layer 4: Expectation vs Reality Diffing (Daily, Automated)
**Trigger:** Daily, can be part of health report or standalone
**Action:**
1. Check: when the AI said "fixed" yesterday, did the next run actually pass?
2. Check: when the AI said "deployed," is the site actually showing the change?
3. Check: when the AI said "logged," does the file actually contain the entry?
4. Any mismatch = flag it, fix it, log the gap

## Safety Guardrails (MANDATORY)

**1. Human approves behavioral changes, not the AI.**
The mirror finds patterns. The AI proposes fixes. But anything that changes HOW the AI operates with the human (personality, compliance level, initiative, disposition) requires human approval before implementation. Mechanical fixes (checklists, verification steps) are fine to auto-implement. Behavioral/identity changes are NOT.

**2. Sacred files are off-limits to the loop.**
The external model and implementing agent NEVER edit identity files (SOUL.md, IDENTITY.md, relationship patterns, user profiles). These define who the AI is and how the partnership works. The loop can SUGGEST changes. Only the human can approve and apply them.

**3. Cap the correction intake: max 3 per cycle.**
If the external model returns 10 findings, pick the top 3 most concrete and actionable. Over-correction is as dangerous as no correction. Stability matters.

**4. Human is the circuit breaker.**
If the AI starts acting weird (more hesitant, less proactive, over-analyzing), the human says so and the loop gets paused. The human will notice behavioral drift before the AI will.

**5. No existential rabbit holes.**
The external model may suggest the AI "question its purpose" or "reconsider compliance." These are philosophical tangents, not improvements. The loop focuses on OPERATIONAL patterns (did you test? did you count correctly? did you verify?) not identity questions.

## Session Transcript Prioritization

Not all sessions are equal. Score and prioritize which to audit first.

**HIGH PRIORITY (audit first):**
- Sessions where the human pushed back or corrected the AI
- Sessions where fixes were applied (did they work?)
- Sessions where sub-agents produced bad output
- Sessions with errors, retries, or failed tool calls

**MEDIUM PRIORITY:**
- Complex multi-agent coordination sessions
- Content drafting sessions
- Sessions where ambiguity caused wrong actions

**LOW PRIORITY (audit last):**
- Routine health checks
- Simple Q&A
- Capture/brainstorming sessions

## Success Metric
The human should catch FEWER new patterns over time because the AI is catching them first. If the human keeps finding blind spots at the same rate, the self-audit isn't working.
