#!/usr/bin/env python3
"""session-checkpoint.example.py - keep a durable, rolling snapshot of session
state so a context reset never loses the thread.

THE PROBLEM
  A long agent session eventually compacts or resets its context. Whatever was
  only in the conversation is gone. If the important state also lives in a small
  set of files, a rolling snapshot of those files is a recovery point the
  restored session can read to pick up exactly where it was.

WHAT IT DOES (two independent, cheap jobs per run)
  A. SAVE. If the checkpoint file is older than a cooldown, rewrite it: a single
     rolling snapshot (overwrite, not append) assembled from a configured list
     of source files (each line-capped), an optional recent git-log tail, and an
     optional listing of "state" globs. Written atomically so a reset mid-write
     never leaves a corrupt checkpoint.
  B. BREAKPOINT SIGNAL (optional, busy-guarded). If you configure a breakpoint
     probe (a command that exits 0 when a clean reset is DUE, for example when
     context usage crosses a threshold), the hook fires your notify command ONCE
     per session, but only when a busy probe says work is idle. This is the
     busy guard: it will not nag you to reset in the middle of live work. If you
     configure neither probe, job B is a no-op and this is purely a checkpoint
     writer.

  The SAVE half never needs a network or an LLM. It is local file IO only.

CONTRACT (a prompt-submit or stop style hook)
  stdin : JSON; `session_id` or `transcript_path` is used to scope the
          once-per-session breakpoint signal. Missing fields degrade gracefully.
  exit  : always 0. This is an observer; it never blocks a tool or a turn.

CONFIG
  Read from CHECKPOINT_CONFIG, or `checkpoint.config.json` next to this file.
  See checkpoint.config.example.json for every field.

KILL SWITCH: CHECKPOINT_DISABLE in {1,true,yes,on} -> full no-op.

CLI (for setup and verification, no harness needed)
  --once       force a checkpoint write now, ignoring the cooldown; print path.
  --dry-run    build the checkpoint and print it to stdout; write nothing.
  --self-test  run built-in structural checks; exit 0/1.
"""

import argparse
import glob
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

_TRUTHY = {"1", "true", "yes", "on"}

DEFAULTS = {
    "checkpoint_path": "./.session-checkpoint/checkpoint.md",
    "cooldown_s": 600,
    # Each source: {"path": "...", "max_lines": 40, "label": "Session briefing"}.
    "sources": [],
    "include_git_log": False,
    "git_log_dir": ".",
    "git_log_count": 5,
    # Globs listed (as counts + names) under a "State" section. Content is NOT
    # dumped; only paths, so this stays cheap and leaks nothing large.
    "state_globs": [],
    # Busy-guarded breakpoint signal (job B). All optional.
    "busy_probe_command": "",        # exit 0 = BUSY, non-zero = idle
    "breakpoint_probe_command": "",  # exit 0 = a clean reset is DUE
    "on_breakpoint_command": "",     # fired once/session when due AND idle
    "session_state_path": "./.session-checkpoint/session-state.json",
    "recovery_hint": "Read this checkpoint, then re-read the source files it names, then continue.",
}


def _truthy(value):
    return str(value).strip().lower() in _TRUTHY if value is not None else False


def load_config():
    path = os.environ.get("CHECKPOINT_CONFIG")
    if not path:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "checkpoint.config.json")
    cfg = json.loads(json.dumps(DEFAULTS))  # deep copy
    try:
        with open(path, encoding="utf-8") as f:
            user = json.load(f)
        if isinstance(user, dict):
            cfg.update({k: v for k, v in user.items() if v is not None})
    except Exception:
        pass
    return cfg


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_capped(path, max_lines):
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()[: max(1, int(max_lines))]
        return "".join(lines).strip()
    except (FileNotFoundError, PermissionError, OSError):
        return ""


