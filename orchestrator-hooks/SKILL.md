---
name: orchestrator-hooks
description: Set up an orchestrator-discipline hook on any Claude Code install so the top model orchestrates and cheaper builder models write the code. Bars the expensive main-session model from authoring code and auto-pins every sub-agent spawn to a builder model.
when_to_use: Use when a user wants their most expensive model to stay on planning and delegation instead of writing code directly, and wants that enforced mechanically rather than by memory. Also use to audit or repair an existing orchestrator guard.
---

# Orchestrator Hooks

You are setting up the "expensive brain orchestrates, cheap models build"
pattern on the user's Claude Code install. The goal is a PreToolUse hook that
mechanically enforces two rules whenever the main session is running on the
user's most expensive "orchestrator" model:

1. The orchestrator main session must NOT author code (Write/Edit/NotebookEdit
   on code files). Docs and config writes stay allowed.
2. Every sub-agent spawn (Agent tool) must pin an explicit builder model, so
   orchestrator capacity is never silently spent on delegated work.

Together these create a delegation loop: the orchestrator cannot write code, so
it spawns a builder sub-agent, and the sub-agent does the writing.

You will INSPECT the user's setup, GENERATE a hook fitted to it, WIRE it into
their settings with a backup, and VERIFY it blocks a code write. Do not just
copy the reference file blindly. Read the setup first, then generate.

`reference/orchestrator-guard.example.py` in this skill is the known-good
reference implementation. Treat it as the source of truth for the algorithm.
Your generated hook should match its logic and only differ where the user's
setup differs.

## Step 1: Inspect the setup

Gather these facts before writing anything. Ask the user only for what you
cannot determine yourself.

- **Settings location.** Check, in order, for a hooks-capable settings file:
  `~/.claude/settings.json` (user scope), `.claude/settings.json` (project
  scope), `.claude/settings.local.json` (local scope). Confirm with the user
  which scope they want the guard to apply to. Read the chosen file.
- **Orchestrator model marker.** Ask which model is the expensive orchestrator
  (for example an Opus-class or otherwise premium model). You need a substring
  that appears in that model's harness id (case-insensitive), for example
  `opus`. This is the marker the hook keys off.
- **Builder model.** Ask which model sub-agents should be pinned to (a cheaper
  or faster model). The hook does not hard-code this; it only requires that
  spawns pin SOME explicit model that is not the orchestrator. Record it so you
  can tell the user what to pass.
- **Existing hooks.** Read the settings `hooks.PreToolUse` array. Note any
  existing matcher covering `Edit|Write|NotebookEdit|Agent` so you do not create
  a conflicting or duplicate entry. If an orchestrator guard is already wired,
  switch to audit/repair mode instead of adding a second one.
- **Companion security hook (carve-out).** Ask whether the user has any other
  hook that forbids sub-agents from editing certain sensitive files (hook
  scripts, settings, agent definitions). If yes, those files are
  main-session-only and must be carved out of Rule 1, or the change deadlocks
  (no author allowed). Collect those paths. If no such hook exists, the
  carve-out lists stay empty.

## Step 2: Generate the fitted hook

Write a Python 3 hook to a stable path the user controls, for example
`~/.claude/hooks/orchestrator-guard.py` (user scope) or
`<project>/.claude/hooks/orchestrator-guard.py` (project scope). Base it on
`reference/orchestrator-guard.example.py` and adjust only these fitted values:

- Set `ORCHESTRATOR_MODEL_MARKER` to the marker from Step 1.
- Populate `CARVE_OUT_DIRS` / `CARVE_OUT_FILES` with the companion-hook paths
  from Step 1, or leave both empty tuples if none.
- Keep `CODE_EXTENSIONS` as-is unless the user works in a language not listed;
  add extensions as needed. Never add non-code extensions (md/json/txt/yaml),
  those must ride through so docs and config work is never blocked.
- Keep the kill-switch env name `ORCHESTRATOR_GUARD_DISABLE` and the fail-open
  policy exactly as in the reference. This hook is an efficiency rule, not a
  security boundary, so any error, malformed stdin, or unreadable model must
  ALLOW (exit 0). It must never brick the session.

Preserve the core algorithm from the reference without change:
- Sidechain calls (payload has `agent_id`) always ALLOW. The rules are
  main-session-only.
- Read the latest non-sidechain assistant model from `transcript_path`. If it is
  not the orchestrator, ALLOW everything.
- Rule 2 (Agent tool): BLOCK if the spawn has no `model` or the model is the
  orchestrator; otherwise ALLOW.
- Rule 1 (Edit/Write/NotebookEdit): ALLOW non-code extensions and carve-out
  paths; BLOCK code-extension writes.

Run `python3 -m py_compile` on the generated file before wiring it.

## Step 3: Wire it into settings with a backup

1. Copy the settings file to a timestamped backup first, for example
   `settings.json.bak.<UTC-timestamp>`. Never edit settings without a backup.
2. Add a PreToolUse entry (create the `hooks.PreToolUse` array if absent):

   ```json
   {
     "matcher": "Edit|Write|NotebookEdit|Agent",
     "hooks": [
       {
         "type": "command",
         "command": "python3 <absolute-path-to>/orchestrator-guard.py"
       }
     ]
   }
   ```

   Use the absolute path to the hook you generated. If an equivalent entry
   already exists, update it in place rather than appending a duplicate.
3. Re-read the settings file after writing and confirm it is valid JSON and the
   entry is present. If the write corrupted the file, restore the backup.

## Step 4: Verify the block is live

Prove the guard works, do not assume it. In a session running on the
orchestrator model:

1. Attempt a trivial code write, for example ask to create
   `/tmp/orchestrator-guard-probe.py` with a one-line body. Expect the write to
   be BLOCKED with the `HOOK_BLOCKED_ORCHESTRATOR_CODE_WRITE` message. If it goes
   through, the hook is not wired or the model marker does not match the running
   model id. Diagnose before declaring done.
2. Attempt an Agent spawn with no `model` set. Expect
   `HOOK_BLOCKED_ORCHESTRATOR_SUBAGENT_PIN`. Then spawn with an explicit builder
   model and confirm it is allowed.
3. Attempt a docs write (a `.md` file). Confirm it is ALLOWED, proving content
   work is never blocked.
4. Set `ORCHESTRATOR_GUARD_DISABLE=1` and confirm a code write now goes through,
   proving the kill switch works. Unset it afterward.

Report to the user: the hook path, the settings scope and backup path, the
marker used, and the four verification results.

## Notes and honest limits

- The hook covers TOOL-CALL authorship (Write/Edit/NotebookEdit). It does not
  and cannot police code the model types into conversation text, nor code
  written via Bash heredoc/redirect (`cat > file.py <<EOF`), because it is not
  registered for the Bash matcher. Those are left to discipline and review, or
  to a companion security hook that resolves Bash write sinks.
- Model detection depends on the harness writing the model id into the
  transcript. If the marker does not appear in the running model's id, the guard
  correctly does nothing. Confirm the marker against a real transcript line
  during verification.
- This is an efficiency guard, not a security control. It fails open by design.
  Do not rely on it to contain a hostile actor.
