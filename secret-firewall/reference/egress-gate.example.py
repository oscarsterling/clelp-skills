#!/usr/bin/env python3
"""egress-gate.example.py - PreToolUse gate that scans an outbound tool payload
for secret/credential values before the call fires.

WHAT IT GUARDS
  Any tool call whose payload leaves the trust boundary: a message send, a
  sub-agent prompt, a file write that may be committed, an external API call.
  Before such a call runs, this gate recursively pulls every string value out of
  the tool input, scans the joined text with the detection engine
  (scripts/secret-scan.py), and decides whether to allow or deny.

TWO MODES
  * SHADOW (default): findings are appended to a JSONL telemetry log and the call
    is ALLOWED. This sizes your real leak rate before you turn on blocking. No
    secret value is ever logged, only detector/label/offset/length/fingerprint.
  * ENFORCE (SECRET_FIREWALL_ENFORCE=1): a HIGH-confidence finding (a known
    pattern or an index exact-match, never entropy alone) DENIES the call with a
    static, secret-free stderr line. Turn enforce on only after shadow telemetry
    shows the false-positive rate is acceptable.

FAIL-OPEN BY CONSTRUCTION
  The guarded tools are core to real work, so a bug here that blocked a
  legitimate call would be worse than the leak it guards. The kill switch,
  malformed stdin, an unhandled tool, a missing engine, or ANY exception -> exit
  0 (allow). The only exit-2 path is a positive HIGH-confidence finding while
  ENFORCE is on.

PROTECTED-SURFACE LOOP BREAKER
  One outbound surface can be your only line to a human (a chat reply). An
  enforce-mode false positive that denied every attempt on that surface would
  silence you entirely. For the tool named in `protected_surface`, a deny is
  allowed to fire ONCE per window; a repeat within the window FAILS OPEN so the
  channel can never be permanently wedged. Every other surface has no breaker: a
  persistent deny there is correct (remove the secret).

stderr is static and contains NO secret bytes and NO channel/role markup (hook
  stderr can re-enter the model's context).
KILL SWITCH: SECRET_FIREWALL_DISABLE=1 -> allow everything.
"""

import importlib.util
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE_PATH = os.path.join(HERE, "..", "scripts", "secret-scan.py")

DEFAULTS = {
    # Tool names whose outbound payload is scanned. Map name -> run_index
    # (whether the slower salted-hash index layer runs for that tool). The fast
    # pattern layer always runs; reserve the index for true external sends.
    "egress_tools": {
        "reply": True,
        "Task": True,
        "Agent": True,
        "Write": False,
        "Edit": False,
        "Bash": False,
        "WebFetch": True,
    },
    "index_path": "./.secret-firewall/index.json",
    "shadow_log": "./.secret-firewall/shadow.jsonl",
    "protected_surface": "reply",
    "breaker_window_s": 120,
    "breaker_counter_path": "./.secret-firewall/breaker.json",
    "use_entropy": False,
    "extract_budget": 4000,
}

DENY_MSG = (
    "HOOK_BLOCKED_SECRET_EGRESS: this tool call's payload contains what looks "
    "like a secret or credential value leaving via an egress path. Remove the "
    "literal secret (reference it from your secret store at runtime instead) and "
    "retry. If this is a verified false positive on non-secret data, the operator "
    "can disable the gate with the SECRET_FIREWALL_DISABLE kill switch."
)


def load_config():
    path = os.environ.get("SECRET_FIREWALL_CONFIG")
    if not path:
        path = os.path.join(HERE, "gate.config.json")
    cfg = json.loads(json.dumps(DEFAULTS))  # deep copy
    try:
        with open(path, encoding="utf-8") as f:
            user = json.load(f)
        if isinstance(user, dict):
            cfg.update({k: v for k, v in user.items() if v is not None})
    except Exception:
        pass
    return cfg


def load_engine():
    spec = importlib.util.spec_from_file_location("secret_scan", ENGINE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def extract_strings(obj, out, budget):
    """Recursively collect every string VALUE in a tool_input structure. Keys are
    skipped (field names, not payload)."""
    if len(out) >= budget:
        return
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            extract_strings(v, out, budget)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            extract_strings(v, out, budget)


def shadow_write(path, entry):
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def breaker_release(path, window_s, session_id):
    """For the protected surface, return True if this deny should FAIL OPEN
    (already denied once in-window for this session). Records the deny otherwise.
    Any I/O failure degrades to 'no breaker' (the deny stands), still safe."""
    now = time.time()
    try:
        with open(path, encoding="utf-8") as f:
            st = json.load(f)
        st = st if isinstance(st, dict) else {}
    except Exception:
        st = {}
    same = st.get("session_id") == session_id
    recent = same and (now - float(st.get("last_ts", 0) or 0)) <= window_s
    prev = int(st.get("count", 0) or 0) if recent else 0
    new_state = {"session_id": session_id, "last_ts": now}
    if recent and prev >= 1:
        new_state["count"] = 0
        _write_json(path, new_state)
        return True
    new_state["count"] = prev + 1
    _write_json(path, new_state)
    return False


def _write_json(path, obj):
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f)
    except Exception:
        pass


def main():
    if os.environ.get("SECRET_FIREWALL_DISABLE"):
        return 0
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0

    cfg = load_config()
    tool_name = data.get("tool_name")
    egress_tools = cfg["egress_tools"]
    if tool_name not in egress_tools:
        return 0

    strings = []
    tool_input = data.get("tool_input")
    if isinstance(tool_input, (dict, list)):
        extract_strings(tool_input, strings, cfg["extract_budget"])
    payload = "\n".join(strings)
    if not payload.strip():
        return 0

    use_index = bool(egress_tools[tool_name])
    use_entropy = bool(os.environ.get("SECRET_FIREWALL_ENTROPY") or cfg.get("use_entropy"))
    try:
        engine = load_engine()
        idx = engine.load_index(cfg["index_path"]) if use_index else None
        result = engine.scan(payload, index=idx, use_entropy=use_entropy)
        findings = result.get("findings", [])
        high = engine.has_high_confidence(result)
    except Exception:
        return 0
    if not findings:
        return 0

    summary = [
        {k: f.get(k) for k in ("detector", "label", "offset", "length", "fingerprint", "confidence")}
        for f in findings
    ]
    enforce = bool(os.environ.get("SECRET_FIREWALL_ENFORCE"))
    session_id = data.get("session_id", "") or ""

    do_deny = enforce and high
    breaker_released = False
    if do_deny and tool_name == cfg["protected_surface"]:
        if breaker_release(cfg["breaker_counter_path"], cfg["breaker_window_s"], session_id):
            do_deny = False
            breaker_released = True

    action = "deny" if do_deny else ("allow_breaker_released" if breaker_released else "allow")
    shadow_write(cfg["shadow_log"], {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "session_id": session_id,
        "tool_name": tool_name,
        "payload_len": len(payload),
        "high_confidence": high,
        "index_degraded": result.get("index_degraded", False),
        "mode": "enforce" if enforce else "shadow",
        "action": action,
        "findings": summary,
    })

    if do_deny:
        print(DENY_MSG, file=sys.stderr)
        return 2
    return 0


def guarded_main():
    try:
        return main()
    except SystemExit:
        raise
    except Exception:
        return 0


if __name__ == "__main__":
    sys.exit(guarded_main())
