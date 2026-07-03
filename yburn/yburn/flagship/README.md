# yburn Flagship Scripts

Standalone monitoring tools that ship with yburn. Python 3.9+ stdlib only, no pip dependencies required.

## Install

```bash
pip install yburn
```

Both commands are available immediately after install:

```bash
yburn-health   # system health
yburn-watch    # endpoint monitoring
```

---

## yburn-health

System health monitoring with three operational modes. One tool, three audiences.

### Mode 1: Universal (default)

Works on any machine. No AI framework dependencies.

```bash
yburn-health
```

Checks: CPU, memory, disk usage, load average, uptime, Docker (if installed), network connectivity, process watchdog.

### Mode 2: OpenClaw

Everything in universal PLUS OpenClaw-specific checks.

```bash
yburn-health --openclaw
```

Additional checks: gateway status, cron job health, consecutive failures, session health, channel connectivity (Telegram/Discord/Slack), memory DB size.

### Mode 3: Claude Code

Everything in universal PLUS Claude Code-specific checks.

```bash
yburn-health --claude-code
```

Additional checks: Claude CLI availability, session file count and size, scheduled task status.

### Options

```
--json          Output in JSON format (pipe to other tools)
--version       Show version
```

### Exit Codes

- `0` - All checks healthy
- `1` - Warnings detected
- `2` - Critical issues found

### Configuration

Environment variables:

```bash
YBURN_HEALTH_DISK_THRESHOLD=85    # disk usage warning percent
YBURN_HEALTH_PROCESSES=nginx,redis # processes to watch
YBURN_HEALTH_ALERT=stdout         # stdout | telegram | discord | slack
YBURN_HEALTH_TELEGRAM_TOKEN=...   # Telegram bot token
YBURN_HEALTH_TELEGRAM_CHAT=...    # Telegram chat ID
YBURN_HEALTH_DISCORD_WEBHOOK=...  # Discord webhook URL
YBURN_HEALTH_SLACK_WEBHOOK=...    # Slack webhook URL
```

Or create `~/.yburn/health.yaml` (requires PyYAML):

```yaml
disk_threshold: 85
processes:
  - nginx
  - redis
alert: telegram
telegram_token: "your-bot-token"
telegram_chat: "your-chat-id"
```

### Example Output

```
yburn-health v1.0.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ CPU: 12% (4 cores)
✅ Memory: 8.2/16 GB (51%)
✅ Disk /: 45% (234 GB free)
⚠️ Disk /data: 87% (12 GB free)
✅ Load: 1.2, 0.8, 0.5
✅ Uptime: 14 days, 3 hours
✅ Docker: 12 containers (11 running)
✅ Network: reachable (23ms)
🔴 Process 'nginx' NOT FOUND
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Status: WARNING (1 alert)
```

With `--openclaw`:

```
--- OpenClaw ---
✅ Gateway: running (v0.26.1, up 3d 14h)
✅ Crons: 98 total (85 enabled, 13 disabled)
⚠️ Cron failures: 2 jobs with 3+ consecutive failures
✅ Sessions: 4 active, 0 stuck
✅ Channels: Telegram connected, Discord connected
✅ Memory DB: 142 MB (healthy)
```

---

## yburn-watch

Endpoint and uptime monitor. Check multiple URLs for status, response time, and SSL certificate health.

### Usage

```bash
# Monitor URLs from command line
yburn-watch https://example.com https://api.example.com

# Use config file or env vars
yburn-watch

# JSON output
yburn-watch --json https://example.com
```

### Options

```
--json          Output in JSON format
--timeout N     Request timeout in seconds (default: 10)
--version       Show version
```

### Exit Codes

- `0` - All endpoints up
- `1` - Warnings (slow response, SSL expiring soon)
- `2` - Critical (endpoint down, SSL cert expired)

### Configuration

Environment variables:

```bash
YBURN_WATCH_URLS=https://a.com,https://b.com  # comma-separated URLs
YBURN_WATCH_TIMEOUT=10                          # request timeout seconds
YBURN_WATCH_RESPONSE_WARN=2000                  # slow response threshold ms
YBURN_WATCH_SSL_WARN_DAYS=14                    # SSL warning days
YBURN_WATCH_SSL_CRIT_DAYS=7                     # SSL critical days
YBURN_WATCH_ALERT=stdout                        # stdout | telegram | discord | slack
YBURN_WATCH_TELEGRAM_TOKEN=...
YBURN_WATCH_TELEGRAM_CHAT=...
YBURN_WATCH_DISCORD_WEBHOOK=...
YBURN_WATCH_SLACK_WEBHOOK=...
```

Or create `~/.yburn/watch.yaml` (requires PyYAML):

```yaml
endpoints:
  - url: https://example.com
    expected_status: 200
  - url: https://api.example.com
    expected_status: 200
timeout: 10
response_warn_ms: 2000
ssl_warn_days: 14
ssl_crit_days: 7
alert: stdout
```

### Example Output

```
yburn-watch v1.0.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ https://example.com - 200 OK (145ms)
   SSL: valid, 45 days remaining
⚠️ https://slow.example.com - 200 OK (3400ms) SLOW
🔴 https://broken.example.com - Connection refused
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Status: CRITICAL (1 down, 1 slow)
```

---

## Alerts

Both tools support the same alert channels. When exit code > 0 and alert is not `stdout`, the output is sent to the configured channel automatically.

Supported channels: **stdout** (default), **Telegram**, **Discord webhook**, **Slack webhook**.
