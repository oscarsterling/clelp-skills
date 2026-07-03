# yburn

**Why burn tokens on tasks that don't think?**
Your cron jobs should not depend on an API being up.

Audit your AI agent cron jobs, identify the ones that never needed an LLM, and replace them with local scripts that run in under a second and cost nothing. Plus standalone health monitoring tools that work on any machine.

![Python Version](https://img.shields.io/badge/python-3.9%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Tests](https://img.shields.io/badge/tests-277%20passing-brightgreen)

```bash
pip install yburn
```

<p align="center">
  <img src="docs/gifs/yburn-audit.gif" alt="yburn audit demo" width="700">
</p>

---

## 30-Second Quickstart

```bash
# See what you're burning tokens on
yburn audit

# Check your system health (no tokens, no LLM, instant)
yburn-health

# Monitor your endpoints
yburn-watch https://yoursite.com https://api.yoursite.com/health
```

That's it. Three commands. Zero configuration needed.

yburn generates standalone Python scripts that run directly on your machine - no LLM, no API calls, zero tokens. Telegram, Discord, and Slack are optional notification channels. By default, scripts print to stdout. Configure them later or never - the scripts work either way.

---

## The Problem

Real numbers from a live 98-cron OpenClaw deployment (March 2026):

- **57 of 98 crons (58%) were mechanical** - the LLM was doing nothing a shell script couldn't do faster
- **~30 seconds per LLM cron** drops to **under 1 second** with a local script
- Common culprits: system health checks, DB maintenance, git backups, cron audits, uptime monitoring

Your AI agent is spending 30 seconds and burning tokens to check if your disk is full. A Python script does it in 200ms for free.

The community has been discovering this one job at a time. Developers at Moltbook AI manually replaced OpenClaw crons with Python scripts. Others cut AI agent costs by 60% by auditing crons and switching models. Yburn automates what they all did by hand.

---

## What's In The Box

### The Audit Engine (scan, classify, convert, replace, rollback)

Five verbs, one tool, zero tokens after conversion:

```
$ yburn audit

Scanning cron jobs...
Found 97 jobs. Classifying...

  Mechanical:  51 jobs (convertible)
  Reasoning:   16 jobs (kept as-is)
  Unsure:      30 jobs (need your input)

--- MECHANICAL (convertible) ---
  DB Maintenance - Daily Full [enabled]
    Score: mech=16 reason=0 conf=1.00
    Template -> system-diagnostics

  Nightly Backup + Git Commit [enabled]
    Score: mech=17 reason=2 conf=0.79
    Template -> git-backup-status

  Daily Cron Health Report [enabled]
    Score: mech=11 reason=1 conf=0.83
    Template -> cron-health-report
  ...
```

Classification is deterministic keyword scoring. No LLM call needed. The tool that finds your LLM waste does not itself create LLM waste.

### yburn-health: System Health Monitor

Three modes for three audiences. One install.

<p align="center">
  <img src="docs/gifs/yburn-health.gif" alt="yburn-health demo" width="700">
</p>

**Universal (any machine):**
```
$ yburn-health

yburn-health v1.0.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ CPU: 12% (10 cores)
✅ Memory: 8.2/16 GB (51%)
✅ Disk /: 45% (234 GB free)
⚠️ Disk /data: 87% (12 GB free)
✅ Load: 1.2, 0.8, 0.5
✅ Uptime: 14 days, 3 hours
✅ Docker: 12 containers (11 running)
✅ Network: reachable (23ms)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Status: WARNING (1 alert)
```

**OpenClaw users** get gateway, cron, session, and channel health:
```
$ yburn-health --openclaw

... (all universal checks) ...

--- OpenClaw ---
✅ Gateway: running (v0.26.1, up 3d 14h)
✅ Crons: 97 total (92 enabled, 5 disabled)
⚠️ Cron failures: 2 jobs with 3+ consecutive failures
✅ Sessions: 4 active, 0 stuck
✅ Channels: Telegram connected, Discord connected
✅ Memory DB: 142 MB (healthy)
```

**Claude Code users** get scheduled task and session checks:
```
$ yburn-health --claude-code

... (all universal checks) ...

--- Claude Code ---
✅ CLI: claude available (v1.2.3)
✅ Sessions: 3 recent
✅ Scheduled tasks: 5 active, 0 expired
```

### yburn-watch: Endpoint and Uptime Monitor

<p align="center">
  <img src="docs/gifs/yburn-watch.gif" alt="yburn-watch demo" width="700">
</p>

```
$ yburn-watch https://clelp.ai https://api.example.com/health

yburn-watch v1.0.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ https://clelp.ai - 200 OK (285ms)
   SSL: valid, 35 days remaining
✅ https://api.example.com/health - 200 OK (89ms)
   SSL: valid, 45 days remaining
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Status: ALL UP (2 endpoints)
```

Checks HTTP status, response time, and SSL certificate expiry. Warns at 14 days, critical at 7.

---

## Installation

```bash
pip install yburn
```

Requires Python 3.9+. Works on macOS and Linux (including WSL).

The flagship tools (`yburn-health`, `yburn-watch`) use stdlib only. Zero dependencies. The audit engine uses `requests` and `pyyaml` (installed automatically).

---

## How The Audit Engine Works

```
scan -> classify -> convert -> replace -> (rollback if needed)
```

1. **Scan:** Reads your cron jobs via `openclaw cron list` (or any JSON file with `--file`).

2. **Classify:** Scores each job against weighted keyword sets. Mechanical signals: shell commands, file ops, status checks. Reasoning signals: analysis, drafting, strategy, synthesis. No LLM call needed.

3. **Convert:** Matches mechanical jobs to built-in templates. Generates standalone Python scripts that replicate the job without an LLM. Preview before confirming.

4. **Replace:** Disables the original LLM cron, activates the script-based equivalent on the same schedule. Tracks everything for rollback.

5. **Rollback:** One command restores the original. `yburn rollback <job-id>`. Originals are never deleted.

---

## Full Command Reference

### Audit

```bash
yburn audit                    # Scan and classify all cron jobs
yburn audit --verbose          # Show reasoning jobs too
yburn audit --threshold 2      # Lower threshold (more jobs classified)
yburn audit --file jobs.json   # Audit from a JSON file
```

### Convert

```bash
yburn convert <job-id>         # Convert one job to a local script
yburn convert --all            # Convert all mechanical jobs
yburn convert --all --dry-run  # Preview without writing files
```

### Replace

```bash
yburn replace <job-id>         # Swap original cron for script version
yburn replace <job-id> --yes   # Skip confirmation
yburn replace <job-id> --dry-run
```

### List, Test, Rollback

```bash
yburn list                     # Show all active replacements
yburn test <job-id>            # Run converted script once, show output
yburn rollback <job-id>        # Restore original cron
```

### Health and Watch

```bash
yburn-health                   # System health (universal)
yburn-health --openclaw        # + OpenClaw checks
yburn-health --claude-code     # + Claude Code checks
yburn-health --json            # JSON output for piping
yburn-health --processes nginx,postgres  # Watch specific processes

yburn-watch https://example.com          # Check one URL
yburn-watch url1 url2 url3               # Check multiple
yburn-watch --json                       # JSON output
yburn-watch --timeout 5                  # Custom timeout (seconds)
yburn-watch --warn-ms 2000              # Response time warning threshold
```

---

## Alert Channels

Both `yburn-health` and `yburn-watch` support sending alerts via:

- **stdout** (default)
- **Telegram** (`YBURN_TELEGRAM_TOKEN` + `YBURN_TELEGRAM_CHAT_ID`)
- **Discord** (`YBURN_DISCORD_WEBHOOK`)
- **Slack** (`YBURN_SLACK_WEBHOOK`)

Set via environment variables or `~/.yburn/health.yaml` / `~/.yburn/watch.yaml`.

---

## Exit Codes

All tools use consistent exit codes for scripting and CI:

| Code | Meaning |
|------|---------|
| 0    | Healthy / all up |
| 1    | Warnings present |
| 2    | Critical issues |

```bash
yburn-health || echo "Something needs attention"
```

---

## Template Library

Five built-in templates for the most common mechanical cron patterns:

| Template | Replaces |
|----------|----------|
| `system-diagnostics` | Disk, CPU, memory, uptime, process checks |
| `cron-health-report` | Cron status audits, failure counts |
| `git-backup-status` | Git add/commit/push, repo state checks |
| `api-endpoint-check` | HTTP health checks, OAuth validation |
| `file-watcher` | File change detection, size monitoring |

Custom templates go in `~/.yburn/templates/`. See `yburn/templates/TEMPLATE_SPEC.md`.

---

## What Converts Well (and What Doesnt)

**Converts cleanly:**

- System health checks (disk, CPU, memory, uptime)
- DB maintenance and size monitoring
- Git backup status and commit checks
- Endpoint and SSL cert monitoring
- OAuth token health checks
- Log file scanning
- File change detection
- Cron health reports

**Struggles with:**

- Jobs that call external APIs with variable or stateful output
- Jobs that need OpenClaw session context to function
- Jobs with reasoning, decision-making, or natural language in the payload
- Jobs that write back to OpenClaw memory or state files
- Anything where "if the output looks wrong, think about it" is the job

If your job is doing any of the second list, keep it as an LLM cron. That is what LLMs are for.

---

## Supported Platforms

| Platform | Status |
|----------|--------|
| macOS    | Full support |
| Linux    | Full support |
| WSL      | Full support (Linux mode) |
| Windows  | Planned for v2 |

**Agent frameworks:**

| Framework | Status |
|-----------|--------|
| OpenClaw  | Native integration (`openclaw cron list`) |
| Claude Code | Health checks via `--claude-code` flag |
| Any JSON  | `yburn audit --file jobs.json` |

---

## Tips: Running Converted Scripts via Cron

When yburn replaces an LLM cron with a script-based one, a small model (like Haiku) still runs the script. Here's what we learned running this on our own 97-cron setup:

**Use this prompt pattern for the cron payload:**
```
Run this command exactly and produce the output as your response.
Do not send any messages yourself, delivery is handled automatically.

python3 /path/to/your/yburn/script.py
```

**Why this matters:** Lower-reasoning models (Haiku, GPT-4o-mini) will try to "help" by interpreting the script output, sending messages on their own, or hallucinating results. This prompt keeps them focused: run the command, return the output, stop. Let your cron system's delivery mechanism (OpenClaw's `announce`, webhooks, etc.) handle routing.

**Other tips:**
- Set `delivery.mode: "announce"` on OpenClaw crons so output routes automatically. Don't ask the model to send messages.
- Use `timeoutSeconds: 60` for script-based crons. The scripts run in under a second, but the model needs time to process output.
- Test with `yburn test <job-id>` before replacing. Always.
- Keep `yburn rollback <job-id>` in your back pocket. Originals are never deleted.

---

## Test Suite

```
$ pytest
252 passed in 1.68s
```

---

## What's Next

- More templates (OAuth checker, DB maintenance, log scanner)
- Interactive audit flow with guided prompts
- Auto-detection of cron system (OpenClaw, crontab, systemd, Claude Code)
- Token savings tracking over time
- Community template contributions

---

## Contributing

Contributions welcome, especially:

- New templates for common mechanical cron patterns
- Scanner adapters for other agent frameworks
- Classification signal improvements

```bash
git clone https://github.com/oscarsterling/yburn.git
cd yburn
pip install -e '.[dev]'
pytest
```

Please open an issue before submitting a large PR.

---

## License

MIT. See `LICENSE`.

---

*Built because 58% of our cron jobs were burning tokens on work that doesn't think.*
