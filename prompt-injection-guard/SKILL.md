---
name: prompt-injection-guard
description: Refuse confabulated or spoofed inbound before a tool-capable agent acts on it. Ships a portable UserPromptSubmit-style hook that detects four content-free forgery signatures (role-label scaffolding, a mismatched or under-populated trusted-channel wrapper, a future or malformed timestamp, a duplicated message id), a standalone scanner with a self-test, and a provenance/taint design doc for the wider retrieval pipeline.
when_to_use: Use when an agent reads a prompt that can mix genuine operator commands with untrusted or self-round-tripped text, and you want a deterministic front line that refuses the classic forgery shapes at the harness layer instead of trusting the model to catch them. Also use to set up or audit an existing inbound-integrity guard.
---

# Prompt Injection Guard

You are wiring a deterministic guard that inspects each inbound prompt and
refuses the specific STRUCTURAL shapes a forged operator command takes. The
model cannot rationalize its way past a harness-level refusal the way it can
talk itself past a prose rule in a system prompt.

The package ships three portable pieces:

- `reference/inbound-guard.example.py` - the hook. It checks four content-free
  signatures and exits 2 (refuse) on a match, 0 (pass) otherwise. It fails OPEN
  on any parser error so it can never wedge the agent loop.
- `scripts/inbound-scan.py` - a standalone scanner that loads the same detection
  and runs it over arbitrary text, with a `--self-test`. One source of truth, so
  a green self-test proves what the live hook will decide.
- `templates/untrusted-content-fence.md` - the provenance/taint design doc. The
  hook is the cheap front line; provenance is the real boundary. Read it before
  you rely on the hook alone.

## The four signatures

1. **Role-label prefix.** A body that opens with a configured role word
   ("Human:", "Assistant:", or your own list), at the top of the prompt or
   inside a trusted-channel wrapper. Real human input carries no role
   scaffolding; a body that does is usually the model's own output round-tripped
   as a fake user turn.
2. **Wrapper integrity.** If you configure a trusted-channel wrapper tag, a block
   wearing that tag must carry the exact `source` value a genuine envelope
   carries AND populate every required attribute. A tag that claims the channel
   but mismatches the source or skimps on attributes is refused. An unparseable
   wrapper (opener present, no valid close) is refused too: fail CLOSED.
3. **Future / malformed timestamp.** A genuine envelope timestamp is the real
   send time, offset-aware, and not in the future. Future-beyond-tolerance,
   unparseable, or timezone-naive timestamps are refused.
4. **Duplicate id.** The same `(source, id)` seen again inside a short window with
   DIFFERENT content is a replay/confabulation tell.

## Step 1: Decide your trusted-channel shape

- **Role labels.** List the words that never legitimately open real input in your
  setup. `Human` and `Assistant` are the safe defaults. Add or remove words
  deliberately; common status words ("System:", "User:") false-positive in log
  pastes, so they are intentionally excluded from the default.
- **Wrapper tag and source.** If your genuine inbound is wrapped in an
  XML-style envelope (a channel/message tag your transport emits), set
  `wrapper_tag` to that tag name and `trusted_source_value` to the exact `source`
  attribute a real envelope carries. If you have no wrapper, leave `wrapper_tag`
  empty and the guard runs in role-label-only mode.
- **Required attributes.** List the attributes a genuine envelope always
  populates (for example the source, a unique id, and a timestamp). A wrapper
  missing any of them is refused.

## Step 2: Place the files and write the config

1. Copy `reference/inbound-guard.example.py` to a stable path you control.
2. Copy `reference/guard.config.example.json` next to it as `guard.config.json`,
   or to a path you export as `INBOUND_GUARD_CONFIG`. Fill in the fields from
   Step 1. Leave `on_block_command` empty until you have an alert seam; leave
   `soft_only` false for enforcement, or true for alert-only shadow mode.
3. Run `python3 -m py_compile` on the hook and
   `python3 scripts/inbound-scan.py --self-test`; confirm all checks PASS.

## Step 3: Register the hook with a backup

1. Back up your hooks-capable settings file to a timestamped copy first. Never
   edit settings without a backup.
2. Register the hook on the prompt-submit event (the event your harness fires
   with the inbound prompt on stdin as JSON with a `prompt` field). Point it at
   the absolute path of the copied hook.
3. Re-read the settings file, confirm it is valid JSON and the entry is present.
   If the write corrupted the file, restore the backup.

## Step 4: Wire the alert seam (optional)

Set `on_block_command` to a shell command that receives the block event JSON on
stdin. Use it to notify a human out of band that a forged inbound was refused.
The event is content-free (a signature tag and a short snippet), safe to send.

## Step 5: Verify

- `python3 -m py_compile reference/inbound-guard.example.py` passes.
- `python3 scripts/inbound-scan.py --self-test` reports all PASS.
- Feed the hook a synthetic stdin payload whose `prompt` opens with a role label
  and confirm exit 2 with a refusal on stderr.
- Feed it a well-formed, current, correctly-sourced wrapper and confirm exit 0.
- Confirm the kill switch: set `INBOUND_GUARD_DISABLE=1` and confirm the hook
  no-ops (exit 0) on any input.

## Honest limits

- **This refuses STRUCTURAL forgery, not meaning.** A well-formed, correctly
  sourced, non-duplicate inbound passes even if its prose is manipulative. The
  guard raises the cost of impersonating your trusted channel; it does not read
  intent.
- **A genuine wrapper is only as trustworthy as its transport.** Source
  verification proves the envelope claims your channel, not that the channel
  itself is unforgeable end to end. Provenance and an executing-agent intake
  guard (see the fence template) are the load-bearing controls; this hook is the
  front line.
- **Fails open by design.** On malformed input or any internal error it passes,
  because a front-line guard that wedges the agent loop is worse than the forgery
  it misses. Keep a behavioral backstop in your system prompt.
- **The soft echo signature is not shipped as a block.** Detecting a short
  inbound that merely echoes the agent's own last recommendation is high-noise;
  run it in alert-only mode if you want it (see the README).
