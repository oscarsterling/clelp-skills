# oauth-health-check

Checks OAuth token sources stored in files or environment variables and reports whether they are present, expired, or nearing expiry.

## Config

- `token_file_paths`: JSON token files or raw token files to inspect
- `token_env_vars`: environment variable names holding token values
- `warn_if_expiring_within_days`: warning threshold

## Usage

The script understands common expiry keys in JSON token files and JWT `exp` claims. It sends the report to Telegram when configured with `YBURN_TELEGRAM_TOKEN` and `YBURN_TELEGRAM_CHAT_ID`; otherwise it prints to stdout. Exit codes: `0` healthy, `1` warning/critical findings, `2` execution error.
