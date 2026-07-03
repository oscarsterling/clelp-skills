# Attack round packet (template)

Fill in the bracketed fields and send the whole thing as one packet to
`gauntlet-bounce.py`. Keep the code inline in the packet so each model reviews
exactly what will ship. One packet goes to both models unchanged.

---

ADVERSARIAL REVIEW ([component name], round [N]): confirm or refute GO for
[the specific decision on the table, e.g. "wiring this teardown into a live
auto-deleting hook"].

CONTEXT: [What this code does and why it is dangerous if wrong. Name the
failure modes that matter: what counts as a wrong outcome. State the layering:
is there a second gate behind this one, and which direction does each fail
(open or closed)? A reviewer needs to know whether refusal is always safe.]

THE CONTRACT: [The exact pass/fail rules the code must satisfy. List every gate
and what it checks. Be precise; this is what the models attack against.]

YOUR TASK: attack the code below. A finding only counts with a concrete
input or state that produces one of:
  (1) [wrong outcome 1, e.g. a destructive action on content that exists nowhere else]
  (2) [wrong outcome 2, e.g. a safety gate silently disabled]
  (3) [wrong outcome 3, e.g. a state that later causes a downstream component to fail]

Attack surfaces, non-exhaustive:
  (a) [surface 1 and the specific question to probe]
  (b) [surface 2 ...]
  (c) [surface 3 ...]

Question the contract itself, not only the implementation: if the stated rule
does not match the real requirement, that is a finding. An enumeration gap (the
code blocks one mechanism but an equivalent mechanism reaches the same bad
outcome) is a finding.

Findings need a concrete reproduction state, a severity, and which layer must
fix them. Refusal is [always safe / describe the safe direction], so a fix that
moves an ambiguous case toward refusal is acceptable. If nothing concrete
survives, say GO.

=== [FILE 1: path] ===
[full file contents]

=== [FILE 2: path, e.g. the enforcement boundary or downstream consumer] ===
[full file contents]
