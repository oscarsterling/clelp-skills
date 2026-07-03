# session-cleanup

Detects OpenClaw sessions that have been running longer than `max_session_age_hours` and either reports or kills them.

## Config

- `max_session_age_hours`: age threshold for stuck sessions
- `dry_run`: if `true`, report only
- `exclude_session_labels`: exact session labels to skip

## Usage

Run the generated script locally or via cron. It calls `openclaw sessions list --json`, filters sessions older than the configured threshold, and sends the report to Telegram when `YBURN_TELEGRAM_TOKEN` and `YBURN_TELEGRAM_CHAT_ID` are set; otherwise it prints to stdout.
