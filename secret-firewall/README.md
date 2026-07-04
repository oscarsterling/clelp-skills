# Secret Firewall

Block secret and credential VALUES from leaving a tool-capable agent, and redact
the ones that surface in tool output.

An agent that can send messages, spawn sub-agents, write files, and call external
APIs can also leak a credential through any of those paths. This package scans
the OUTBOUND payload of an egress-capable tool call before it fires, and scrubs
secrets out of tool OUTPUT before the model reads them (where they could be
paraphrased back out).

## What it does

1. **Detect (pure engine).** `scripts/secret-scan.py` scans text with three
   layers: provider PATTERNS, a salted-hash INDEX of your own secrets, and an
   opt-in ENTROPY layer. It never emits a secret; a finding carries only a
   detector, label, offset, length, and a non-reversible fingerprint.
2. **Gate outbound (PreToolUse).** `reference/egress-gate.example.py` pulls every
   string out of a tool's input, scans it, and in SHADOW mode logs and allows, or
   in ENFORCE mode denies a high-confidence leak with a static, secret-free
   message.
3. **Redact output (PostToolUse).** `reference/redact-output.example.py` replaces
   secret patterns in tool output with type-labeled sentinels.

## The salted-hash index

The pattern layer knows common provider shapes (`sk-ant-`, `ghp_`, `AIza...`,
`AKIA...`, private-key headers, JWTs, and more). The index catches YOUR specific
secrets in ANY format, including ones no pattern knows.

You keep a local, never-committed plaintext list of the exact values that must
never egress, build a salted-hash index from it once, and the gate matches
against the index in-process. The match is mathematically identical to a
substring search for each value, but the stored index holds only salted SHA-256
hashes plus a character set and 2-char prefixes that bound the scan window. No
full secret is ever stored. This is cross-platform: your secret values come from
a file you control, not from any OS-specific store. Rebuild the index whenever
you rotate or add a secret.

## What ships here

| File | What it is |
|------|-----------|
| `scripts/secret-scan.py` | Pure detection engine + CLI: `scan-text`, `build-index`, `selftest`. Pattern, salted-hash index, entropy layers. Never emits a secret. |
| `reference/egress-gate.example.py` | The PreToolUse gate. Shadow/enforce modes, fail-open, protected-surface loop breaker, kill switch. |
| `reference/redact-output.example.py` | The PostToolUse output redactor (pattern-only, cheap). |
| `reference/gate.config.example.json` | Example config: scanned tools, index path, shadow log, protected surface. |
| `reference/secret-values.example.txt` | Format for the plaintext values file that feeds the index (never commit the real one). |
| `SKILL.md` | Step-by-step setup for an agent to wire it in. |

## Modes

- **Shadow (default).** Findings are appended to the shadow log and the call is
  allowed. Use it to size your real leak rate and catch false positives before
  blocking. Every log entry is secret-free.
- **Enforce (`SECRET_FIREWALL_ENFORCE=1`).** A HIGH-confidence finding (pattern or
  index, never entropy alone) denies the call with a static stderr line. Turn it
  on only after shadow telemetry looks clean.

The gate FAILS OPEN by construction: the kill switch, malformed input, an
unhandled tool, a missing engine, or any exception allows the call. The only
deny path is a positive high-confidence finding under enforce.

## Protected-surface loop breaker

One outbound surface can be your only line to a human (a chat reply). To keep an
enforce-mode false positive from silencing that channel forever, a deny on the
tool named in `protected_surface` fires once per window; a repeat within the
window fails open. Every other surface has no breaker, because a persistent deny
there is the correct outcome: remove the secret.

## Quickstart

1. Build the index from your own secret-values file:
   `python3 scripts/secret-scan.py build-index --values <file> --out <index>`
   (or skip it and run pattern-only).
2. Point `gate.config.json` at the index and your scanned tools.
3. Register the gate on PreToolUse and the redactor on PostToolUse.
4. Run in shadow, read the log, then set `SECRET_FIREWALL_ENFORCE=1`.

Verify with `python3 scripts/secret-scan.py selftest`.

## Kill switch

Set `SECRET_FIREWALL_DISABLE=1` to allow everything (full no-op gate). Unset it
to re-enable.

## Honest limits

- **Literals, not interpolation.** A literal secret string is caught; a secret
  assembled at runtime is not in the payload text and is out of scope. Reference
  secrets at runtime, never inline them.
- **Fails open.** Any error allows the call; the guarded tools are too core to
  risk a false block. The block path is a positive high-confidence finding under
  enforce.
- **The index is only as complete as your values file.** Rebuild on every
  rotation. The redactor is pattern-only and cheap; the gate is the stronger
  control.
- **Defense in depth, not a guarantee.** This raises the cost of a leak behind
  the discipline of never writing secret literals.

## License

MIT. See [LICENSE](./LICENSE).
