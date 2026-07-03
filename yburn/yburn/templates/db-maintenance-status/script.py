#!/usr/bin/env python3
"""Database maintenance status template."""

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

CONFIG = {
    "telegram_token": os.environ.get("YBURN_TELEGRAM_TOKEN", ""),
    "telegram_chat_id": os.environ.get("YBURN_TELEGRAM_CHAT_ID", ""),
    "db_type": "sqlite",
    "sqlite_path": "",
    "postgres_dsn": "",
    "sql_query": "",
    "psql_binary": "psql",
}


def send_output(message):
    """Send output via Telegram if configured, else print to stdout."""
    token = CONFIG["telegram_token"]
    chat_id = CONFIG["telegram_chat_id"]

    if token and chat_id:
        import urllib.request

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = json.dumps(
            {"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}
        ).encode("utf-8")
        request = urllib.request.Request(
            url, data=payload, headers={"Content-Type": "application/json"}
        )
        try:
            urllib.request.urlopen(request, timeout=30)
            return
        except Exception as exc:
            print(f"Telegram send failed: {exc}", file=sys.stderr)

    print(message)


def run_sqlite_check():
    """Run sqlite query or existence check."""
    sqlite_path = str(CONFIG["sqlite_path"]).strip()
    if not sqlite_path:
        raise ValueError("sqlite_path is required when db_type=sqlite")
    db_path = Path(sqlite_path).expanduser()
    if not db_path.exists():
        return 1, f"*DB Maintenance Status*\nSQLite DB missing: `{db_path}`"

    if not CONFIG["sql_query"].strip():
        return 0, f"*DB Maintenance Status*\nSQLite DB exists: `{db_path}`"

    conn = sqlite3.connect(str(db_path))
    try:
        cursor = conn.execute(CONFIG["sql_query"])
        rows = cursor.fetchall()
    finally:
        conn.close()

    lines = [
        "*DB Maintenance Status*",
        "",
        "*Backend:* sqlite",
        f"*DB:* `{db_path}`",
        f"*Rows Returned:* {len(rows)}",
    ]

    if rows:
        preview = ", ".join(str(value) for value in rows[0])
        lines.append(f"*First Row:* `{preview[:200]}`")

    return 0, "\n".join(lines)


def run_postgres_check():
    """Run postgres query or connection check via psql."""
    dsn = CONFIG["postgres_dsn"].strip()
    if not dsn:
        raise ValueError("postgres_dsn is required when db_type=postgres")

    query = CONFIG["sql_query"].strip() or "SELECT 1;"
    result = subprocess.run(
        [CONFIG["psql_binary"], dsn, "-At", "-c", query],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        return 1, (
            "*DB Maintenance Status*\n"
            f"Postgres check failed for `{dsn}`\n"
            f"`{result.stderr.strip()[:300]}`"
        )

    output = result.stdout.strip()
    lines = [
        "*DB Maintenance Status*",
        "",
        "*Backend:* postgres",
        f"*Query:* `{query}`",
    ]
    if output:
        lines.append(f"*Result:* `{output[:300]}`")
    else:
        lines.append("*Result:* query succeeded with no output")
    return 0, "\n".join(lines)


def main():
    """Run the DB maintenance status check."""
    try:
        db_type = CONFIG["db_type"].strip().lower()
        if db_type == "sqlite":
            code, message = run_sqlite_check()
        elif db_type == "postgres":
            code, message = run_postgres_check()
        else:
            raise ValueError(f"Unsupported db_type: {CONFIG['db_type']}")

        send_output(message)
        return code
    except Exception as exc:
        send_output(f"*DB Maintenance Error*\n`{exc}`")
        return 2


if __name__ == "__main__":
    sys.exit(main())
