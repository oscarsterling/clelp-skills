# yburn Security Audit - 2026-03-30

**Auditor:** Forge (Oscar Sterling Agency Lead Engineer)
**Version Audited:** v1.3.0
**Scope:** Full codebase review - dependencies, code security, input validation, templates, output channel security, PyPI hygiene

---

## Summary

| Severity | Count |
|----------|-------|
| CRITICAL | 0 |
| HIGH     | 3 |
| MEDIUM   | 4 |
| LOW/INFO | 5 |
| PASS     | 10 |

No critical issues found. Three high-severity issues should be addressed before launch. The codebase is generally well-structured with good security habits.

---

## CRITICAL

*None.*

---

## HIGH

### HIGH-1: Dependency Vulnerabilities (pip-audit findings)

**File:** `pyproject.toml` / `.venv`
**Finding:** `pip-audit` found 3 known CVEs in installed packages:

| Package   | Version | CVE           | Fix Version |
|-----------|---------|---------------|-------------|
| pip       | 25.3    | CVE-2026-1703 | 26.0        |
| pygments  | 2.19.2  | CVE-2026-4539 | (none listed) |
| requests  | 2.32.5  | CVE-2026-25645| 2.33.0      |

`requests` is a direct production dependency (in `pyproject.toml`). The CVE affects HTTP redirect handling. Since yburn uses `requests` only as a dependency (the templates use stdlib `urllib.request` directly), the actual attack surface is limited - but the package is shipped to PyPI users.

**Recommendation:** Pin `requests >= 2.33.0` in `pyproject.toml`. The current spec is unbounded (`"requests"` with no version constraint). Update to `"requests>=2.33.0"` to ensure clean installs.

```toml
dependencies = [
    "requests>=2.33.0",
    "pyyaml",
]
```

---

### HIGH-2: Telegram Bot Token Exposed in Base URL (Potential Log Leakage)

**File:** `yburn/channels/telegram.py`, line 35
**Finding:** The bot token is embedded directly in `self.base_url`:

```python
self.base_url = f"https://api.telegram.org/bot{token}"
```

If any exception handling or third-party library logs the full request URL (which includes `base_url`), the token leaks into logs. Python's `urllib.request` can include the URL in certain `HTTPError` tracebacks and exception messages. Additionally, `test_connection()` passes the full URL directly to `urlopen()`, and if DEBUG-level logging is enabled, some middleware could log it.

**The template scripts** (api-endpoint-check, cron-health-report, etc.) have the same pattern inline - the token is used directly in the URL string passed to `urlopen`.

**Recommendation:** Use a helper that accepts token and chat_id separately and never constructs a loggable URL with the token embedded. Alternatively, log only sanitized URL fragments:

```python
# Instead of logging the url, log only the endpoint
logger.debug("Sending to Telegram sendMessage endpoint")
```

For the `test_connection` method, catch and re-raise without including the URL in the exception message.

---

### HIGH-3: `config.py` Allows Telegram Token in YAML Config File

**File:** `yburn/config.py`, lines 26, 40
**Finding:** `Config.load()` reads `telegram_token` directly from the YAML config file:

```python
telegram_token=file_config.get("telegram_token", ""),
```

This means users can (and will) put their bot token in `yburn.yaml` or `~/.yburn/config.yaml` - plain text on disk. The docs/README should explicitly warn against this. If a user commits `yburn.yaml` to a repo (and `.gitignore` does not include `yburn.yaml`), the token is publicly exposed.

**Current `.gitignore` state:** `yburn.yaml` is NOT in `.gitignore`.

**Recommendation:**
1. Add `yburn.yaml` to `.gitignore`.
2. Add a prominent warning in the README and config template that `telegram_token` in the YAML file is a convenience-only option and that env vars (`YBURN_TELEGRAM_TOKEN`) are the recommended approach for any token that matters.
3. Consider refusing to load tokens from YAML if the config file is world-readable (`stat.st_mode & 0o077 != 0`).

---

## MEDIUM

### MEDIUM-1: `_apply_config_overrides` Uses Regex Substitution on Script Source Code

**File:** `yburn/converter.py`, lines 291-311
**Finding:** The `_apply_config_overrides` function uses `re.sub()` to patch CONFIG values directly into generated Python script source code. The `key` parameter is used raw in a regex pattern:

```python
pattern = rf'("{key}":\s*)(.*?)([,\n}}])'
```

