---
name: gauntlet
description: Adversarial hardening loop for security-critical code, especially hooks and guards that must never fail open. Two models from different labs attack the same code for concrete, reproducible failures; an orchestrator adjudicates; a separate fixer closes the enumerable class; repeat until both models return zero findings. Use it when a wrong outcome is expensive and "looks fine" is not good enough.
---

# The Gauntlet

The Gauntlet is a loop for hardening code that must not fail silently: deletion
guards, permission hooks, safety gates, anything where a single missed case is a
real incident. It is more than "ask an AI to review this." Plain review gives you
a list of vibes and maybe a real bug buried in it. The Gauntlet is structured so
that the only things that survive are concrete, reproducible failures, and so that
one model's blind spot does not become your shipped bug.

## When to use it

Reach for the Gauntlet when all of these are true:

- A wrong result is expensive or irreversible (data loss, a security bypass, a
  destructive action taken on bad state).
- The failure surface is enumerable but large: many small cases, easy to miss one.
- You can write down a clear pass/fail contract for the code under review.

If the cost of being wrong is low, or you cannot articulate what "wrong" means,
this loop is overkill. Use a normal review.

## The five pillars

These are what separate the Gauntlet from ordinary red-teaming. Drop any one of
them and the loop stops working.

1. **The concrete-reproducer bar.** A finding only counts if it comes with an
   input or a state that actually produces the failure: the exact commands, the
   exact file contents, the sequence of events. "This looks risky" is not a
   finding. "Here is a repo state where the gate passes and deletes a commit that
   exists nowhere else" is a finding. This bar is what filters style notes from
   real holes, and it is what makes a fix verifiable: you can reproduce the state,
   apply the fix, and watch the failure disappear.

2. **Cross-lab two-model review.** Send the same packet to two models from
   different labs (this skill ships an OpenAI leg and a Google Gemini leg). They
   have different blind spots. In a real run, one model confidently returned GO on
   the exact area where the other model proved a concrete hole. A single reviewer
   would have shipped that bug. The orchestrator adjudicates disagreements: when
   the models split, you read both arguments and decide which one reasoned
   correctly, rather than averaging them.

3. **Reviewer and builder separation.** The models that attack never write the
   fix. The person or agent that writes the fix never sits in judgment of whether
   it holds. The next round's confirmation is done by the attackers again, against
   the new code, with no memory that they are supposed to be satisfied. This keeps
   the fixer honest and stops the review from collapsing into self-congratulation.

4. **The convergence criterion.** You do not stop when you are tired of iterating.
   You stop when a round returns zero findings from BOTH models against the current
   code. One model saying GO is not convergence; it is one data point. Double-zero
   is the bar.

5. **Class-closure discipline.** When a finding names an instance, fix the whole
   enumerable family, not the one member. If a reviewer proves that one obscure
   per-file admin record can be orphaned, do not add a check for that one record;
   enumerate the entire category and close it by construction (scan every member,
   or refuse on the presence of the category outright). Fixing instances is how you
   get a loop that never terminates. Closing classes is how you end it.

## The round structure

Each round is four moves. You repeat rounds until you converge.

1. **Attack round.** Hand the models a packet: the code under review, the exact
   contract it must satisfy, and an attack framing that names the surfaces to hit
   and restates the concrete-reproducer bar. Each model hunts independently and
   returns findings (with reproducers) or a GO. Use `templates/attack-round.md`.

2. **Adjudication.** The orchestrator reads both responses. Discard anything that
   does not clear the reproducer bar. Where the models disagree, decide who is
   right and why. The output is a short list of real findings, each tied to a class.

3. **Fix.** The fixer (a different agent, or at least a separate pass) closes each
   finding's class, not its instance. Re-run the code's own test suite; add a test
   that encodes the reproducer so the failure can never come back silently.

4. **Confirmation round.** Send the hardened code back to both models with a
   confirmation framing: here is what changed, re-attack the fix and re-confirm the
   rest. Use `templates/confirmation-round.md`. If both return zero findings, you
   have converged. If not, the surviving findings feed the next round.

A healthy run's finding count trends toward zero but is not monotonic: a fix can
expose an area the models look at harder next round, so counts can rise before they
fall. That is the loop doing its job, not a regression. See `EXAMPLE.md` for a real
six-round arc.

## Running a round

`scripts/gauntlet-bounce.py` runs one attack or confirmation round across both
models. It reads a packet, calls both APIs, and writes a transcript with each
model's full response plus a verdict summary and a converged flag.

```bash
export OPENAI_API_KEY=...   # your key
export GEMINI_API_KEY=...   # your key
python3 scripts/gauntlet-bounce.py --file round-1-packet.md --out round-1.md
```

Bring your own keys via those two environment variables. Model ids are overridable
with `--openai-model` / `--gemini-model` (or the `OPENAI_MODEL` / `GEMINI_MODEL`
env vars) so you can point each leg at whatever current model you want. Use two
strong reasoning models; weak models generate noise that fails the reproducer bar
and wastes adjudication time. `--dry-run` validates your packet and keys without
making any network calls.

The verdict parse in the script is a convenience for tracking convergence. It is
not the judgment. A model can print GO and still be wrong, which is the entire
reason there are two of them and a human-or-agent adjudicator in the middle.

## Honest limitations

- **A hook is only as strong as the conditions you wrote.** The Gauntlet hardens
  the code you give it against the contract you state. It cannot test a requirement
  you never wrote down. If your contract says "block rm" but the real requirement
  is "block deletion," an agent that reaches for `mv` or truncation walks straight
  through. Enumeration gaps like that are precisely what this loop exists to surface,
  but only if the attack framing invites the models to question the contract itself,
  not just the implementation.

- **Two models are not infinite models.** Cross-lab review shrinks the blind spot;
  it does not eliminate it. Both models can share a wrong assumption. Convergence
  means "neither of these two reviewers can find a concrete failure right now," not
  "this code is provably correct."

- **It costs real tokens and real time.** A six-round arc is a dozen or more model
  calls on large packets plus fix cycles in between. Spend it where a wrong outcome
  is expensive. Do not run the Gauntlet on code where a bug is a shrug.

- **Convergence is a stopping rule, not a proof.** Double-zero is where you ship,
  not where the code becomes perfect. Pair it with the code's own test suite and,
  for anything genuinely destructive, a layered design where a second independent
  gate fails closed if this one fails open.
