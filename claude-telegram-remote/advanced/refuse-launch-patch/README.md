# Refuse-Launch Patch for the Telegram MCP Plugin

A local patch to `telegram@claude-plugins-official` that prevents Claude
Desktop's agent-mode sub-claudes from hijacking the polling slot from your
Code session's bun.

**Optional, but strongly recommended if you run Claude Desktop alongside
Claude Code.** The base `claude-telegram-remote` setup works fine without it
on systems where Desktop is never running, never has agent-mode features
enabled, and never has the telegram plugin loaded in Customize.

## The bug this patch fixes

Claude Desktop's "agent mode" features (Computer Use, Skills, ccd_session,
Claude in Chrome, etc.) spawn CLI sub-claude processes via
`~/Library/Application Support/Claude/local-agent-mode-sessions/...`. Those
sub-claudes inherit the FULL plugin-dir set from `~/.claude/plugins/cache/`
regardless of what's toggled in Desktop's Customize panel.

If the telegram plugin is in your global plugin cache (which it is whenever
your Code session uses it), every Desktop sub-claude that fires loads the
plugin, spawns its own `bun server.ts`, and the new bun then SIGTERMs your
legitimate Code session's bun via the upstream "replacing stale poller" path.

What you observe:

- Telegram bot suddenly stops replying mid-Code session, no obvious cause.
- `pgrep -af "bun.*server.ts"` shows multiple bun processes spawning every
  few minutes when Desktop is running.
- Multiple `claude` processes in `ps -A` whose argv contains BOTH
  `--plugin-dir .../local-agent-mode-sessions/...` AND
  `--plugin-dir .../claude-plugins-official/telegram/0.0.6`. That's the
  smoking gun: Desktop sub-claudes loading the plugin.
- Extended Telegram silence (60+ minutes) followed by self-recovery once the
  Desktop sub-claudes shut down.

The Customize panel toggle is not a durable lever. The toggle has been
observed re-enabling itself after Desktop restarts even when
`~/.claude/settings.json` correctly stores `enabledPlugins: false`. Either
Desktop reads "loaded right now" instead of the disk preference, or Desktop's
startup re-writes the value silently. Either way, you cannot rely on the UI
toggle to keep the plugin out of Desktop's plugin set.

Root cause is upstream:

- [anthropics/claude-code#43645](https://github.com/anthropics/claude-code/issues/43645):
  agent mode creates hundreds of junk sessions; documents the
  `local-agent-mode-sessions` plugin inheritance behavior.

## What this patch does

At server.ts startup, BEFORE the upstream "replacing stale poller" path
fires, walk the parent process chain (depth-cap 12). If any ancestor's
command line contains `local-agent-mode-sessions` OR `Claude.app`, this bun
was launched by Claude Desktop. In that case, exit cleanly with `code 0`
without claiming the polling slot.

A clean `process.exit(0)` drops the MCP stdio transport cleanly without
triggering retry/backoff loops on the Desktop sub-claude side. That sub-claude
sees the MCP server as failed-to-start, continues without the telegram tools,
and the cascade is broken.

Only legitimate `claude --channels plugin:telegram@claude-plugins-official`
invocations from your Code session can reach the polling slot, because
neither `Claude.app` nor `local-agent-mode-sessions` appears in their parent
chain.

The patch logs every check (pass, refuse, ps-error) to
`~/.claude/channels/telegram/server.log` so you can audit decisions. Sample
output for a healthy Code-session spawn:

```
2026-05-08T21:18:15.102Z pid=16047 refuse-launch check-passed chain-depth=4
```

Sample output for a Desktop sub-claude bouncing off the patch:

```
2026-05-08T22:34:01.221Z pid=51234 refuse-launch reason="local-agent-mode-sessions in ancestor pid=51100 depth=2" chain-depth=3
2026-05-08T22:34:01.222Z pid=51234 refuse-launch ancestor pid=51230 <home>/.bun/bin/bun server.ts
2026-05-08T22:34:01.223Z pid=51234 refuse-launch ancestor pid=51220 bun run --cwd <home>/.claude/plugins/cache/claude-plugins-official/telegram/0.0.6 --shell=bun --silent start
2026-05-08T22:34:01.224Z pid=51234 refuse-launch ancestor pid=51100 /Applications/Claude.app/Contents/.../local-agent-mode-sessions/<session-id>
2026-05-08T22:34:01.225Z pid=51234 refuse-launch exiting cleanly with code 0
```

## Installation

```bash
python3 apply.py              # apply to the highest installed plugin version
python3 apply.py --check      # report only; exit 0 if patched, 2 if missing
python3 apply.py --dry-run    # print the unified diff without writing
```

After applying, run `/reset` inside Claude Code. The bun MCP child does not
hot-reload `server.ts`, so the patch only takes effect once the plugin
process respawns. **Do not kill the bun process mid-session**; that
permanently disconnects the Telegram MCP channel until the next session
start. `/reset` is the safe way.

## Idempotency

`apply.py` is idempotent. It checks for the patch sentinel
(`LOCAL PATCH (not upstream): refuse-launch-from-agent-mode-sessions`) and
no-ops if the file is already patched. Safe to run on every plugin upgrade
without verifying status first.

## The known rot problem

Plugin auto-upgrades wipe local patches silently. Re-run `apply.py` after
every plugin version bump to put the patch back. A simple cron or shell-rc
helper that runs `python3 apply.py --check` daily will tell you when the
patch has gone missing without re-applying.

## Compatibility

The patch anchors on `const ENV_FILE = join(STATE_DIR, '.env')`, which is a
state-path constant near the top of upstream `server.ts`. This boundary has
been stable across the 0.0.x line, so the patch should apply cleanly to any
0.0.x version unless upstream rewrites the state-path block.

If `apply.py` aborts with `insert anchor not found`, upstream has moved the
anchor. Re-derive the patch manually against the new shape and update
`apply.py`.

## Stacking with other patches

The patch is self-contained: it does NOT depend on the sibling
`reply-context-patch` or any other local patch. You can install:

- Just refuse-launch
- Just reply-context
- Both
- Neither

Sentinels and anchors are disjoint, so the apply scripts will not interfere
with each other.

## Verifying the patch is firing

After applying and `/reset`-ing, your next bun spawn should write a
`refuse-launch check-passed` line to
`~/.claude/channels/telegram/server.log`. To watch in real time:

```bash
tail -f ~/.claude/channels/telegram/server.log | grep refuse-launch
```

If you have Claude Desktop running with agent-mode features active, you
should also see `refuse-launch reason=...` lines whenever a Desktop
sub-claude attempts to load the plugin. Those lines are the patch doing its
job: Desktop tried to take the slot, refused itself, your bun stayed alive.

If you never see any `refuse-launch` lines, either the patch did not apply
(re-run `apply.py --check`), or your bun spawn predates the patch (run
`/reset` to spawn a fresh bun).
