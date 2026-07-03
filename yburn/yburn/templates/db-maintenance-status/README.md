# db-maintenance-status

Checks sqlite or Postgres database health by running a configured query or falling back to a simple presence/connection check.

## Config

- `db_type`: `sqlite` or `postgres`
- `sqlite_path`: sqlite database file
- `postgres_dsn`: DSN/connection string passed to `psql`
- `sql_query`: optional status query
- `psql_binary`: override the `psql` executable if needed

## Usage

For sqlite, the script uses the stdlib `sqlite3` module. For Postgres, it shells out to `psql`. Exit codes: `0` healthy, `1` warning/problem state, `2` execution error. Output goes to Telegram when configured, otherwise stdout.
