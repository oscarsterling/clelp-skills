---
name: secret-firewall
description: Block secret and credential VALUES from leaving a tool-capable agent, and redact ones that surface in tool output. Ships a portable PreToolUse egress gate (shadow and enforce modes, fail-open, protected-surface loop breaker), a pure detection engine with pattern, salted-hash index, and entropy layers, and a PostToolUse output redactor. The index detects your own curated secrets in any format while storing only salted hashes, never plaintext.
when_to_use: Use when a tool-capable agent could emit a literal secret through a message, a sub-agent prompt, a file write, or an external call, and you want a deterministic gate that scans outbound payloads and can block a high-confidence leak. Also use to set up or audit an existing credential-egress gate.
---

# Secret Firewall

You are wiring an outbound gate that scans the payload of an egress-capable tool
call for secret/credential values before it fires, plus a redactor that scrubs
secrets out of tool OUTPUT before the model reads them.

The package ships three portable pieces:

- `scripts/secret-scan.py` - the pure detection engine and CLI. Three layers:
  provider PATTERNS (no config), a salted-hash INDEX of your own secrets, and an
  opt-in ENTROPY layer. It never emits a secret value; findings carry only a
  detector, label, offset, length, and a non-reversible fingerprint.
- `reference/egress-gate.example.py` - the PreToolUse gate. Shadow by default
  (log and allow), enforce on an explicit flag (deny a high-confidence leak).
  Fails open on any error.
- `reference/redact-output.example.py` - the PostToolUse redactor. Replaces
  secret patterns in tool output with type-labeled sentinels.

## The salted-hash index (the non-obvious half)

The pattern layer catches known provider shapes. The index catches YOUR specific
secrets even in a format no pattern knows. You keep a local, never-committed
plaintext list of the exact values that must never egress
(`secret-values.example.txt` shows the format), build a salted-hash index from
it ONCE, and the running gate matches against the index. The index holds only
salted SHA-256 hashes plus a character set and 2-char prefixes that bound the
scan window; it never holds a full secret. Rebuild the index whenever you rotate
or add a secret.

## Step 1: Build the index

1. Create your secret-values file from `reference/secret-values.example.txt`.
   Populate it in a build step from your own vault or secret manager. Keep it
   0600, add its path to your ignore rules, and never commit it.
2. Build the index:
   `python3 scripts/secret-scan.py build-index --values <your-values-file> --out <index-path>`
   The output is written 0600 and contains hashes only. Confirm with a grep that
   no plaintext secret appears in it.
3. If you have no curated list yet, skip the index. The pattern layer alone is a
   useful gate; add the index later for unknown-format coverage.

## Step 2: Place the files and write the config

1. Copy the engine and the two hooks to stable paths you control.
2. Copy `reference/gate.config.example.json` to `gate.config.json` (or a path you
   export as `SECRET_FIREWALL_CONFIG`). Set:
   - `egress_tools`: map each tool name whose payload you want scanned to whether
     the (slower) index layer runs for it. Run the index on true external sends;
     leave it off for high-frequency local tools (pattern layer still runs).
   - `index_path`: where you wrote the index in Step 1.
   - `shadow_log`: the JSONL telemetry path.
   - `protected_surface`: the tool name of your only human-facing channel (the
     loop breaker protects it). Set to a name no tool uses to disable it.
3. Run `python3 -m py_compile` on all three files and
   `python3 scripts/secret-scan.py selftest`; confirm PASS.

## Step 3: Register the hooks with a backup

1. Back up your hooks-capable settings file to a timestamped copy first.
2. Register `egress-gate.example.py` on the PreToolUse event and
   `redact-output.example.py` on the PostToolUse event, at their absolute paths.
3. Re-read the settings file and confirm it is valid JSON and the entries are
   present. If the write corrupted the file, restore the backup.

## Step 4: Run in shadow, then enforce

1. Leave enforce OFF. Work normally for a while and read the shadow log. Every
   entry is secret-free. This sizes your real leak rate and surfaces false
   positives before any blocking.
2. When the false-positive rate is acceptable, set `SECRET_FIREWALL_ENFORCE=1` in
   the agent's environment. Now a HIGH-confidence finding (pattern or index, not
   entropy) denies the call.
3. The entropy layer stays off by default; it is noisy. Enable it per call with
   `SECRET_FIREWALL_ENTROPY=1` only for a deliberate audit.

## Step 5: Verify

- `python3 scripts/secret-scan.py selftest` reports PASS.
- Feed the gate a synthetic Write payload containing a planted pattern secret
  with `SECRET_FIREWALL_ENFORCE=1` and confirm exit 2 with the static deny line.
- Feed it the same payload without enforce and confirm exit 0 plus a secret-free
  shadow-log entry.
- Feed the redactor a tool_response whose stdout contains a planted token and
  confirm the output carries a sentinel, not the token.
- Confirm the kill switch: `SECRET_FIREWALL_DISABLE=1` allows everything.

## Honest limits

- **Detects literals, not interpolation.** A secret pasted as a literal string is
  caught. A secret assembled at runtime (read from your store inside the same
  command) is not in the payload text and is out of scope by construction. That
  is the correct pattern anyway: reference secrets at runtime, never inline them.
- **Fails open on purpose.** The guarded tools are core to real work, so any
  error, missing engine, or malformed input allows the call. The only block path
  is a positive high-confidence finding under enforce.
- **The index is only as complete as your values file.** A secret you never added
  is invisible to the index layer (the pattern layer may still catch a known
  shape). Rebuild the index on every rotation.
- **The redactor is pattern-only.** It runs on every tool result, so it stays
  cheap and does not use the index. A secret in an unknown format can still reach
  the model via output; the egress gate is the stronger control.
- **This raises the cost of a leak; it is not a guarantee.** Treat it as
  defense in depth behind the discipline of never writing secret literals.