If `key` contains regex special characters (e.g., `+`, `.`, `*`, `(`, `)`), the pattern can fail silently or produce unexpected replacements. The `replacement_val` (via `json.dumps`) is safe, but the key itself is not escaped.

**Recommendation:** Wrap the key in `re.escape()`:

```python
pattern = rf'("{re.escape(key)}":\s*)(.*?)([,\n}}])'
```

---

### MEDIUM-2: `db-maintenance-status` Template - Postgres DSN and SQL Query Passed to `psql` Subprocess

**File:** `yburn/templates/db-maintenance-status/script.py`, lines 82-88
**Finding:** The postgres DSN and SQL query come from `CONFIG` and are passed as arguments to `psql`. While they are passed as a list (not `shell=True`, which is good), the values themselves are user-controlled at template config time. A malicious or misconfigured `CONFIG["sql_query"]` could execute arbitrary SQL.

This is expected behavior for a database query tool, but it is worth documenting clearly. More importantly, `CONFIG["psql_binary"]` is user-controlled - a user could set this to any executable. There is no validation that `psql_binary` is actually psql or even a valid binary name.

**Recommendation:** Validate `psql_binary` against an allowlist (e.g., `["psql", "/usr/bin/psql", "/usr/local/bin/psql"]`) before executing. Add a comment noting that `sql_query` executes arbitrary SQL and should be reviewed before deploying.

---

### MEDIUM-3: `ssl-cert-expiry` Template - Domain Value Passed to `openssl s_client`

**File:** `yburn/templates/ssl-cert-expiry/script.py`, lines 45-46
**Finding:** The domain is passed to `openssl s_client -servername <domain> -connect <domain>:443`. While it's passed as a list (no shell injection), a specially crafted domain value like `evil.com:443 -proxy attacker.com:8080` could confuse the openssl argument parser since openssl uses positional and flag-style arguments. The `-connect` argument value is `f"{domain}:443"` with no validation.

**Recommendation:** Validate the domain value against a simple hostname regex before use:

```python
import re
if not re.match(r'^[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)*$', domain):
    raise ValueError(f"Invalid domain: {domain}")
```

---

### MEDIUM-4: `__pycache__` Committed in Template Directory

**File:** `yburn/templates/session-cleanup/__pycache__/script.cpython-314.pyc`
**Finding:** A compiled Python bytecode file is committed to the repository inside a template directory. This is not a direct security vulnerability but it:
- Leaks Python version information to anyone who clones the repo
- Can cause confusion if the source and bytecode get out of sync
- Should not be in a published package (users get `.pyc` files for a different Python version than they run)

**Recommendation:** Add `**/__pycache__/` and `**/*.pyc` to `.gitignore` if not already present, and remove the committed bytecode file:

```bash
git rm -r yburn/templates/session-cleanup/__pycache__/
echo "**/__pycache__/" >> .gitignore
echo "**/*.pyc" >> .gitignore
```

---

## LOW/INFO

### LOW-1: No `SECURITY.md` File

**Finding:** The repository has no `SECURITY.md` or security contact in `pyproject.toml`. For a published PyPI package, having a disclosure path is a best practice and is increasingly expected.

**Recommendation:** Add a `SECURITY.md` with responsible disclosure instructions, and optionally add a security contact to `pyproject.toml`:

```toml
[project.urls]
"Security Policy" = "https://github.com/oscarsterling/yburn/security/policy"
```

---

### LOW-2: `requests` and `pyyaml` Have No Version Pins

**File:** `pyproject.toml`
**Finding:** Both production dependencies have no version constraints:

```toml
dependencies = [
    "requests",
    "pyyaml",
]
```

This means any future breaking or vulnerable release could be auto-installed. While minimal version constraints are common for libraries, specifying a lower bound at minimum prevents installation of ancient vulnerable versions.

**Recommendation:** Add minimum version bounds:

```toml
dependencies = [
    "requests>=2.33.0",
    "pyyaml>=6.0",
]
```

---

### LOW-3: `cmd_test` Runs Arbitrary Scripts from State File

**File:** `yburn/cli.py`, lines 490-521
**Finding:** `cmd_test` resolves a script path from the replacements state file (`~/.yburn/state/replacements.json`) and executes it with `sys.executable`. If the state file is tampered with (e.g., by another process with user-level write access), this could execute an arbitrary file. This is a local-privilege-escalation risk only - it requires the attacker to already have write access to `~/.yburn/state/`.

The risk is low for a single-user tool but worth noting.

