#!/usr/bin/env python3
"""Apply the refuse-launch-from-agent-mode-sessions patch to the Telegram MCP plugin's server.ts.

Why this patch exists
---------------------
If you run Claude Code's ``telegram@claude-plugins-official`` plugin alongside
Claude Desktop, you can hit a thrash bug: every Desktop "agent mode" sub-claude
(Computer Use, Skills, ccd_session, Claude in Chrome, etc.) inherits the global
plugin cache from ``~/.claude/plugins/cache/``, spawns its OWN ``bun server.ts``
for the telegram plugin, and the new bun then SIGTERMs the bun owned by your
legitimate Code session via the upstream "replacing stale poller" path. Net
effect: rapid-fire bun cycling, MCP disconnects, and extended Telegram outages
while your Code session sits unable to reach the bot.

Root cause is upstream:
- anthropics/claude-code#43645: agent mode creates hundreds of junk sessions.
  ``local-agent-mode-sessions`` plugin inheritance is documented there.
- The Customize panel toggle for the telegram plugin is not a durable lever:
  Desktop has been observed re-enabling it across restarts even when
  ``~/.claude/settings.json`` correctly stores ``enabledPlugins: false``.

This patch is belt-and-suspenders: at startup, server.ts walks its parent
process chain (depth-cap 12). If any ancestor's command line contains
``local-agent-mode-sessions`` OR ``Claude.app``, the bun was launched by Claude
Desktop and refuses to claim the polling slot via ``process.exit(0)`` BEFORE
the SIGTERM-stale-poller path further down can fire. Only legitimate
``claude --channels plugin:telegram@claude-plugins-official`` invocations from
your Code session are allowed to take the slot.

Symptoms users would notice without this patch
----------------------------------------------
- Telegram bot suddenly stops replying mid-session, no obvious cause.
- ``ps -A | grep "bun.*server.ts"`` shows multiple bun processes spawning
  every few minutes when Desktop is running and you have agent-mode features
  enabled.
- ``~/.claude/channels/telegram/server.log`` shows repeated ``replacing stale
  poller`` lines (assuming the stderr-instrumentation patch is also applied;
  if not, the rapid bun cycling is invisible until you go looking).
- Extended Telegram silence (60+ minutes) followed by self-recovery once
  Desktop sub-claudes shut down.

Plugin auto-upgrades will wipe this patch silently. Re-run this script after
each upgrade — it's idempotent (sentinel check skips already-patched files).

Usage
-----
    python3 apply.py             # apply to the highest installed plugin version
    python3 apply.py --check     # report only, do not modify
    python3 apply.py --dry-run   # print the diff, do not write

After applying you MUST ``/reset`` Claude Code so the bun MCP child reloads
``server.ts``. Killing bun mid-session permanently disconnects the MCP
channel (no hot-reload support).

Compatibility
-------------
The patch anchors on ``const ENV_FILE = join(STATE_DIR, '.env')``, which is a
state-path constant near the top of upstream server.ts and has been stable
across the 0.0.x line. Tested against plugin version 0.0.6. The script will
still apply against newer versions if the anchor is unchanged; if upstream
moves the anchor, the script aborts loudly rather than guessing.

The patch is self-contained: it does NOT depend on the sibling
reply-context-patch or any other local patch. You can install either patch,
both, or neither — the sentinels and anchors are disjoint.
"""
from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

PLUGIN_ROOT = Path.home() / ".claude/plugins/cache/claude-plugins-official/telegram"
SENTINEL = "LOCAL PATCH (not upstream): refuse-launch-from-agent-mode-sessions"

# Anchor line: insert the patch block on the line AFTER this anchor. ENV_FILE
# is the last state-path constant before runtime startup in upstream server.ts.
# Stable boundary, unlikely to shift across plugin versions.
INSERT_ANCHOR = "const ENV_FILE = join(STATE_DIR, '.env')"

