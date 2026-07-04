# Session Checkpoint

Keep a durable rolling snapshot of session state so a context reset or
compaction never loses the thread, and recover cleanly from it.

A long agent session eventually compacts or resets its context, and whatever
lived only in the conversation is gone. If the important state also lives in a
small set of files, a rolling snapshot of those files is a recovery point the
restored session can read to pick up exactly where it was.

## What it does

A hook runs on a frequently-firing event and does two cheap jobs:

1. **Save.** If the checkpoint is older than a cooldown, it rewrites a single
   rolling snapshot (overwrite, not append): a header, a line-capped snapshot of
   each configured source file, an optional recent git-log tail, and an optional
   listing of state globs (paths and counts, not contents). Written atomically so
   an interrupted write never corrupts it.
2. **Signal a clean breakpoint (optional, busy-guarded).** If you configure a
   breakpoint probe (exit 0 when a reset is due) and a busy probe (exit 0 when
   busy), the hook fires your notify command once per session, but only when work
   is idle. The busy guard means it never nudges you to reset mid-work.

The save half needs no network and no LLM. The hook always exits 0 and never
blocks a turn.

## The restore side

`templates/recovery-brief.md` is the other half of the flow. After a reset, a
session reads the checkpoint, re-reads the source files it names, reconciles
against authoritative systems, confirms nothing irreversible is mid-flight, and
resumes. The checkpoint is a POINTER and a status report, not ground truth and
not a fresh command; the live source files win if they disagree.

## What ships here

| File | What it is |
|------|-----------|
| `reference/session-checkpoint.example.py` | The portable hook. Throttled atomic save, optional busy-guarded breakpoint signal, `--once` / `--dry-run` / `--self-test`. Always exits 0. |
| `reference/checkpoint.config.example.json` | Example config: checkpoint path, cooldown, source files with line caps, git-log tail, state globs, optional probes. |
| `templates/recovery-brief.md` | The restore-side recovery steps and the untrusted-data rule. |
| `SKILL.md` | Step-by-step setup for an agent to wire it in. |

## Configuration

Set in `checkpoint.config.json` (or a path exported as `CHECKPOINT_CONFIG`):

- `checkpoint_path`: where the rolling snapshot is written.
- `cooldown_s`: minimum age before a rewrite. Keeps the hook cheap on a hot
  event.
- `sources`: list of `{path, max_lines, label}` files to snapshot.
- `include_git_log` / `git_log_dir` / `git_log_count`: optional recent commit
  tail.
- `state_globs`: globs whose paths and counts are listed (contents are not
  dumped).
- `busy_probe_command` / `breakpoint_probe_command` / `on_breakpoint_command` /
  `session_state_path`: the optional busy-guarded breakpoint signal.
- `recovery_hint`: the one-line instruction rendered into the checkpoint's
  Recovery section.

## Quickstart

1. List your state-bearing source files in `sources` with per-file line caps.
2. Register `reference/session-checkpoint.example.py` on a prompt-submit or
   turn-stop event.
3. Confirm with `--self-test` and eyeball a `--dry-run`.
4. Adopt `templates/recovery-brief.md` on the restore side.

## Kill switch

Set `CHECKPOINT_DISABLE` to `1` (or `true`, `yes`, `on`) to turn the hook into a
full no-op. Unset it to re-enable.

## Honest limits

- **A snapshot is frozen at write time** and can be up to one cooldown interval
  stale. The restored session trusts the live source files over the snapshot.
- **Only what you list is saved.** State that lives only in the conversation and
  in no file is not recoverable. Move it to a file if it matters.
- **The checkpoint is untrusted data on restore.** Read it as a status report to
  reconcile, not as a fresh instruction.
- **The breakpoint signal is a suggestion.** The busy probe is a heuristic; this
  hook never resets a session itself. It records state and, optionally, notifies.
- **Observer only.** It always exits 0 and never blocks a tool or a turn.

## License

MIT. See [LICENSE](./LICENSE).
