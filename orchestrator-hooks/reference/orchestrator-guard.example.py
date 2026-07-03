#!/usr/bin/env python3
"""
Orchestrator Guard - a PreToolUse hook that keeps your top model on
orchestration only.

WHAT IT DOES
  When the main session is running on your expensive "orchestrator" model, that
  model is for thinking and delegating, NOT for authoring code. Code writes must
  go to a sub-agent (the Agent tool) pinned to a cheaper or faster "builder"
  model. This hook encodes two rules mechanically:
    1. The orchestrator main session must not write code directly.
    2. Every sub-agent spawn must pin an explicit builder model, so orchestrator
       capacity is never silently burned on delegated work.

  The result is a delegation loop: the orchestrator main session cannot author
  code, so it spawns a sub-agent, and the sub-agent writes where it is sent.

HOW IT DECIDES (register as PreToolUse matcher "Edit|Write|NotebookEdit|Agent"):
  - Sub-agent (sidechain) calls carry agent_id in the stdin JSON. If agent_id is
    present -> ALLOW immediately. Both rules apply to the main session only; the
    sub-agent IS the desired delegation path.
  - The latest MAIN-chain assistant model is read from the transcript tail
    (transcript_path in the stdin payload; sidechain turns skipped). If it does
    not contain the ORCHESTRATOR_MODEL_MARKER -> ALLOW everything. The rule
    protects your orchestrator model specifically; a cheaper-fallback session is
    free to work directly.
  - Rule 1 (code-write gate): for Edit/Write/NotebookEdit, BLOCK when the target
    path has a CODE extension AND is not in the carve-out set. Non-code
    extensions (md/json/txt/yaml/...) always ride through, so docs and config
    work is never blocked.
  - Rule 2 (sub-agent pin gate): for the Agent tool, BLOCK if the spawn has no
    explicit model, or the model is itself the orchestrator. Any explicit builder
    model is allowed.

CARVE-OUT CONCEPT
  Some code paths are main-session-only by design. If a companion security hook
  forbids sub-agents from editing certain sensitive files (hook scripts,
  settings, agent definitions), then blocking the main session from those same
  files too would DEADLOCK the change: no author would be allowed. List those
  paths in CARVE_OUT_DIRS / CARVE_OUT_FILES so the main session keeps authoring
  them. If you have no such companion restriction, leave the carve-out lists
  empty.

KILL SWITCH: ORCHESTRATOR_GUARD_DISABLE in {1,true,yes,on} -> full no-op before
  any IO.

FAIL-OPEN POLICY: this guard is an EFFICIENCY rule, not a security boundary. If
  stdin is malformed, the model cannot be read, or ANY exception is raised, the
  hook ALLOWS (exit 0). It must never brick the session.

HONEST RESIDUAL: Bash heredoc / redirect file writes (`cat > foo.py <<EOF`) are
  NOT covered by this guard. Its write detection is path-extension on the
  Edit/Write/NotebookEdit target, and it is never registered for the Bash
  matcher. That path is left to discipline plus review, or to a companion
  security hook that resolves Bash write sinks.

Exit codes: 0 = allow, 2 = block (stderr is fed back to the model).
Token cost: 0. No LLM. Pure local file IO, tail-read only.
"""

import json
import os
import sys

# --- CONFIGURE THESE FOR YOUR SETUP ----------------------------------------

# Substring that identifies your ORCHESTRATOR model in the harness model id
# (case-insensitive). The rules only fire when the main session is on this
# model. Replace with your actual orchestrator model marker.
ORCHESTRATOR_MODEL_MARKER = "orchestrator-model"

# Code file extensions that trigger Rule 1 when the orchestrator writes them.
# Non-code extensions (md json txt yaml ...) are deliberately absent so they
# always ride through with content work.
CODE_EXTENSIONS = {
    "py", "sh", "bash", "zsh", "js", "mjs", "cjs", "ts", "tsx", "jsx",
    "go", "rs", "rb", "php", "swift", "m", "c", "h", "cc", "cpp", "hpp",
    "java", "kt", "sql", "vue", "svelte", "pl", "lua",
}

# Carve-out: main-session-only paths that stay ALLOWED for the orchestrator (see
# CARVE-OUT CONCEPT above). Absolute paths. Leave empty if you have no companion
# security hook that would otherwise deadlock these files.
CARVE_OUT_DIRS = (
    # os.path.expanduser("~/your-project/scripts/hooks/"),
)
CARVE_OUT_FILES = (
    # os.path.expanduser("~/.claude/settings.json"),
)

# --- END CONFIG ------------------------------------------------------------

_TRUTHY = {"1", "true", "yes", "on"}

# Block messages. One line, informative, no em dashes.
MSG_CODE_WRITE = (
    "HOOK_BLOCKED_ORCHESTRATOR_CODE_WRITE: main session is on the orchestrator "
    "model; code writes go to a builder sub-agent (Agent tool with an explicit "
    "builder model). Spawn the sub-agent instead of editing directly. Kill "
    "switch: ORCHESTRATOR_GUARD_DISABLE=1."
)
MSG_SUBAGENT_PIN = (
    "HOOK_BLOCKED_ORCHESTRATOR_SUBAGENT_PIN: pin this sub-agent to an explicit "
    "builder model so orchestrator capacity is not burned on delegated work."
)

_BLOCK_SIZE = 64 * 1024
DEFAULT_TAIL_BYTES = 1 * 1024 * 1024


def _truthy(value):
    return str(value).strip().lower() in _TRUTHY if value is not None else False


