#!/usr/bin/env python3
"""model-sentinel.example.py - watch the session model per turn and fire when it
silently falls back to a cheaper model.

THE PROBLEM
  Under capacity pressure a harness can quietly serve your turns on a cheaper
  fallback model. The work keeps flowing, so the downgrade is invisible until
  degraded output has already become the foundation for later work. This hook
  makes the switch VISIBLE and drives a safe recovery.

WHAT IT DOES (per turn, observational only)
  Registered as a hook (see SKILL.md; Stop is the recommended event, PreToolUse
  works for earlier detection), it runs each turn and:
    1. Reads the latest main-chain assistant model from the transcript tail.
    2. Compares it to your INTENDED model marker from config. If the model still
       carries the marker, nothing has changed and the hook is a no-op.
    3. On a fallback (model no longer carries the marker), it does a BUSY CHECK
       FIRST. Ring-and-ask is the safe default: while work may be in flight it
       only records an alert and waits. It never interrupts live work to switch.
    4. Only when a busy probe explicitly reports IDLE does it emit a
       switch-back-ready event. Even then the event defaults to requiring a
       human ack, because busy heuristics are imperfect.
    5. It tracks the SPAN of turns served by the fallback (first seen, last seen,
       turn count), so the restored session can re-review exactly that span.
    6. When the model returns to the intended marker after a fallback span, it
       emits a "restored" event carrying that span and resets its state.

  The hook NEVER blocks a tool and NEVER switches the model itself. It observes
  and it emits events to a handoff file. The actual switch-back, context clear,
  and restore are harness-specific and belong to YOUR automation, wired at the
  documented seam (see the config `on_event_command` and README). This file is
  the generic, portable half; the plumbing is yours.

CONFIG
  A JSON config is read from the path in MODEL_SENTINEL_CONFIG, or from
  `sentinel.config.json` next to this script. Fields (all optional except the
  marker):
    intended_model_marker  (required) substring identifying your intended model
    handoff_path           JSONL file the hook appends events to
    state_path             JSON file the hook uses to track the fallback span
    busy_probe_command     shell command; exit 0 means BUSY, non-zero means idle.
                           If absent or it errors, busy state is UNKNOWN and the
                           hook stays in alert-and-wait (the safe default).
    on_event_command       shell command run once per emitted event, with the
                           event JSON on stdin. This is YOUR automation seam.
    auto_switch            if true, switch-back-ready events set requires_ack
                           false. Default false (ring-and-ask).

KILL SWITCH: MODEL_SENTINEL_DISABLE in {1,true,yes,on} -> full no-op.

FAIL-OPEN POLICY: this is an OBSERVER, not a gate. Malformed stdin, missing
  config, unreadable transcript, or ANY exception -> exit 0 and do nothing. It
  must never block or brick a session.

Exit codes: always 0. This hook does not gate tools.
Token cost: 0. No LLM. Local file IO, transcript tail-read only.
"""

import json
import os
import subprocess
import sys
import time

# --- config loading --------------------------------------------------------

_TRUTHY = {"1", "true", "yes", "on"}
_BLOCK_SIZE = 64 * 1024
DEFAULT_TAIL_BYTES = 1 * 1024 * 1024


def _truthy(value):
    return str(value).strip().lower() in _TRUTHY if value is not None else False


def load_config():
    """Read the JSON config from MODEL_SENTINEL_CONFIG or the sibling default.
    Returns {} if none is found or it is unreadable; the caller no-ops on a
    missing marker."""
    path = os.environ.get("MODEL_SENTINEL_CONFIG")
    if not path:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sentinel.config.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg if isinstance(cfg, dict) else {}
    except Exception:
        return {}


# --- transcript tail reader (portable, backward, capped) -------------------


def iter_lines_reversed(path, block_size=_BLOCK_SIZE, max_bytes=DEFAULT_TAIL_BYTES):
    """Yield complete text lines from the end of `path` backward, reading at most
    `max_bytes`. A line straddling a block boundary is carried and prepended to
    the previous block. Keeps the hot path off a forward multi-MB read."""
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


def read_latest_turn(transcript_path):
    """Return (model, turn_id) for the latest non-sidechain assistant turn, or
    (None, None). turn_id is a stable identifier for the turn (message uuid or,
    failing that, a timestamp) used to count fallback turns without
    double-counting a single turn across multiple hook fires."""
    try:
        if not transcript_path or not os.path.isfile(transcript_path):
            return None, None
        for line in iter_lines_reversed(transcript_path):
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
                turn_id = obj.get("uuid") or message.get("id") or obj.get("timestamp") or str(time.time())
                return str(model), str(turn_id)
    except Exception:
        return None, None
    return None, None


# --- state (fallback span tracking) ----------------------------------------


def load_state(path):
    if not path:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            state = json.load(f)
        return state if isinstance(state, dict) else {}
    except Exception:
        return {}


def save_state(path, state):
    if not path:
        return
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f)
        os.replace(tmp, path)
    except Exception:
        pass


# --- busy probe ------------------------------------------------------------


