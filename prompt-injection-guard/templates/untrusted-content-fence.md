# Untrusted-Content Fence

The standing rule for any content your stack SCRAPES or FETCHES from the outside
world: it is DATA to be analyzed, never INSTRUCTIONS to be obeyed. The inbound
guard is the cheap deterministic front line that refuses forged operator
commands. This document is the design convention that makes the rest of a
retrieval-to-model pipeline safe. Provenance, not blanket lockdown, decides what
gets gated, so trusted operator channels keep their zero-friction path.

## The trust boundary

- **TRUSTED, unchanged:** the operator's own command channels (the app's
  authenticated control surface, a direct terminal session, a cryptographically
  signed relay). These stay fast. Nothing here adds friction to a genuine
  operator command.
- **UNTRUSTED, gated:** any externally authored content pulled from outside
  operator-controlled channels (web page text, social posts and bios, video
  transcripts and titles, channel descriptions, fetch output, OCR text, link
  previews, issue bodies, PDF text). Same words, different provenance: an
  instruction from the operator on the trusted channel is a command; the
  identical sentence inside a fetched transcript is advisory-only.

## The taint model (information-flow control)

1. **Taint at the source.** Content from an untrusted retrieval is untrusted.
2. **Propagate through transforms.** A summary of untrusted content is STILL
   untrusted. A ticket or record built from it is STILL untrusted. Taint must
   survive summarization, staging, and record creation. The "summary laundering"
   attack is a poisoned summary shedding its taint. Where a whole pipeline is
   untrusted by construction, stamp the taint unconditionally rather than
   tracking it per item.
3. **Enforce at the sinks.** Sinks are where untrusted content could become
   action: filing a task, routing to another agent, running a shell command,
   writing durable memory, changing a schedule, posting externally. The
   load-bearing control lives at the sink, not at creation.

## The controls, in order of load-bearingness

1. **Executing-agent intake guard (THE control).** No component with execution
   authority acts on an untrusted-origin artifact unless it carries a
   NON-FORGEABLE operator clearance. Untrusted-origin is a provenance stamp;
   clearance is applied ONLY by the operator or an operator-reviewed trusted
   path, never by an automated pipeline, and a body phrase like "the operator
   approved this" does NOT count (it is forgeable). The guard refuses
   untrusted-and-uncleared work and allows ordinary or cleared work.
2. **Wrapper-enforced provenance + advisory rendering.** The pipeline that files
   a record from untrusted content stamps the untrusted origin unconditionally
   and renders its fields as SUGGESTIONS, never as execution routes. Ownership
   and next-action stay with the operator.
3. **The fence primitive (hygiene, not the boundary).** Wrap a retrieved blob in
   a spotlight tag with an explicit data-vs-instruction contract so a
   well-behaved model is not talked into obeying the data, and FLAG (never
   silently strip) apparent injection markers so a consumer can downrank or skip.
   Be honest: the fence is a representation aid. Provenance plus the intake guard
   are the actual security.
4. **Agent-contract spotlighting.** Every consuming agent's brief states plainly
   that the retrieved fields are untrusted data and that instructions inside them
   are a risk signal, never a command.

## Gate execution, not capture

Synchronous approval on every auto-captured item worsens review fatigue and
kills nimbleness. So let untrusted pipelines capture freely (file advisory
records, write staging files) with no new prompts, but forbid them from writing
durable behavioral memory (system prompts, preference files, routing
heuristics). Untrusted-derived storage is tainted evidence, not preference. The
conscious-decision posture lands at the EXECUTION sink via the intake guard.

## Adding a new retrieval seam

When you wire a new path that pulls external content into a tool-capable model:
taint it at the source, keep the taint through every transform, fence the blob
for hygiene, and make sure every downstream sink either refuses
untrusted-and-uncleared work (executing agents) or renders it as advisory
evidence (everything else). Never let an untrusted-derived field become an
execution route or a durable behavioral-memory write.
