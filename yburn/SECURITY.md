# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 1.4.x   | ✅ |
| < 1.4   | ❌ |

## Reporting a Vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Please report via GitHub's private advisory system:
👉 https://github.com/oscarsterling/yburn/security/advisories/new

### What to include
- Description of the vulnerability
- Steps to reproduce
- Affected version(s)
- Potential impact

### What to expect
- Acknowledgment within **48 hours**
- Status update within **7 days**
- Fix or mitigation within **90 days** (sooner for critical issues)
- Credit in the release notes (if desired)

## Scope

yburn runs locally on your machine with your user permissions. It:
- Reads cron job state via `openclaw cron list`
- Writes scripts to `~/.yburn/scripts/`
- Writes state to `~/.yburn/state/`
- Calls `openclaw cron update` via subprocess for rollback operations

Out of scope: issues requiring physical access to the machine, or issues in openclaw itself (report those upstream).
