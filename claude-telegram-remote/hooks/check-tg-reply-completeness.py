#!/usr/bin/env python3
"""
Stop hook: enforce Telegram reply completeness on the last turn.

Replaces the LLM-judge prompt-type Stop hook. Deterministic check:
- If the turn's user prompts contained a `<channel source="plugin:telegram:telegram"`
  tag AND no `mcp__plugin_telegram_telegram__reply` tool was called anywhere in
  the turn, BLOCK with "missing TG reply". edit_message does NOT satisfy this
  rule; the TG inbound requires a fresh reply for push notification delivery.
- If a reply tool was called AND there is trailing text after it, BLOCK with
  "trailing terminal text after TG reply". Trailing text is detected via two
  paths:
    1. stdin `last_assistant_message`: the in-flight final text, populated by
       Claude Code even BEFORE it is flushed to the JSONL transcript. If it
       doesn't match any already-persisted text block in this turn, it is
       post-reply trailing text.
    2. Persisted transcript scan (belt-and-braces): any non-empty text block
       positioned after the last reply tool use, either within the same
       assistant message or in a later one.
- Pre-reply / between-reply leak check (2026-05-15 hardening): any assistant
  text block in a TG-triggered turn must be a normalized substring of some
  reply-or-edit tool call's `text` parameter. If a text block is emitted
  BEFORE the first reply or BETWEEN two reply calls and its content does not
  appear in any TG-bound payload, it is invisible terminal text and we BLOCK.
  Why: the original hook only caught text AFTER the last reply, so pre-reply
  status narration ("On it...") and between-reply commentary slipped through.
  Design locked via model bounce against GPT-5.5 on 2026-05-15:
  block over auto-relay (auto-relay risks shipping stale "On it..." text
  after work completes), suppress pre-tool announcements rather than
  duplicate them into the reply payload, normalize only line endings and
  outer whitespace (aggressive normalization hides real mismatches).
- Reply-delivery success check (2026-05-15): the existing "TG inbound requires
  a reply call" rule counted the tool_use, not a successful tool_result. If
  the reply tool returned an error result, the hook used to pass. Now we
  check the last reply tool_use has a corresponding non-error tool_result
  (matched by tool_use_id). Heuristic is conservative: only the explicit
  is_error flag, an "error" key in dict content, or a string starting with
  "Error:" trips it. Missing tool_result entirely is treated as PASS because
  the result may not have landed by hook-fire time.
- Otherwise PASS.

Turn boundary: walks backwards to the most recent real user prompt (a user
message NOT consisting exclusively of tool_result blocks), then treats all
subsequent user/assistant messages as part of the turn. This correctly handles
multi-message flows where tool_result user messages separate assistant
messages within a single logical turn.

Why deterministic: the LLM-judge missed two real failures on 2026-04-13 by
stamping PASS based on an earlier reply-tool call in the same turn, missing
trailing terminal text. See bounce-2026-04-13-131113.md for the design.

Why no silent exit-2 (2026-05-13, Layer 1 of two): one-hour rewake death loop
~21:23-22:30 EDT. Hook detected trailing text, exit-2'd with EMPTY stderr,
the agent got an empty rewake, read it as "ack the user", wrote "Hook passed."
terminal text, loop back. Every exit-2 path now writes actionable stderr
naming what is wrong AND what the next turn should do. Even the
suspected-but-unidentified case writes a debug log path. Silent exit-2 is
banned by construction: decide() returns (code, message) and main() refuses
to print an empty message on a code == 2 path.

Hook contract (Claude Code Stop hook, command type):
- stdin: JSON with `transcript_path` and optionally `last_assistant_message`
- exit 0: PASS (no objection to stopping)
- exit 2: BLOCK; stderr is surfaced to the model as a system reminder

Defaults to PASS on malformed input or missing transcript, to avoid breaking
the agent loop.
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPLY_TOOL = "mcp__plugin_telegram_telegram__reply"
EDIT_TOOL = "mcp__plugin_telegram_telegram__edit_message"
# TG_TEXT_TOOLS: tools whose `text` parameter renders to Telegram (visible to
# the user). Used by the pre-reply/between-reply leak check. edit_message counts
# here because anything in its payload was delivered somewhere visible, even
# though it doesn't satisfy the "inbound requires reply" rule.
TG_TEXT_TOOLS = {REPLY_TOOL, EDIT_TOOL}
TG_CHANNEL_TAG = '<channel source="plugin:telegram:telegram"'

# State directory for runtime artifacts (debug logs, rewake counter,
# loop-event log). All three live under ~/claude-telegram-remote/state/
# by default; override paths individually via environment variables for
# alternate install layouts. The directory is created on first write.
STATE_DIR = Path.home() / "claude-telegram-remote" / "state"

# Debug log directory for suspected-but-unidentified trailing text. Lives
# under STATE_DIR by default. Overridable via TG_HOOK_DEBUG_LOG_DIR.
DEBUG_LOG_DIR = STATE_DIR / "hook-debug"

# Layer 2 (2026-05-13): rewake-counter loop break. If the hook keeps exit-2'ing
# without the model converging on a clean stop, force a release at N=3 to break
# the cycle. Without this, an actionable-but-unactioned directive can still
# loop (e.g., the model misreads even an actionable rewake). The Layer 1 silent-
# exit-2 ban is the primary fix; this is the safety net.
REWAKE_COUNTER_PATH = STATE_DIR / "stop-hook-rewake-counter.json"
REWAKE_LOOP_THRESHOLD = 3  # 4th block in window forces release
REWAKE_LOOP_WINDOW_SECONDS = 60
# Loop-event log: appended one JSONL line per force-release so incidents
# leave a durable trail. Portable across install layouts; override the
# location with TG_HOOK_LOOP_EVENT_LOG_PATH.
LOOP_EVENT_LOG_PATH = STATE_DIR / "stop-hook-loop-events.jsonl"


def load_transcript(path):
    """Read JSONL transcript file. Returns a list of message dicts."""
    if not path:
        return []
    p = Path(path)
    if not p.exists():
        return []
    messages = []
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                messages.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return messages


def extract_message(entry):
    """Extract role and content from a transcript entry. Tolerates wrapper shapes."""
    if "role" in entry and "content" in entry:
        return entry["role"], entry["content"]
    msg = entry.get("message")
    if isinstance(msg, dict) and "role" in msg and "content" in msg:
        return msg["role"], msg["content"]
    return None, None


def content_blocks(content):
    """Normalize content into a list of dict blocks. Handles str shorthand."""
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        return [b for b in content if isinstance(b, dict)]
    return []


def is_tool_result_only(content):
    """True if content is exclusively tool_result blocks (no real user text)."""
    blocks = content_blocks(content)
    if not blocks:
        return False
    return all(b.get("type") == "tool_result" for b in blocks)


def find_turn_boundary(messages):
    """Return (turn_user_idxs, turn_assistant_idxs) for the current turn."""
    turn_start = None
    for i in range(len(messages) - 1, -1, -1):
        role, content = extract_message(messages[i])
        if role == "user" and not is_tool_result_only(content):
            turn_start = i
            break
    if turn_start is None:
        return [], []

    user_idxs = []
    assistant_idxs = []
    for k in range(turn_start, len(messages)):
        role, _ = extract_message(messages[k])
        if role == "user":
            user_idxs.append(k)
        elif role == "assistant":
            assistant_idxs.append(k)
    return user_idxs, assistant_idxs


def has_tg_channel_tag(messages, indices):
    """Did any user message at the given indices contain a TG channel tag?"""
    for idx in indices:
        _, content = extract_message(messages[idx])
        for block in content_blocks(content):
            if block.get("type") == "text":
                if TG_CHANNEL_TAG in block.get("text", ""):
                    return True
            elif block.get("type") == "tool_result":
                inner = block.get("content", "")
                if isinstance(inner, str) and TG_CHANNEL_TAG in inner:
                    return True
    return False


def reply_tool_uses(content):
    """Return list of (block_idx, tool_use_id) for reply tool uses in content.

    tool_use_id is preserved so the reply-delivery success check can find the
    matching tool_result. None id is tolerated for older transcripts without
    block ids. Replaces the older reply_tool_indices helper which dropped id.
    """
    out = []
    for i, block in enumerate(content_blocks(content)):
        if block.get("type") == "tool_use" and block.get("name") == REPLY_TOOL:
            out.append((i, block.get("id")))
    return out


def first_trailing_text(content, after_idx):
    """Return the first non-empty trailing text block at index > after_idx, or None."""
    blocks = content_blocks(content)
    for i, block in enumerate(blocks):
        if i <= after_idx:
            continue
        if block.get("type") == "text":
            text = block.get("text", "")
            if text and text.strip():
                return text.strip()
    return None


def excerpt(text, limit=200):
    """Short, single-line excerpt for stderr surfacing."""
    if not text:
        return ""
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    return flat[: limit - 3] + "..."


def normalize_for_compare(text):
    """Normalize line endings and strip outer whitespace for leak-check
    comparison. Inner whitespace and markdown stay raw, per the 2026-05-15
    bounce: aggressive normalization (markdown, inner whitespace) would hide
    real mismatches between terminal text and the TG payload."""
    if not isinstance(text, str):
        return ""
    return text.replace("\r\n", "\n").strip()


def collect_tg_tool_texts(messages, turn_assistant_idxs):
    """Gather all `text` params from TG_TEXT_TOOLS tool_use blocks in the turn.

    Returns a list of normalized strings (one per tool call). Used by the
    pre-reply/between-reply leak check. Why both reply + edit: edit_message
    payloads ARE delivered to Telegram, so terminal text mirrored into them
    is still visible to the user and should not trip the leak check.
    """
    out = []
    for a_idx in turn_assistant_idxs:
        _, content = extract_message(messages[a_idx])
        for block in content_blocks(content):
            if block.get("type") != "tool_use":
                continue
            if block.get("name") not in TG_TEXT_TOOLS:
                continue
            tool_input = block.get("input") or {}
            text = tool_input.get("text", "")
            if isinstance(text, str):
                out.append(normalize_for_compare(text))
    return out


def find_leaked_text_block(messages, turn_assistant_idxs, tg_tool_texts):
    """Walk every text block in the turn. Return the first block content that
    is not a substring of any TG-bound tool param, or None if all clean.

    2026-05-15: catches pre-reply narration ("On it...") and between-reply
    commentary, which the original "trailing text after last reply" check
    missed. Design locked via 2026-05-15 GPT-5.5 bounce
    that fixed the design (block, do not auto-relay; suppress duplication).
    """
    for a_idx in turn_assistant_idxs:
        _, content = extract_message(messages[a_idx])
        for block in content_blocks(content):
            if block.get("type") != "text":
                continue
            raw = block.get("text", "")
            norm = normalize_for_compare(raw)
            if not norm:
                # Empty / whitespace-only text blocks are noise, never a leak.
                continue
            if any(norm in payload for payload in tg_tool_texts):
                continue
            return raw
    return None


def utcnow_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_debug_log(turn_id, payload):
    """Best-effort dump of the suspected-trailing-text case for inspection."""
    try:
        ddir = debug_log_dir()
        ddir.mkdir(parents=True, exist_ok=True)
        path = ddir / f"hook-debug-{turn_id}.log"
        with path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, default=str)
        return str(path)
    except OSError:
        return None


# -----------------------------------------------------------------------------
# Stderr message builders. Every exit-2 path goes through one of these so the
# silent-exit-2 ban is enforced by construction. Why distinct directives:
# the agent must be able to tell the 4 conditions apart in the rewake
# system-reminder so the next turn's response is correct. Tonight's loop kept
# repeating "Hook passed." because the empty rewake gave zero discrimination.
# -----------------------------------------------------------------------------


def msg_no_reply():
    return (
        "BLOCKED: Telegram inbound received this turn but no "
        "mcp__plugin_telegram_telegram__reply tool call. Terminal output is "
        "invisible to the user. Next turn: call the reply tool with chat_id from "
        "the inbound <channel> tag, send the response, end the turn with zero "
        "trailing text and no further tool calls."
    )


def msg_trailing_text_known(reply_msg_idx, reply_block_idx, text):
    return (
        f"BLOCKED: trailing terminal text after TG reply detected "
        f"(reply at msg_idx={reply_msg_idx}, block_idx={reply_block_idx}, "
        f"trailing_text={excerpt(text)!r}). Terminal output is invisible to "
        f"the user. Next turn: emit zero text and no tool calls. End turn cleanly. "
        f"If the trailing content matters to the user, fold it into a NEW reply "
        f"tool call instead of writing it to terminal."
    )


def msg_trailing_text_inflight(text):
    return (
        f"BLOCKED: trailing terminal text after TG reply detected via in-flight "
        f"last_assistant_message (text={excerpt(text)!r}). Terminal output is "
        f"invisible to the user. Next turn: emit zero text and no tool calls. End "
        f"turn cleanly."
    )


def msg_invisible_terminal_text(text):
    """Pre-reply / between-reply leak block (2026-05-15 hardening).

    The leaked snippet drives the directive: the model should fold it into
    the next reply or delete the sentence. Block over auto-relay was the
    GPT-5.5 bounce conclusion: relay risks shipping stale narration after
    work completes.
    """
    snippet = excerpt(text, limit=120)
    return (
        f"BLOCKED: invisible terminal text detected in Telegram turn.\n"
        f'Leaked snippet (first 120 chars): "{snippet}"\n'
        f"Move it into the next Telegram reply text parameter, or delete the "
        f"sentence. the user cannot see terminal output."
    )


def msg_trailing_text_suspected(debug_path):
    """Used when we cannot pinpoint the exact trailing text. Still actionable."""
    where = debug_path or "<failed to write debug log>"
    return (
        f"BLOCKED: trailing-text-suspected after TG reply (specific text not "
        f"isolated from transcript). Full assistant turn dumped to {where}. "
        f"Next turn: emit zero text and no tool calls. End turn cleanly. "
        f"Investigate the debug log if the cause is unclear."
    )


def msg_reply_tool_errored(detail):
    """2026-05-15 hardening (GPT-5.5 bounce): TG reply tool returned an
    error tool_result. The hook would otherwise pass because the tool_use
    exists, but nothing actually shipped to the user. Block so the model
    retries or surfaces the failure in a new reply."""
    excerpt_str = excerpt(detail, limit=200)
    return (
        f"BLOCKED: Telegram reply tool was called but returned an error: "
        f"{excerpt_str}. Retry the reply or surface the error in a separate "
        f"reply call. the user cannot see the original failure since it landed "
        f"in tool_result, not in his Telegram thread."
    )


def find_tool_result(messages, indices, tool_use_id):
    """Locate the tool_result block matching `tool_use_id` within the turn.
    Returns the block dict on hit, None if no matching result exists yet
    (the result may not have flushed to transcript when the hook fires).
    """
    if not tool_use_id:
        return None
    for idx in indices:
        if idx >= len(messages):
            continue
        _, content = extract_message(messages[idx])
        for block in content_blocks(content):
            if (
                block.get("type") == "tool_result"
                and block.get("tool_use_id") == tool_use_id
            ):
                return block
    return None


def tool_result_indicates_error(result):
    """Conservative failure heuristic (GPT-5.5 bounce 2026-05-15): false
    negatives tolerable (a missed-error pass), false positives not (blocking
    a successful reply that happens to contain the word 'error'). Three
    trip conditions, in order of confidence:
      1. `is_error: true` at the result block level (Anthropic API contract)
      2. result content is a string starting with "Error:" (common shell/MCP)
      3. result content list contains a block with text starting with "Error:"
    Returns (errored: bool, detail: str). `detail` is the message we surface.
    """
    if not isinstance(result, dict):
        return False, ""
    if result.get("is_error") is True:
        content = result.get("content")
        if isinstance(content, str):
            return True, content
        if isinstance(content, list):
            for blk in content:
                if isinstance(blk, dict) and blk.get("type") == "text":
                    return True, blk.get("text", "")
        return True, "is_error=true (no readable detail)"
    content = result.get("content")
    if isinstance(content, str):
        if content.lstrip().startswith("Error:"):
            return True, content
    elif isinstance(content, list):
        for blk in content:
            if not isinstance(blk, dict):
                continue
            text = blk.get("text", "") if blk.get("type") == "text" else ""
            if text.lstrip().startswith("Error:"):
                return True, text
    return False, ""


# -----------------------------------------------------------------------------
# Core decision function. Returns (exit_code, stderr_message).
# Pure: no I/O side effects beyond the optional debug-log write in the
# suspected-fallback wrapper. Tested in isolation by
# scripts/hooks/test-check-tg-reply-completeness.py.
# -----------------------------------------------------------------------------


def decide(input_data, messages, turn_id):
    """Apply the trailing-text / no-reply rules. Returns (code, stderr_or_empty)."""
    turn_user_idxs, turn_assistant_idxs = find_turn_boundary(messages)
    if not turn_user_idxs:
        return 0, ""

    tg_arrived = has_tg_channel_tag(messages, turn_user_idxs)

    reply_positions = []
    last_reply_msg_idx = None
    last_reply_block_idx = None
    last_reply_tool_use_id = None
    for a_idx in turn_assistant_idxs:
        _, content = extract_message(messages[a_idx])
        for b_idx, use_id in reply_tool_uses(content):
            reply_positions.append((a_idx, b_idx))
            last_reply_msg_idx = a_idx
            last_reply_block_idx = b_idx
            last_reply_tool_use_id = use_id

    if tg_arrived and not reply_positions:
        return 2, msg_no_reply()

    # Reply-delivery success check (2026-05-15 hardening). The TG-inbound rule
    # above only counts the tool_use; if the harness returned an error result,
    # nothing actually shipped. Conservative heuristic: only block when the
    # tool_result explicitly signals failure. See tool_result_indicates_error
    # for the trip conditions. Missing tool_result (None) = PASS because the
    # result may not have landed at hook-fire time.
    if last_reply_tool_use_id:
        result = find_tool_result(
            messages, turn_user_idxs + turn_assistant_idxs, last_reply_tool_use_id
        )
        if result is not None:
            errored, detail = tool_result_indicates_error(result)
            if errored:
                return 2, msg_reply_tool_errored(detail)

    # Stdin-based trailing-text check. Stop hook fires before the final
    # assistant text block is flushed to the JSONL transcript, so we have to
    # cross-reference the in-flight `last_assistant_message` payload.
    last_assistant_message = (input_data.get("last_assistant_message") or "").strip()
    if reply_positions and last_assistant_message:
        already_in_transcript = False
        for a_idx in turn_assistant_idxs:
            _, content = extract_message(messages[a_idx])
            for block in content_blocks(content):
                if (
                    block.get("type") == "text"
                    and block.get("text", "").strip() == last_assistant_message
                ):
                    already_in_transcript = True
                    break
            if already_in_transcript:
                break
        if not already_in_transcript:
            return 2, msg_trailing_text_inflight(last_assistant_message)

    # Persisted-transcript trailing-text check (belt-and-braces).
    if reply_positions:
        for a_idx in turn_assistant_idxs:
            _, content = extract_message(messages[a_idx])
            if a_idx < last_reply_msg_idx:
                continue
            threshold = last_reply_block_idx if a_idx == last_reply_msg_idx else -1
            trailing = first_trailing_text(content, threshold)
            if trailing is not None:
                return 2, msg_trailing_text_known(a_idx, threshold, trailing)

    # Pre-reply / between-reply leak check (2026-05-15 hardening). Only fires
    # in TG-triggered turns: outside that context, terminal text is fine.
    # Compares every text block in the turn against the normalized text param
    # of every reply/edit_message tool call. Substring match (normalized) is
    # the pass condition. Why not equality: the model often wraps the user-
    # visible message in extra reply payload (greeting, signature) that's
    # legitimate; the leak we care about is text that goes NOWHERE near the
    # reply payload. See 2026-05-15 design bounce notes.
    if tg_arrived:
        tg_tool_texts = collect_tg_tool_texts(messages, turn_assistant_idxs)
        leaked = find_leaked_text_block(
            messages, turn_assistant_idxs, tg_tool_texts
        )
        if leaked is not None:
            return 2, msg_invisible_terminal_text(leaked)

    return 0, ""


def decide_with_suspected_fallback(input_data, messages, turn_id):
    """
    Wrapper around decide(). If the caller knows trailing text is present
    (e.g., the model wrote "Hook passed." but the transcript is still in
    flight) but decide() returned PASS because nothing concrete was found,
    they can promote the result to suspected-trailing-text BLOCK by setting
    `force_suspected_trailing_text` on input_data. Defensive surface so we
    NEVER exit 2 silently; even the fallback gets actionable stderr plus a
    debug log so a human can inspect what was missed.
    """
    code, msg = decide(input_data, messages, turn_id)
    if code == 0 and input_data.get("force_suspected_trailing_text"):
        debug_path = write_debug_log(
            turn_id,
            {
                "reason": "force_suspected_trailing_text",
                "input_data_keys": sorted(input_data.keys()),
                "messages_count": len(messages),
            },
        )
        return 2, msg_trailing_text_suspected(debug_path)
    return code, msg


# -----------------------------------------------------------------------------
# Public entry point
# -----------------------------------------------------------------------------


def rewake_counter_path():
    """Counter file path. Env var override enables test isolation and
    alternate install layouts."""
    override = os.environ.get("TG_HOOK_REWAKE_COUNTER_PATH")
    return Path(override) if override else REWAKE_COUNTER_PATH


def loop_event_log_path():
    """Loop-event log path. Env var override enables test isolation."""
    override = os.environ.get("TG_HOOK_LOOP_EVENT_LOG_PATH")
    return Path(override) if override else LOOP_EVENT_LOG_PATH


def debug_log_dir():
    """Debug log directory. Env var override enables test isolation."""
    override = os.environ.get("TG_HOOK_DEBUG_LOG_DIR")
    return Path(override) if override else DEBUG_LOG_DIR


def load_rewake_counter():
    """Load the persisted rewake counter. Returns default shape on miss/parse fail."""
    default = {
        "session_id": "",
        "last_transcript_path": "",
        "consecutive_blocks": 0,
        "first_block_ts": None,
        "last_block_ts": None,
    }
    path = rewake_counter_path()
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return default
        for key, fallback in default.items():
            data.setdefault(key, fallback)
        return data
    except (json.JSONDecodeError, OSError):
        return default


def save_rewake_counter(state):
    """Best-effort write of the counter. Hook never fails on counter I/O."""
    path = rewake_counter_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except OSError:
        pass


def parse_iso_ts(ts):
    """Parse an ISO timestamp. Returns None on miss."""
    if not ts:
        return None
    try:
        # Handle the "Z" suffix python's fromisoformat doesn't accept pre-3.11.
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None


def session_changed(state, input_data):
    """Reset trigger 1: new session_id."""
    current = input_data.get("session_id", "") or ""
    return current and state.get("session_id", "") and current != state["session_id"]


def time_gap_exceeded(state, now):
    """Reset trigger 2: > window seconds since last_block_ts."""
    last = parse_iso_ts(state.get("last_block_ts"))
    if last is None:
        return False
    return (now - last).total_seconds() > REWAKE_LOOP_WINDOW_SECONDS


def append_known_issue(now_iso, consecutive):
    """Append a JSONL event so loop incidents leave a durable trail."""
    record = {
        "ts": now_iso,
        "kind": "stop_hook_rewake_loop_break",
        "consecutive_blocks": consecutive,
        "window_seconds": REWAKE_LOOP_WINDOW_SECONDS,
        "outcome": "force_released_to_exit_0",
    }
    path = loop_event_log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except OSError:
        pass


def main():
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError, ValueError):
        sys.exit(0)

    transcript_path = input_data.get("transcript_path", "")
    turn_id = input_data.get("turn_id") or utcnow_iso().replace(":", "").replace(
        "-", ""
    )

    messages = load_transcript(transcript_path)
    if not messages:
        # No transcript = no evidence to block on. PASS.
        sys.exit(0)

    code, message = decide_with_suspected_fallback(input_data, messages, turn_id)

    # Layer 2 (2026-05-13 loop incident): rewake-counter loop break.
    # Counter is updated AFTER decide() so a clean pass resets state.
    state = load_rewake_counter()
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat().replace("+00:00", "Z")

    if session_changed(state, input_data) or time_gap_exceeded(state, now):
        # Reset triggers 1 (new session) or 2 (time gap).
        state["consecutive_blocks"] = 0
        state["first_block_ts"] = None
        state["last_block_ts"] = None

    state["session_id"] = input_data.get("session_id", "") or state.get(
        "session_id", ""
    )
    state["last_transcript_path"] = transcript_path or state.get(
        "last_transcript_path", ""
    )

    if code == 2:
        state["consecutive_blocks"] = int(state.get("consecutive_blocks", 0)) + 1
        if state.get("first_block_ts") is None:
            state["first_block_ts"] = now_iso
        state["last_block_ts"] = now_iso

        if state["consecutive_blocks"] > REWAKE_LOOP_THRESHOLD:
            # 4th block in window: release the loop. Write loud event, reset
            # state, exit 0. The model needs space to stop cleanly; the loop
            # was preventing convergence even with actionable stderr.
            append_known_issue(now_iso, state["consecutive_blocks"])
            state["consecutive_blocks"] = 0
            state["first_block_ts"] = None
            state["last_block_ts"] = None
            save_rewake_counter(state)
            sys.exit(0)

        save_rewake_counter(state)

        # Silent-exit-2 ban (Layer 1): if we somehow have an empty message on
        # a block path, fabricate a suspected-trailing-text directive + debug
        # log rather than exit 2 with no stderr. Should be unreachable by
        # construction; this is the belt-and-braces guard.
        if not message:
            message = msg_trailing_text_suspected(
                write_debug_log(
                    turn_id,
                    {
                        "reason": "empty_message_guard",
                        "code": code,
                        "input_data_keys": sorted(input_data.keys()),
                    },
                )
            )
        print(message, file=sys.stderr)
        sys.exit(2)

    # Reset trigger 3: clean pass.
    state["consecutive_blocks"] = 0
    state["first_block_ts"] = None
    state["last_block_ts"] = None
    save_rewake_counter(state)
    sys.exit(0)


if __name__ == "__main__":
    main()
