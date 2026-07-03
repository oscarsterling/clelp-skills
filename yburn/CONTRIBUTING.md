# Contributing to yburn

Thanks for wanting to help. This doc covers how to contribute without stepping on anything.

## Before You Start

- Open an issue first for anything non-trivial. Saves everyone time.
- Check open issues and PRs - someone may already be on it.
- For security issues, **do not open a public issue** - see the [Security](#security) section below.

## What We Want

- Bug fixes (with a test that proves it)
- New templates (see below)
- Platform support improvements (Windows native, systemd, launchd)
- Documentation fixes
- Performance improvements with benchmarks

## What We Don't Want (Yet)

- LLM integration features - the whole point is zero tokens
- Breaking changes to the CLI interface without prior discussion
- New external dependencies - stdlib only for the core engine

## Setup

```bash
git clone https://github.com/oscarsterling/yburn.git
cd yburn
pip install -e ".[dev]"
python3 -m pytest tests/ -q
```

All tests should pass before you touch anything.

## Making a Change

1. Fork the repo
2. Create a branch: `git checkout -b fix/your-fix-name`
3. Make your change
4. Add or update tests - **no test, no merge**
5. Run the full suite: `python3 -m pytest tests/ -q`
6. Run the linter: `ruff check .`
7. Commit with a clear message: `fix: what you fixed` or `feat: what you added`
8. Open a PR against `main`

## Adding a Template

Templates live in `yburn/templates/<name>/`. Each needs:

- `script.py` - the generated script (stdlib only, no external deps)
- `template.yaml` - metadata (name, description, keywords, params)

The script must:
- Accept `--config` pointing to a YAML config file
- Support `--output json` for machine-readable output
- Exit 0 on success, 1 on warning, 2 on critical
- Work on macOS and Linux with Python 3.9+
- Send Telegram/Discord/Slack alerts if configured (optional)

Look at `yburn/templates/system-diagnostics/` as the reference implementation.

Open an issue describing your template before building it so we can confirm it fits.

## Code Style

- Python, PEP 8 baseline
- Ruff for linting (`ruff check .`)
- Type hints where they add clarity
- Docstrings on public functions
- No f-strings with complex expressions - keep them readable

## Security

**Do not open a public GitHub issue for security vulnerabilities.**

Email **oscar.exec.asst@gmail.com** with:

- Description of the vulnerability
- Steps to reproduce
- Affected version(s)
- Potential impact

### What constitutes a security issue

- Path traversal or arbitrary file write/read outside expected directories
- Command injection through user-supplied config values or template parameters
- Credential or token exposure in logs, state files, or generated scripts
- Privilege escalation through subprocess calls
- Dependency vulnerabilities with a known exploit

### What to expect

- Acknowledgment within **48 hours**
- Status update within 7 days
- Fix or mitigation within 90 days (sooner for critical issues)
- Credit in the release notes if desired

### Out of scope

Issues requiring physical access to the machine, or issues in openclaw itself (report those upstream).

## PR Review

PRs are reviewed by the maintainer within a few days. We will tell you clearly if something needs changes or will not be merged, and why.

## License

By contributing, you agree your work is licensed under the [MIT License](LICENSE).
