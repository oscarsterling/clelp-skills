# Model Switch Sentinel

Detect when your session is silently served by a cheaper fallback model, and
recover safely instead of letting degraded work quietly become the foundation
for everything after it.

Under capacity pressure a harness can quietly downgrade the model serving your
turns. The work keeps flowing, so nothing looks wrong, and by the time anyone
notices, degraded output is already load bearing. This package makes the switch
visible and drives a careful recovery.

## What it does

A per-turn hook watches the model the session is running on and fires on any
change to a fallback:

1. **Busy check first.** If work may be in flight, the sentinel only alerts and
   waits. It never interrupts live work to switch models. Ring-and-ask is the
   safe default, on purpose: busy heuristics are imperfect, so the sentinel
   prefers to tell a human and wait over acting on a wrong guess.
2. **On confirmed idle**, it emits a switch-back-ready event. Your automation
   then switches back to the intended model, saves a scrubbed context brief,
   clears, and restores the session with plain verbiage about what happened and
   where you were.
3. **It marks the span of turns served by the fallback** and hands that span to
   the restored session, which re-reviews exactly those turns for quality before
   continuing. Degraded work never silently becomes the foundation.

The context brief is treated as untrusted data. Before it is injected into the
restored session it is run through a mechanical scrubber that neutralizes the two
classic injection signatures: a line shaped like a conversational turn boundary,
and angle-bracket markup shaped like a trusted channel wrapper. The scrubber
transforms structure, it does not match a denylist of known bad strings.

## What ships here

| File | What it is |
|------|-----------|
| `reference/model-sentinel.example.py` | The portable per-turn hook. Detects the fallback, does the busy check, tracks the fallback span, emits events. Never switches the model, never blocks a tool. |
| `scripts/brief-scrub.py` | The mechanical brief scrubber. Standalone, `--help`, `--self-test`. |
| `templates/context-brief.md` | The restore brief format, including the re-review-required span section. |
| `reference/sentinel.config.example.json` | Example config: intended marker, handoff and state paths, busy probe, automation seam, auto-switch flag. |
| `SKILL.md` | Step-by-step setup for an agent to wire it into a Claude Code install. |

## The seam (packaging honesty)

Switch-back and clear/restore automation is harness-and-setup dependent. There is
no portable way to swap the running model and clear a session that works across
every install, so this package does not pretend to ship one.

What it ships is the generic half: the detection hook, the event contract, the
brief format, and the scrubber. Where your own plumbing plugs in is a documented
seam, the config field `on_event_command`: a shell command the hook runs once per
event, with the event JSON on stdin. What that command does, notify you, drive a
model swap, clear and restore, is yours to implement for your harness. No
internal transport, paths, or identifiers are shipped here.

## Event contract

The hook appends one JSON object per line to `handoff_path` and, if configured,
pipes the same object to `on_event_command` on stdin. The `action` field tells
your seam what to do:

- `alert_and_wait`: a silent fallback happened and work may be in flight. Notify
  a human, do nothing else, wait.
- `switch_back_ready`: idle on a fallback model. If `requires_ack` is true, ask
  first. When cleared, assemble a brief, scrub it, switch back, clear, restore.
- `re_review_span`: back on the intended model. Inject the scrubbed brief whose
  re-review section names `fallback_span`, so the restored session re-reviews
  those turns before continuing.

Each event carries `fallback_span` (first seen, last seen, turn count) so the
degraded span is always explicit.

## Quickstart

1. Invoke the `model-switch-sentinel` skill in a Claude Code session, or set it
   up by hand from `SKILL.md`.
2. Copy the hook and the config somewhere stable, set `intended_model_marker` to
   a substring of your intended model id, and register the hook (the `Stop` event
   is the recommended registration; it fires at the idle boundary of each turn).
3. Point `busy_probe_command` at a mechanical busy check, or leave it empty and
   the sentinel stays in alert-and-wait rather than ever claiming idle itself.
4. Wire `on_event_command` to your own switch-back plumbing when you are ready to
   automate. Until then, consume the handoff JSONL by hand.

Verify with `python3 -m py_compile reference/model-sentinel.example.py` and
`python3 scripts/brief-scrub.py --self-test`.

## Kill switch

Set `MODEL_SENTINEL_DISABLE` to `1` (or `true`, `yes`, `on`) to turn the hook
into a full no-op before it does any work. Unset it to re-enable.

## Honest limits

- **Detection depends on the harness recording the model id** in the transcript.
  If it does not, or the marker does not appear, the hook correctly does nothing.
- **Busy heuristics are imperfect.** That is why ring-and-ask is the default and
  why `auto_switch` is false unless you deliberately opt in.
- **Switch-back and clear/restore are not portable.** The package ships detection,
  the event contract, the brief format, and the scrubber. The plumbing is yours.
- **The scrubber demotes two structural signatures, it is not a content filter.**
  It makes turn-boundary and markup impersonation mechanically inert; it does not
  judge meaning. The brief stays untrusted data.
- **This is an observer, not a gate.** The hook never blocks a tool and fails
  open on any error. It cannot brick a session and it cannot contain a hostile
  actor.

## License

MIT. See [LICENSE](./LICENSE).