**Recommendation:** Validate that `script_path` from the state file starts with `~/.yburn/scripts/` (or the configured `YBURN_SCRIPTS_DIR`) before executing.

---

### LOW-4: `--file` JSON Input Not Validated Against Schema

**File:** `yburn/cli.py`, line 70
**Finding:** When using `yburn audit -f crons.json`, the JSON file is loaded and passed to `scan_from_json()` with minimal validation. Individual field values from the JSON (like `payload_text`, `name`) flow into classification and eventually into file paths and script headers.

The `name` field goes through `re.sub(r'[^a-z0-9_-]', '-', ...)` before use in file paths (good), but `payload_text` is inserted into the generated script header comment without sanitization. A comment injection (e.g., payload containing `\n#`) could mangle the script header but nothing more dangerous.

**Recommendation:** Low priority. Truncate `payload_text` in headers and consider validating expected field types.

---

### LOW-5: Missing `License` Classifier Consistency

**File:** `pyproject.toml`
**Finding:** The `license` field uses the string `"MIT"` but the SPDX-compatible classifier would be `"License :: OSI Approved :: MIT License"`. This is not a security issue but affects PyPI display and tooling compatibility.

**Recommendation:** Add the classifier:

```toml
"License :: OSI Approved :: MIT License",
```

---

## PASS Items

1. **No hardcoded credentials found.** All tokens, webhook URLs, and chat IDs are read from environment variables or config files. No static secrets in source code.

2. **No `shell=True` in any subprocess call.** All subprocess invocations use list-style arguments, preventing shell injection. Checked across all templates and core modules.

3. **No `eval()` or `exec()` calls in production code.** The grep for `eval`/`exec` found only function names (`evaluate_results`) and no dynamic code execution.

4. **`yaml.safe_load()` used everywhere.** Both `config.py` and `yburn_health.py` use `yaml.safe_load()` instead of the unsafe `yaml.load()`, preventing YAML deserialization attacks.

5. **Tokens never logged at INFO or higher.** The `telegram.py` logger calls do not log the token or `base_url` directly. The token is not included in error messages (though see HIGH-2 for the latent URL-in-traceback risk).

6. **Discord and Slack webhooks accepted only as env vars** (`YBURN_DISCORD_WEBHOOK`, `YBURN_SLACK_WEBHOOK`). Neither is written to state files.

7. **File paths sanitized before use in filesystem operations.** `re.sub(r'[^a-z0-9_-]', '-', name.lower())` is consistently applied to job names before constructing file paths.

8. **State files stored in user home directory only** (`~/.yburn/`). No writes to system directories. Permissions rely on OS-level user isolation.

9. **`check_output_config()` warns but does not fail** when channels are unconfigured. Scripts degrade gracefully to stdout rather than crashing or exposing partial config.

10. **Template scripts are self-contained and do not import `yburn`.** Generated scripts work standalone with stdlib only (except `requests`/`pyyaml` in the core CLI). This limits the attack surface of deployed scripts.

---

## Dependency Vulnerability Details

**Command run:** `pip-audit` in `.venv` (Python 3.14)

```
Name     Version  ID              Fix Versions
-------- -------  --------------  ------------
pip      25.3     CVE-2026-1703   26.0
pygments 2.19.2   CVE-2026-4539   (none listed)
requests 2.32.5   CVE-2026-25645  2.33.0
```

Notes:
- `pip` vulnerability: affects the dev toolchain only, not production runtime. Low operational risk but update anyway.
- `pygments`: dev dependency (used by Rich for syntax highlighting in terminal output). Not a direct production risk but monitor for a fix version.
- `requests`: only `requests` is a declared production dependency in `pyproject.toml`. **Update to `>=2.33.0`** before next PyPI publish.

---

## Recommended Action Priority

Before next PyPI release:
1. Pin `requests >= 2.33.0` in `pyproject.toml` (HIGH-1)
2. Add `yburn.yaml` to `.gitignore` + README warning about token storage (HIGH-3)
3. Fix regex key escaping in `_apply_config_overrides` (MEDIUM-1)
4. Add `SECURITY.md` (LOW-1)
5. Remove committed `__pycache__` from templates (MEDIUM-4)

Post-launch:
6. Refactor Telegram URL construction to avoid token-in-URL logging risk (HIGH-2)
7. Add domain validation to ssl-cert-expiry template (MEDIUM-3)
8. Add psql_binary allowlist to db-maintenance template (MEDIUM-2)