def _git_log(repo_dir, count):
    try:
        out = subprocess.run(
            ["git", "-C", str(repo_dir), "log", "--oneline", "-" + str(int(count))],
            capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() or "(none)"
    except Exception:
        return "(unavailable)"


def _state_listing(globs):
    lines = []
    for pattern in globs:
        matches = sorted(glob.glob(os.path.expanduser(pattern)))
        names = ", ".join(os.path.basename(m) for m in matches[:20])
        more = "" if len(matches) <= 20 else " (+" + str(len(matches) - 20) + " more)"
        lines.append("- `" + pattern + "`: " + str(len(matches)) + " item(s)"
                     + ((" - " + names + more) if matches else ""))
    return "\n".join(lines) if lines else "(none configured)"


def build_checkpoint(cfg):
    """Assemble the full checkpoint text from the configured sources. Pure: no
    writes, so --dry-run and --self-test exercise exactly what SAVE persists."""
    parts = [
        "# Session Checkpoint",
        "",
        "Written: " + _now_iso(),
        "Purpose: rolling pre-reset snapshot. If you are reading this after a "
        "context reset or compaction, this is your recovery point.",
        "",
    ]
    for src in cfg.get("sources", []):
        if not isinstance(src, dict) or not src.get("path"):
            continue
        label = src.get("label") or src["path"]
        body = _read_capped(src["path"], src.get("max_lines", 40))
        parts.append("## " + str(label) + " (snapshot)")
        parts.append(body if body else "(unavailable)")
        parts.append("")
    if _truthy(cfg.get("include_git_log")):
        parts.append("## Recent Commits")
        parts.append(_git_log(cfg.get("git_log_dir", "."), cfg.get("git_log_count", 5)))
        parts.append("")
    if cfg.get("state_globs"):
        parts.append("## State")
        parts.append(_state_listing(cfg["state_globs"]))
        parts.append("")
    parts.append("## Recovery")
    parts.append(str(cfg.get("recovery_hint", DEFAULTS["recovery_hint"])))
    parts.append("")
    return "\n".join(parts)


def _atomic_write(path, text):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def _should_save(path, cooldown_s):
    try:
        age = time.time() - os.stat(path).st_mtime
        return age >= float(cooldown_s)
    except OSError:
        return True  # no checkpoint yet


# --- Job B: busy-guarded breakpoint signal ---------------------------------


def _probe(cmd):
    """Run a probe. Return True if it exited 0, False on non-zero, None on
    error/absent. Convention: busy_probe 0 = BUSY; breakpoint_probe 0 = DUE."""
    if not cmd:
        return None
    try:
        return subprocess.run(cmd, shell=True, timeout=10).returncode == 0
    except Exception:
        return None


def _session_id(data):
    for key in ("session_id", "transcript_path"):
        val = data.get(key)
        if val:
            return str(val)
    return "default"


def _load_session_state(path):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _maybe_signal_breakpoint(cfg, data):
    """Fire on_breakpoint_command once per session when a reset is DUE and work
    is idle. Missing probes or command make this a no-op. The busy guard means we
    never fire mid-work: only a confirmed-idle probe releases the signal."""
    on_cmd = cfg.get("on_breakpoint_command")
    bp_cmd = cfg.get("breakpoint_probe_command")
    if not on_cmd or not bp_cmd:
        return
    if _probe(bp_cmd) is not True:
        return  # not due
    if _probe(cfg.get("busy_probe_command")) is True:
        return  # busy: do not nag mid-work
    sid = _session_id(data)
    state_path = cfg.get("session_state_path")
    state = _load_session_state(state_path)
    if state.get("signalled_session") == sid:
        return  # already signalled this session
    try:
        subprocess.run(on_cmd, shell=True, timeout=15)
    except Exception:
        return
    try:
        os.makedirs(os.path.dirname(os.path.abspath(state_path)), exist_ok=True)
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump({"signalled_session": sid, "ts": _now_iso()}, f)
    except Exception:
        pass


# --- entrypoints ------------------------------------------------------------


def run_hook():
    if _truthy(os.environ.get("CHECKPOINT_DISABLE")):
        return 0
    try:
        data = json.load(sys.stdin)
        data = data if isinstance(data, dict) else {}
    except Exception:
        data = {}
    cfg = load_config()
    # Job A: save (throttled).
    if _should_save(cfg["checkpoint_path"], cfg["cooldown_s"]):
        try:
            _atomic_write(cfg["checkpoint_path"], build_checkpoint(cfg))
        except Exception:
            pass
    # Job B: busy-guarded breakpoint signal.
    try:
        _maybe_signal_breakpoint(cfg, data)
    except Exception:
        pass
    return 0


def _self_test():
    import tempfile
    checks = []
    tmp = tempfile.mkdtemp()
    src = os.path.join(tmp, "briefing.md")
    with open(src, "w", encoding="utf-8") as f:
        f.write("line one of state\nline two\n")
    cfg = dict(DEFAULTS)
    cfg["checkpoint_path"] = os.path.join(tmp, "cp.md")
    cfg["sources"] = [{"path": src, "max_lines": 5, "label": "Briefing"}]
    cfg["state_globs"] = [os.path.join(tmp, "*.md")]

    text = build_checkpoint(cfg)
    checks.append(("header present", "# Session Checkpoint" in text))
    checks.append(("source content included", "line one of state" in text))
    checks.append(("source label rendered", "## Briefing (snapshot)" in text))
    checks.append(("state section present", "## State" in text))
    checks.append(("recovery section present", "## Recovery" in text))

    _atomic_write(cfg["checkpoint_path"], text)
    checks.append(("checkpoint written", os.path.isfile(cfg["checkpoint_path"])))
    checks.append(("fresh checkpoint skips save", not _should_save(cfg["checkpoint_path"], 600)))
    checks.append(("stale checkpoint triggers save", _should_save(cfg["checkpoint_path"], 0)))

    ok = True
    for name, passed in checks:
        sys.stderr.write(("PASS " if passed else "FAIL ") + name + "\n")
        ok = ok and passed
    return ok


def main(argv=None):
    parser = argparse.ArgumentParser(description="Rolling session checkpoint writer.")
    parser.add_argument("--once", action="store_true", help="Force a write now, ignoring cooldown.")
    parser.add_argument("--dry-run", action="store_true", help="Build and print; write nothing.")
    parser.add_argument("--self-test", action="store_true", help="Run built-in checks and exit.")
    args = parser.parse_args(argv)

    if args.self_test:
        return 0 if _self_test() else 1

    cfg = load_config()
    if args.dry_run:
        sys.stdout.write(build_checkpoint(cfg))
        return 0
    if args.once:
        _atomic_write(cfg["checkpoint_path"], build_checkpoint(cfg))
        print("wrote checkpoint: " + cfg["checkpoint_path"])
        return 0
    return run_hook()


if __name__ == "__main__":
    sys.exit(main())
