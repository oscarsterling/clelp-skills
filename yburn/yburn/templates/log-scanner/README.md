# log-scanner

Scans configured log files with configurable regular expressions and reports match counts per file.

## Config

- `log_paths`: files to scan
- `error_patterns`: regex patterns to count
- `alert_threshold`: return a warning when total matches meet or exceed this threshold

## Usage

The script reads each file locally, counts regex matches, and sends the report to Telegram when configured or prints to stdout otherwise. Exit codes: `0` no alert, `1` threshold reached or missing config, `2` execution error.
