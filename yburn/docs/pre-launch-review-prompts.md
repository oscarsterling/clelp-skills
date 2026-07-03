# Pre-Launch Review Prompts
# Created: 2026-03-30 | Use before any major version push or marketing run

These three prompts are designed to be model-bounced (ChatGPT, Gemini, Guru) simultaneously.
Synthesize responses into a gap list with a "ship as-is / fix first / not ready" verdict.

---

## Prompt 1 - Product Readiness Review
**Model: ChatGPT o3**

You are a senior developer tools product manager. Review yburn, a Python CLI tool that audits AI agent cron jobs and replaces mechanical ones with local scripts.

Context: yburn targets developers using OpenClaw, Claude Code, or similar AI agent platforms who have cron jobs running on a schedule. The tool scans their crons, classifies them as mechanical (convertible to local scripts) or reasoning (needs LLM), and generates standalone Python scripts to replace the mechanical ones.

Current feature set: `yburn audit` (scan + classify), `yburn convert` (generate scripts), `yburn replace` (disable original, provide crontab entry), `yburn rollback`, `yburn report` (before/after markdown report), `yburn audit --interactive` (manually classify UNSURE jobs), `yburn-health` (system health monitor, 3 modes), `yburn-watch` (endpoint/SSL monitor), 10 templates (system-diagnostics, cron-health-report, git-backup-status, api-endpoint-check, file-watcher, session-cleanup, oauth-health-check, db-maintenance-status, ssl-cert-expiry, log-scanner), Discord + Slack + Telegram output channels.

README excerpt (first-time user experience):
- `pip install yburn` then `yburn audit` shows mechanical/reasoning/unsure breakdown
- `yburn convert --all` generates scripts
- `yburn replace <job-id>` gives crontab entry + openclaw disable command

Answer these specific questions:
1. Is this ready to ship to a cold audience, or are there gaps that would cause a significant percentage of first-time users to abandon it?
2. What is the single biggest friction point in the first-run experience?
3. What one feature, if added before launch, would meaningfully increase adoption?
4. What's missing from the value prop that would make developers share this with their team?
5. Verdict: ship as-is, ship with one fix, or not ready?

---

## Prompt 2 - Cold User UX Walkthrough
**Model: Gemini**

You are a developer who runs an AI agent system with ~50 scheduled cron jobs. You've never heard of yburn. Someone linked you to it on Reddit saying "this saved me money on tokens."

Walk through the experience of a first-time user who:
- Installs with `pip install yburn`
- Runs `yburn audit` and gets back: 57 mechanical, 14 reasoning, 27 unsure
- Tries to convert a mechanical job and gets told output will go to stdout only (Telegram not configured)
- Runs `yburn replace` and gets a crontab entry to add manually
- Has 27 UNSURE jobs staring at them

For each step, answer:
- What is the user thinking/feeling?
- What question do they have that isn't answered?
- Where do they get stuck or give up?

Then give your top 3 UX improvements that would get more users through the full convert → replace flow without dropping off.

---

## Prompt 3 - Positioning and Marketing
**Model: Guru**

You are a senior product marketer reviewing yburn, a Python CLI tool that replaces token-burning AI agent crons with local scripts.

The core claim: "57 of 97 cron jobs (59%) don't need an LLM. Your AI agent is spending 30 seconds and burning tokens to check if your disk is full. A Python script does it in 200ms for free."

Target audience: developers running OpenClaw, Claude Code, or home-built AI agent systems with scheduled jobs.

Current positioning: "Why burn tokens on tasks that don't think?"

Answer:
1. Does the tagline land for the target audience, or is it too clever?
2. Is "token savings" the right lead, or is something else (speed, reliability, simplicity) more compelling?
3. Where would this tool get organic traction fastest - HN, Reddit r/selfhosted, r/LocalLLaMA, AI Twitter, or somewhere else?
4. What's the one-liner for the HN "Show HN" post?
5. What social proof or credibility signal is missing that would make developers trust this enough to run it against their live system?

---

## How to Use

1. Update the feature set in Prompt 1 to reflect the current version before running
2. Run all three simultaneously (sessions_spawn or paste into browser tabs)
3. Collect responses, synthesize into a gap list
4. Verdict categories: **ship as-is** | **ship with one fix** | **not ready**
5. Log the verdict + date in this file under Results

---

## Results Log

| Date | Version | Verdict | Key Finding |
|------|---------|---------|-------------|
| (pending) | v1.3.0 | - | First run scheduled |
