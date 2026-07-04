# Prompt Injection Guard

Refuse confabulated or spoofed inbound before a tool-capable agent acts on it.

A tool-capable agent reads a prompt every turn. Some of that prompt is a genuine
operator command; some of it can be text the model round-tripped from its own
earlier output, or content an attacker shaped to look like a trusted channel
envelope. One forged turn can drive a real action. This package refuses the
classic forgery SHAPES at the harness layer, where the model cannot rationalize
past them.

## What it does

A hook inspects each inbound prompt and checks four content-free signatures:

1. **Role-label prefix.** Real input does not open with `Human:`, `Assistant:`,
   or any role word you configure. A body that does, at the prompt start or
   inside a trusted-channel wrapper, is the classic self-round-trip tell.
2. **Wrapper integrity.** A block wearing your trusted-channel tag must carry the
   exact `source` a genuine envelope carries and populate every required
   attribute. Mismatched, under-populated, or unparseable wrappers are refused.
   Fail closed.
3. **Future / malformed timestamp.** A genuine envelope timestamp is the real,
   offset-aware send time. Future-beyond-tolerance, unparseable, or
   timezone-naive timestamps are refused.
4. **Duplicate id.** The same `(source, id)` seen again in a short window with
   different content is a replay/confabulation tell.

A match exits 2 with a refusal reason on stderr (which the harness surfaces back
to the model). Everything else exits 0. The hook fails OPEN on any parser error,
so it can never wedge the agent loop.

## What ships here

| File | What it is |
|------|-----------|
| `reference/inbound-guard.example.py` | The portable hook. Four signatures, content-free, fails open, kill switch. |
| `reference/guard.config.example.json` | Example config: role labels, wrapper tag and trusted source, required attributes, dedup window, alert seam, soft-only flag. |
| `scripts/inbound-scan.py` | Standalone scanner over arbitrary text, `--self-test`, `--text`, stdin. Loads the same detection as the hook. |
| `templates/untrusted-content-fence.md` | The provenance/taint design doc for the wider retrieval pipeline. The real boundary. |
| `SKILL.md` | Step-by-step setup for an agent to wire it in. |

## Configuration

Set in `guard.config.json` (or a path exported as `INBOUND_GUARD_CONFIG`):

- `role_labels`: words to refuse at a body start. Default `["Human", "Assistant"]`.
- `wrapper_tag`: the XML-style tag your transport wraps genuine inbound in. Empty
  means role-label-only mode.
- `trusted_source_value`: the exact `source="..."` a genuine wrapper carries.
- `required_wrapper_attrs`: attributes a genuine wrapper always populates.
- `id_attr` / `ts_attr`: which wrapper attributes hold the id and timestamp.
- `future_ts_tolerance_s`: clock skew tolerated before "future". Default 5.
- `dedup_ttl_s` / `dedup_cache_path`: duplicate-id window and cache location.
- `on_block_command`: shell command run once per block with the event JSON on
  stdin (your alert seam). Optional.
- `soft_only`: if true, never exit 2; record and alert only (shadow mode).

## The soft echo signature (optional, not shipped as a block)

A fifth heuristic is worth knowing about but deliberately NOT shipped as a hard
block: flag a short inbound whose content-token overlap with the agent's own
most recent outbound is high. The pattern it catches is a forged reply that
echoes the agent's own recommendation straight back at it ("do X" right after
the agent proposed X). It is high-noise (a genuine "yes, do it" is short too), so
it belongs in alert-only mode with a human eyeballing the flags, never as an
automatic refusal. Implement it against your own outbound record if you want it,
and route it through `on_block_command` with `soft_only` semantics.

## Quickstart

1. Set `wrapper_tag` and `trusted_source_value` to match the envelope your
   transport emits, or leave `wrapper_tag` empty for role-label-only mode.
2. Register `reference/inbound-guard.example.py` on your harness prompt-submit
   event (stdin JSON with a `prompt` field, exit 2 blocks, exit 0 passes).
3. Verify with `python3 scripts/inbound-scan.py --self-test`.

## Kill switch

Set `INBOUND_GUARD_DISABLE` to `1` (or `true`, `yes`, `on`) to turn the hook into
a full no-op before it does any work. Unset it to re-enable.

## Honest limits

- **Structural forgery, not meaning.** A well-formed, correctly sourced,
  non-duplicate inbound passes even if its prose is manipulative. The guard
  raises the cost of impersonating your channel; it does not read intent.
- **A wrapper is only as trustworthy as its transport.** Source verification
  proves the envelope claims your channel, not that the channel is unforgeable
  end to end. Provenance plus an executing-agent intake guard (see the fence
  template) are the load-bearing controls.
- **Fails open by design.** Malformed input or any internal error passes. A
  front-line guard that wedges the loop is worse than the forgery it misses. Keep
  a behavioral backstop in your system prompt.

## License

MIT. See [LICENSE](./LICENSE).