def tail_byte_cap():
    raw = os.environ.get("ORCHESTRATOR_GUARD_TAIL_BYTES")
    if raw:
        try:
            return max(_BLOCK_SIZE, int(raw))
        except ValueError:
            pass
    return DEFAULT_TAIL_BYTES


# --- transcript tail reader ------------------------------------------------


def iter_lines_reversed(path, block_size=_BLOCK_SIZE, max_bytes=DEFAULT_TAIL_BYTES):
    """Yield complete text lines from the end of `path` backward.

    Reads at most `max_bytes`, one block at a time, so the hot path never reads a
    multi-MB transcript forward. A line straddling a block boundary is carried as
    `tail` and prepended to the previous block.
    """
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        pos = f.tell()
        scanned = 0
        tail = b""
        while pos > 0 and scanned < max_bytes:
            read_size = min(block_size, pos)
            pos -= read_size
            scanned += read_size
            f.seek(pos)
            data = f.read(read_size) + tail
            if pos == 0:
                segment = data
                tail = b""
            else:
                newline_idx = data.find(b"\n")
                if newline_idx == -1:
                    tail = data
                    continue
                segment = data[newline_idx + 1:]
                tail = data[:newline_idx]
            for line in reversed(segment.split(b"\n")):
                if line.strip():
                    yield line.decode("utf-8", errors="replace")


def read_latest_model(transcript_path):
    """Return the latest non-sidechain assistant `message.model`, or None.

    Scans the transcript tail backward and stops at the first match, so the
    common case json-parses only the last few lines. Sidechain (sub-agent) turns
    are skipped so a builder sub-agent never masquerades as the main model.
    """
    try:
        if not transcript_path or not os.path.isfile(transcript_path):
            return None
        for line in iter_lines_reversed(transcript_path, max_bytes=tail_byte_cap()):
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if not isinstance(obj, dict) or obj.get("type") != "assistant":
                continue
            if obj.get("isSidechain"):
                continue
            message = obj.get("message")
            if not isinstance(message, dict):
                continue
            model = message.get("model")
            if model:
                return str(model)
    except Exception:
        return None
    return None


# --- decision helpers ------------------------------------------------------


def is_orchestrator(model):
    return ORCHESTRATOR_MODEL_MARKER.lower() in str(model).lower() if model else False


def _canon(p):
    """Absolute, ~-expanded, symlink-resolved, casefolded path. realpath keeps a
    not-yet-created leaf while resolving existing parents, so a write target that
    does not exist yet still canonicalizes correctly."""
    return os.path.realpath(os.path.abspath(os.path.expanduser(p or ""))).casefold()


def has_code_extension(path):
    if not path:
        return False
    ext = os.path.splitext(path)[1].lstrip(".").lower()
    return ext in CODE_EXTENSIONS


def is_carve_out(path):
    """True iff `path` is a main-session-only path that must stay ALLOWED
    (blocking the orchestrator would deadlock it)."""
    if not path:
        return False
    c = _canon(path)
    for f in CARVE_OUT_FILES:
        if c == _canon(f):
            return True
    for d in CARVE_OUT_DIRS:
        dc = _canon(d).rstrip("/") + "/"
        if c == dc[:-1] or c.startswith(dc):
            return True
    return False


def target_path(tool_input):
    """The write target for a mutating tool: file_path (Edit/Write) or
    notebook_path (NotebookEdit)."""
    return tool_input.get("file_path") or tool_input.get("notebook_path") or ""


# --- verdict emitters (each terminates the process) ------------------------


def allow():
    sys.exit(0)


def block(message):
    sys.stderr.write(message + "\n")
    sys.exit(2)


# --- main ------------------------------------------------------------------


def main():
    # 1. Kill switch: full no-op before any IO.
    if _truthy(os.environ.get("ORCHESTRATOR_GUARD_DISABLE")):
        allow()

    # 2. Parse stdin. Malformed -> fail open (this is an efficiency rule).
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError, ValueError):
        allow()
    if not isinstance(data, dict):
        allow()

    # 3. Sub-agent (sidechain) calls carry agent_id -> ALWAYS allow. Both rules
    #    apply to the main session only; the sub-agent IS the delegation path.
    if "agent_id" in data:
        allow()

    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})
    if not isinstance(tool_input, dict):
        tool_input = {}

    # 4. Read the latest main-chain model. Unreadable -> fail open. Not the
    #    orchestrator -> allow (a cheaper-fallback session is free to work).
    model = read_latest_model(data.get("transcript_path"))
    if model is None or not is_orchestrator(model):
        allow()

    # 5. Rule 2: sub-agent model pin gate (Agent tool).
    if tool_name == "Agent":
        pinned = tool_input.get("model")
        if not pinned or is_orchestrator(pinned):
            block(MSG_SUBAGENT_PIN)
        allow()

    # 6. Rule 1: code-write gate (Edit/Write/NotebookEdit).
    if tool_name in ("Edit", "Write", "NotebookEdit"):
        path = target_path(tool_input)
        if not has_code_extension(path):
            allow()          # non-code (md/json/txt/html/css/...) rides content work
        if is_carve_out(path):
            allow()          # main-session-only path; blocking main would deadlock
        block(MSG_CODE_WRITE)

    # 7. Any other tool the matcher happens to route here: allow.
    allow()


def guarded_main():
    # The guard must only ever block on a POSITIVE rule match. An internal bug
    # must fail OPEN (exit 0), not crash with a traceback the harness shows as a
    # hook error. SystemExit passes through so allow (0) and block (2) keep their
    # contract.
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)


if __name__ == "__main__":
    guarded_main()
