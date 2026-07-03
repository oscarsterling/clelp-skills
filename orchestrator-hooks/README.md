# Orchestrator Hooks

Keep your most expensive model on planning and delegation, and let cheaper or
faster models do the actual code writing. This skill sets up a PreToolUse hook
on any Claude Code install that enforces that split mechanically instead of by
memory.

## What it does

When the main session is running on your "orchestrator" model (the expensive
brain), the hook enforces two rules:

1. **No direct code authorship.** The orchestrator cannot Write/Edit/NotebookEdit
   files with a code extension. Docs and config (`.md`, `.json`, `.txt`, `.yaml`,
   and similar) still go through, so planning and configuration work is never
   blocked.
2. **Every sub-agent spawn is pinned to a builder model.** The Agent tool is
   blocked unless the spawn names an explicit model that is not the orchestrator.

The two rules form a delegation loop: the orchestrator cannot author code, so it
spawns a builder sub-agent, and the builder writes where it is sent.

## Why

The most capable model is usually the most expensive and the slowest. Spending
it on mechanical code edits is a poor trade: you burn premium capacity on work a
cheaper model does just as well. The higher-leverage use is orchestration,
holding the plan, deciding what to build, reviewing the result, and delegating
the typing. This hook makes that discipline automatic, so it holds even when
you forget.

## Quickstart

Invoke the `orchestrator-hooks` skill in a Claude Code session and it will:

1. Inspect your setup (settings location, orchestrator model, builder model,
   existing hooks, any companion security hook).
2. Generate a PreToolUse hook fitted to your setup.
3. Wire it into your `settings.json` after taking a timestamped backup.
4. Verify the block is live by attempting a code write and showing it blocked,
   confirming a docs write is allowed, and confirming the kill switch works.

The known-good reference implementation lives at
`reference/orchestrator-guard.example.py`. It runs stand-alone (compile it with
`python3 -m py_compile reference/orchestrator-guard.example.py`) and is the
source of truth for the algorithm. Set `ORCHESTRATOR_MODEL_MARKER` at the top to
the substring that identifies your orchestrator model, register it as a
PreToolUse hook for the matcher `Edit|Write|NotebookEdit|Agent`, and it works
on its own without the generator.

## Kill switch

Set the environment variable `ORCHESTRATOR_GUARD_DISABLE` to `1` (or `true`,
`yes`, `on`) to turn the guard into a full no-op before it does any work. Unset
it to re-enable. Use this when you deliberately want the orchestrator to write
code directly, for example while setting up or debugging the hook itself.

## Carve-out concept

If you already run a companion security hook that forbids sub-agents from editing
certain sensitive files (hook scripts, settings, agent definitions), those files
become main-session-only. Blocking the orchestrator from them too would deadlock
the change, because no author would be allowed. List those paths in the hook's
carve-out configuration so the main session keeps authoring them. If you have no
such companion hook, leave the carve-out lists empty.

## Limitations (honest)

- **Tool-call authorship only.** The hook governs the Write, Edit, and
  NotebookEdit tools. It does not police code the model types directly into its
  conversation text, and it does not cover code written through Bash
  heredoc/redirect (`cat > file.py <<EOF`), because it is not registered for the
  Bash matcher. Those gaps are left to discipline and review, or to a separate
  security hook that resolves Bash write sinks.
- **Model detection depends on the harness.** The guard reads the running model
  id from the session transcript. If your harness does not record the model id,
  or the configured marker does not appear in it, the guard correctly does
  nothing. Verify the marker against a real transcript line during setup.
- **Efficiency rule, not a security boundary.** The guard fails open on any
  error, malformed input, or unreadable model, so it can never brick a session.
  Do not rely on it to contain a hostile actor. It steers your own model's hands,
  nothing more.