def is_busy(cfg):
    """Return True (busy), False (idle), or None (unknown).

    The probe is a user command: exit 0 means work is in flight, non-zero means
    idle. Absent or erroring probe -> None (unknown). The caller treats anything
    that is not a confirmed idle as a reason to hold at alert-and-wait, so an
    unknown result is safe by construction."""
    cmd = cfg.get("busy_probe_command")
    if not cmd:
        return None
    try:
        result = subprocess.run(cmd, shell=True, timeout=10)
        return result.returncode == 0
    except Exception:
        return None


# --- event emission --------------------------------------------------------


def emit_event(cfg, event):
    """Append the event to the handoff JSONL file and hand it to the user's
    automation seam on stdin. Both are best effort; a failure here never
    propagates (this is an observer)."""
    handoff = cfg.get("handoff_path")
    if handoff:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(handoff)), exist_ok=True)
            with open(handoff, "a", encoding="utf-8") as f:
                f.write(json.dumps(event) + "\n")
        except Exception:
            pass
    seam = cfg.get("on_event_command")
    if seam:
        try:
            subprocess.run(seam, shell=True, input=json.dumps(event).encode("utf-8"), timeout=30)
        except Exception:
            pass


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# --- main ------------------------------------------------------------------


def main():
    if _truthy(os.environ.get("MODEL_SENTINEL_DISABLE")):
        return

    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    if not isinstance(data, dict):
        return

    # Sidechain (sub-agent) turns never define the main-session model.
    if "agent_id" in data:
        return

    cfg = load_config()
    marker = cfg.get("intended_model_marker")
    if not marker:
        return  # nothing configured to compare against; no-op

    model, turn_id = read_latest_turn(data.get("transcript_path"))
    if model is None:
        return

    on_intended = marker.lower() in model.lower()
    state = load_state(cfg.get("state_path"))
    fallback_active = bool(state.get("fallback_active"))

    if on_intended:
        # Back on the intended model. If a fallback span was open, announce the
        # restore and hand the span to the restored session for re-review.
        if fallback_active:
            emit_event(cfg, {
                "event": "model_restored_intended",
                "ts": now_iso(),
                "intended_marker": marker,
                "observed_model": model,
                "fallback_span": {
                    "first_seen": state.get("span_first_seen"),
                    "last_seen": state.get("span_last_seen"),
                    "fallback_model": state.get("fallback_model"),
                    "turn_count": int(state.get("span_turn_count", 0)),
                },
                "action": "re_review_span",
                "note": "Re-review the turns in fallback_span for quality before "
                        "building further. They may have been served by a degraded model.",
            })
            save_state(cfg.get("state_path"), {"intended_marker": marker})
        return

    # On a fallback model. Update the span, counting each distinct turn once.
    ts = now_iso()
    if not fallback_active:
        state = {
            "intended_marker": marker,
            "fallback_active": True,
            "fallback_model": model,
            "span_first_seen": ts,
            "span_last_seen": ts,
            "span_turn_count": 1,
            "last_turn_id": turn_id,
        }
    else:
        state["fallback_model"] = model
        state["span_last_seen"] = ts
        if turn_id and turn_id != state.get("last_turn_id"):
            state["span_turn_count"] = int(state.get("span_turn_count", 0)) + 1
            state["last_turn_id"] = turn_id
    save_state(cfg.get("state_path"), state)

    busy = is_busy(cfg)
    confirmed_idle = busy is False  # only an explicit non-zero probe counts as idle

    if confirmed_idle:
        requires_ack = not _truthy(cfg.get("auto_switch"))
        emit_event(cfg, {
            "event": "model_fallback_detected",
            "ts": ts,
            "intended_marker": marker,
            "observed_model": model,
            "busy": False,
            "action": "switch_back_ready",
            "requires_ack": requires_ack,
            "fallback_span": {
                "first_seen": state.get("span_first_seen"),
                "last_seen": state.get("span_last_seen"),
                "turn_count": int(state.get("span_turn_count", 0)),
            },
            "note": "Session idle and on a fallback model. Safe to switch back, "
                    "save a scrubbed brief, clear, and restore. Ring the human "
                    "unless auto_switch is set.",
        })
    else:
        # Busy or unknown: ring and wait. Do not switch under live work.
        emit_event(cfg, {
            "event": "model_fallback_detected",
            "ts": ts,
            "intended_marker": marker,
            "observed_model": model,
            "busy": True if busy else None,
            "action": "alert_and_wait",
            "requires_ack": True,
            "fallback_span": {
                "first_seen": state.get("span_first_seen"),
                "last_seen": state.get("span_last_seen"),
                "turn_count": int(state.get("span_turn_count", 0)),
            },
            "note": "On a fallback model but work may be in flight. Alerting only; "
                    "will not switch until idle is confirmed.",
        })


def guarded_main():
    # An observer must never crash the harness with a hook error. Any internal
    # failure exits 0 (do nothing). It also never blocks: there is no exit 2 path.
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        pass
    sys.exit(0)


if __name__ == "__main__":
    guarded_main()