PATCH_BLOCK = '''\

// LOCAL PATCH (not upstream): refuse-launch-from-agent-mode-sessions
// Mitigates a thrash bug where Claude Desktop's agent-mode sub-claudes
// (Computer Use, Skills, ccd_session, Claude in Chrome, etc.) inherit the
// global plugin cache and spawn their own bun server.ts, which SIGTERMs the
// bun owned by your legitimate Code session via the SIGTERM-stale-poller
// path further down. Walks the parent process chain (depth-cap 12). If any
// ancestor argv contains "local-agent-mode-sessions" OR "Claude.app", this
// bun was launched by Claude Desktop and exits cleanly with code 0 BEFORE
// claiming the polling slot. Only legitimate `claude --channels` invocations
// from your Code session take the slot.
//
// Self-contained: no dependency on other local patches. Logs decisions to
// ~/.claude/channels/telegram/server.log so you can audit pass/refuse calls.
// Try/catch swallows ps failures so a transient ps error never blocks a
// legitimate Code-session launch.
//
// References:
//   github.com/anthropics/claude-code/issues/43645 (root cause)
//   github.com/oscarsterling/claude-telegram-remote (this repo)
try {
  const { spawnSync } = require('child_process') as typeof import('child_process')
  const { mkdirSync: _rlMkdir, appendFileSync: _rlAppend } = require('fs') as typeof import('fs')
  const _rlLog = (line: string): void => {
    try {
      _rlMkdir(STATE_DIR, { recursive: true, mode: 0o700 })
      const ts = new Date().toISOString()
      _rlAppend(STATE_DIR + '/server.log', `${ts} pid=${process.pid} refuse-launch ${line}\\n`, { mode: 0o600 })
    } catch {}
  }
  let walkPid: number = process.ppid
  let refuseReason: string | null = null
  const ancestors: string[] = []
  for (let depth = 0; walkPid > 1 && depth < 12; depth++) {
    const cmdRes = spawnSync('ps', ['-o', 'command=', '-p', String(walkPid)], { encoding: 'utf8' })
    const cmd = (cmdRes.stdout ?? '').trim()
    if (!cmd) break
    ancestors.push(`pid=${walkPid} ${cmd.slice(0, 240)}`)
    if (cmd.includes('local-agent-mode-sessions')) {
      refuseReason = `local-agent-mode-sessions in ancestor pid=${walkPid} depth=${depth}`
      break
    }
    if (cmd.includes('Claude.app')) {
      refuseReason = `Claude.app in ancestor pid=${walkPid} depth=${depth}`
      break
    }
    const ppidRes = spawnSync('ps', ['-o', 'ppid=', '-p', String(walkPid)], { encoding: 'utf8' })
    const next = parseInt((ppidRes.stdout ?? '').trim(), 10)
    if (!Number.isFinite(next) || next <= 1) break
    walkPid = next
  }
  if (refuseReason) {
    _rlLog(`reason="${refuseReason}" chain-depth=${ancestors.length}`)
    for (const a of ancestors) _rlLog(`ancestor ${a}`)
    _rlLog(`exiting cleanly with code 0`)
    process.exit(0)
  }
  _rlLog(`check-passed chain-depth=${ancestors.length}`)
} catch (err) {
  // ps failures must NOT block legitimate launches. Log and fall through.
  try {
    const { mkdirSync: _rlMkdirE, appendFileSync: _rlAppendE } = require('fs') as typeof import('fs')
    _rlMkdirE(STATE_DIR, { recursive: true, mode: 0o700 })
    _rlAppendE(STATE_DIR + '/server.log', `${new Date().toISOString()} pid=${process.pid} refuse-launch check-error: ${err}\\n`, { mode: 0o600 })
  } catch {}
}
'''


def resolve_latest_plugin_dir() -> Path:
    """Highest installed plugin version directory under PLUGIN_ROOT."""
    if not PLUGIN_ROOT.is_dir():
        sys.exit(f"ERROR: plugin not installed at {PLUGIN_ROOT}")
    versions = [d for d in PLUGIN_ROOT.iterdir() if d.is_dir()]
    if not versions:
        sys.exit(f"ERROR: no plugin versions under {PLUGIN_ROOT}")

    def version_key(p: Path):
        try:
            return tuple(int(x) for x in p.name.split("."))
        except ValueError:
            return (0,)

    return max(versions, key=version_key)


def apply_patch(src: str) -> str:
    if SENTINEL in src:
        return src  # already patched, no-op

    if INSERT_ANCHOR not in src:
        sys.exit(
            f"ERROR: insert anchor not found: {INSERT_ANCHOR!r}\n"
            "Plugin source shape has changed upstream. Re-derive the patch "
            "manually and update this script."
        )

    # Insert the patch block on the line AFTER the anchor line. The anchor
    # appears exactly once in upstream server.ts; we still validate count to
    # refuse on ambiguity introduced by future upstream changes.
    occurrences = src.count(INSERT_ANCHOR)
    if occurrences > 1:
        sys.exit(
            f"ERROR: insert anchor appears {occurrences} times; ambiguous insertion."
        )

    lines = src.splitlines(keepends=True)
    out: list[str] = []
    inserted = False
    for line in lines:
        out.append(line)
        if not inserted and INSERT_ANCHOR in line:
            out.append(PATCH_BLOCK)
            inserted = True

    if not inserted:
        sys.exit("ERROR: failed to insert patch block (anchor matched but insert skipped).")

    return "".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply refuse-launch-from-agent-mode-sessions patch.")
    parser.add_argument("--check", action="store_true", help="report only; do not modify")
    parser.add_argument("--dry-run", action="store_true", help="print diff without writing")
    args = parser.parse_args()

    plugin_dir = resolve_latest_plugin_dir()
    target = plugin_dir / "server.ts"
    if not target.is_file():
        sys.exit(f"ERROR: server.ts missing at {target}")

    original = target.read_text()

    if SENTINEL in original:
        print(f"OK: refuse-launch patch already present in {plugin_dir.name}")
        return 0

    if args.check:
        print(f"MISSING: refuse-launch patch absent in {plugin_dir.name}")
        return 2

    patched = apply_patch(original)

    if args.dry_run:
        diff = difflib.unified_diff(
            original.splitlines(keepends=True),
            patched.splitlines(keepends=True),
            fromfile=f"{target} (upstream)",
            tofile=f"{target} (patched)",
        )
        sys.stdout.writelines(diff)
        print(f"\n[dry-run] would patch {target}")
        return 0

    target.write_text(patched)
    print(f"APPLIED: refuse-launch patch written to {target}")
    print("NEXT STEP: run /reset in Claude Code so bun reloads server.ts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
