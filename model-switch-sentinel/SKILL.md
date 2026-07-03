---
name: model-switch-sentinel
description: Detect when a session is silently served by a cheaper fallback model, ring the human first if work is in flight, and drive a safe switch-back that saves a scrubbed context brief and re-reviews the degraded turns before continuing. Ships a portable hook plus a mechanical brief scrubber, with your switch-back plumbing wired at a documented seam.
when_to_use: Use when a user wants to be protected from silent capacity fallback, where the harness quietly serves turns on a cheaper model and degraded work becomes the foundation for later work before anyone notices. Also use to set up or audit an existing model-switch sentinel.
---

# Model Switch Sentinel

You are setting up a sentinel that watches the session model per turn and reacts
when it silently falls back to a cheaper model. The recovery has to be SAFE: it
must not interrupt live work, it must not trust a brief blindly, and it must not
let degraded output silently become the foundation for later work.

The package gives you two portable, runnable pieces and one documented seam:

- `reference/model-sentinel.example.py` - the per-turn hook. It detects the
  fallback, does a busy check first, tracks the span of fallback turns, and
  emits events. It never switches the model itself and never blocks a tool.
- `scripts/brief-scrub.py` - the mechanical scrubber that neutralizes
  turn-boundary and markup injection signatures in the context brief before it
  is injected into the restored session.
- The SEAM: switch-back, context clear, and restore are harness-specific. The
  hook emits events; your automation performs the actual plumbing. You wire it
  at the config `on_event_command` field. The package deliberately ships no
  environment-specific plumbing.

Ring-and-ask is the safe default throughout. Busy heuristics are imperfect, so
the sentinel prefers to alert a human and wait over taking an automatic action
on a wrong guess.

## Step 1: Inspect the setup

- **Settings location.** Check, in order, for a hooks-capable settings file:
  `~/.claude/settings.json` (user scope), `.claude/settings.json` (project
  scope), `.claude/settings.local.json` (local scope). Confirm which scope the
  user wants and read the chosen file.
- **Intended model marker.** Ask which model the session is SUPPOSED to run on.
  You need a substring that appears in that model's harness id (case
  insensitive). Any observed model lacking that marker is treated as a fallback.
- **Busy probe.** Ask how the user can tell, mechanically, that work is in
  flight. This becomes `busy_probe_command`: a shell command that exits 0 when
  busy and non-zero when idle (for example, a check for an active lock file, a
  task file with open items, or a queue depth). Warn the user that any such
  heuristic is imperfect, which is exactly why the default is to ring and wait.
  If they have no reliable probe, leave it empty: the sentinel then stays in
  alert-and-wait and never claims idle on its own.
- **Automation seam.** Ask what should perform the actual switch-back, clear,
  and restore in their harness. This becomes `on_event_command`: a shell command
  the hook runs once per event, with the event JSON on stdin. If they are not
  ready to automate, leave it empty and consume the handoff JSONL manually.

## Step 2: Place the files and write the config

1. Copy `reference/model-sentinel.example.py` to a stable path the user
   controls, for example `~/.claude/hooks/model-sentinel.py`.
2. Copy `reference/sentinel.config.example.json` next to the hook as
   `sentinel.config.json`, or to a path you export as `MODEL_SENTINEL_CONFIG`.
   Fill in:
   - `intended_model_marker` (required)
   - `handoff_path` (JSONL event log)
   - `state_path` (span-tracking state)
   - `busy_probe_command` (from Step 1, or empty)
   - `on_event_command` (from Step 1, or empty)
   - `auto_switch` (leave false unless the user explicitly wants unattended
     switch-back; false keeps ring-and-ask)
3. Run `python3 -m py_compile` on the hook. Run `python3 scripts/brief-scrub.py
   --self-test` and confirm all checks PASS.

## Step 3: Wire the hook into settings with a backup

1. Back up the settings file to a timestamped copy first. Never edit settings
   without a backup.
2. Add a hook entry. Recommend the `Stop` event as the primary registration: it
   fires at the idle boundary of each turn, which is exactly when a safe
   switch-back can happen. Optionally also register `PreToolUse` with a broad
   matcher for earlier detection mid-work, where the busy probe will hold it at
   alert-and-wait.

   ```json
   {
     "matcher": "*",
     "hooks": [
       {
         "type": "command",
         "command": "python3 <absolute-path-to>/model-sentinel.py"
       }
     ]
   }
   ```

3. Re-read the settings file and confirm it is valid JSON and the entry is
   present. If the write corrupted the file, restore the backup.

## Step 4: Wire the automation seam

This is the part only the user's harness can supply. The hook emits events; the
seam turns an event into action. A seam consumer reads one event JSON on stdin
and, based on `action`:

- `alert_and_wait`: notify the human that a silent fallback happened and work is
  (or may be) in flight. Do nothing else. Wait.
- `switch_back_ready`: the session is idle on a fallback model. If `requires_ack`
  is true, ask the human before acting. When cleared to act: assemble a context
  brief from `templates/context-brief.md`, run it through `brief-scrub.py`,
  trigger your harness switch-back to the intended model, clear, and restore
  with the scrubbed brief.
- `re_review_span`: the session is back on the intended model. Inject the
  scrubbed brief whose RE-REVIEW section names the `fallback_span`, so the
  restored session re-reviews those turns before continuing.

Keep the restore verbiage plain: what happened, where you are, what to re-review.
Do not ship or hard-code any environment-specific transport. The seam is a shell
command; what it does is the user's own plumbing.

## Step 5: Verify

- `python3 -m py_compile` on the hook passes.
- `python3 scripts/brief-scrub.py --self-test` reports all PASS.
- Feed the hook a synthetic stdin payload pointing at a transcript whose latest
  assistant model lacks the marker, with a busy probe that exits non-zero
  (idle), and confirm a `switch_back_ready` event lands in the handoff file.
- Repeat with a busy probe that exits 0 and confirm the event is `alert_and_wait`.
- Confirm the kill switch: set `MODEL_SENTINEL_DISABLE=1` and confirm the hook
  no-ops.

## Honest limits

- **Detection depends on the harness recording the model id in the transcript.**
  If it does not, or the marker does not appear, the hook correctly does nothing.
  Confirm the marker against a real transcript line.
- **Busy heuristics are imperfect.** That is the whole reason ring-and-ask is the
  default. Do not set `auto_switch` true unless the user accepts that a wrong
  idle guess could switch during work.
- **Switch-back and clear/restore are not portable.** The package ships the
  detection, the event contract, the brief format, and the scrubber. The plumbing
  that actually swaps the model and clears the session is the user's, wired at
  the seam.
- **The scrubber demotes two structural signatures, it is not a content filter.**
  It makes turn-boundary and markup impersonation mechanically inert. It does not
  judge meaning. The brief stays untrusted data.
- **This is an observer, not a gate.** The hook never blocks a tool and fails
  open on any error. It cannot brick a session and it cannot contain a hostile
  actor.
