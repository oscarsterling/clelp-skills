# Confirmation round packet (template)

Use this after a fix. It tells the models what changed, then asks them to
re-attack the fix and re-confirm everything else. Send unchanged to both models.
The point is not to get agreement; it is to see whether the fix introduced a new
hole or merely moved the old one.

---

CONFIRMATION REVIEW ([component name], round [N]): round [N-1] ended
[state the prior result honestly, e.g. "split: model A returned GO, model B
returned NO-GO with one surviving finding on X"]. That finding is now fixed.
Confirm or refute GO for [the decision on the table].

WHAT CHANGED SINCE ROUND [N-1] (fix per finding):
  1. [Finding 1] -> [what was changed, and note that it closes the CLASS, not
     just the named instance. State how the class is closed by construction.]
  2. [Finding 2] -> [...]

Test status: [suite name] [pass count], [downstream suite] [pass count],
[lint/static checks] clean.

YOUR TASK: re-attack the fixes and re-confirm the rest. A finding only counts
with a concrete input or state producing [wrong outcome 1], [wrong outcome 2],
or [downstream wrong outcome]. Refusal is [the safe direction], so a fix that
refuses on an ambiguous case is acceptable.

A finding of the form "another case like this could exist" must name a REAL,
concrete member not already covered by the fix's class closure or catch-all. If
the fix closed the class by construction, a plausible-sounding sibling is not a
finding unless you can show it escapes the closure.

If nothing concrete survives, say GO.

=== [FILE 1: path] ===
[full hardened file contents]

=== [FILE 2: path] ===
[full file contents]
