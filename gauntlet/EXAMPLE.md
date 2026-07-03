# Worked example: hardening a worktree teardown hook

This is a real six-round Gauntlet run, lightly abstracted. It hardened a
graceful-exit teardown for throwaway git worktrees: on clean exit, the hook
released a lock, and, only when the branch was fully merged and the tree was
completely clean, removed the checkout. Get it wrong and you delete a commit that
exists nowhere else. There was a second, independent layer behind it (a periodic
sweep that reclaims dead worktrees), but the whole point of this loop was that the
front-line hook must not fail open and hand the sweep a corrupted state.

The finding counts across the six rounds were: **4, then 1, then 3, then 2, then
1, then 0.** Notice it is not monotonic. That is normal and healthy: each fix let
the reviewers stare harder at a narrower area, and the count rose in round 3 before
it fell. Convergence is double-zero, not a smooth glide.

## Round 1 - four findings

The first attack round against the initial code returned NO-GO with four distinct
finding groups, each with a reproducer:

1. **Hidden index bits.** The clean-tree check missed `assume-unchanged` and
   `skip-worktree` flags, so a file that looked committed could carry uncommitted
   local content the gate never saw.
2. **Ignored content and nested repos.** Ignored-only content and nested `.git`
   directories could be removed with the checkout even though they might hold the
   only copy of something.
3. **Reflog-only commits.** The teardown deleted branch refs, which could drop the
   last reference to a commit reachable only through the reflog.
4. **Trap semantics.** The exit-trap wiring had a shell-injection path through the
   worktree path, a double-fire case, and a race with an orphaned child process
   still holding a file descriptor under the tree.

Lesson: a first round on genuinely tricky code finds a lot. That is the loop
earning its cost immediately.

## Round 2 - one finding, and the first fix-by-class move

All four were fixed. The biggest structural decision: rather than make branch
deletion safe, the fixer removed branch deletion from this component entirely.
The checkout gets removed; branch lifecycle stays with the separately-gated sweep.
That killed the whole reflog-only loss class by construction rather than patching it.

The confirmation round split: one model returned GO, the other returned NO-GO with
a single surviving finding. On a commit made on a detached HEAD and then abandoned,
the only surviving reference lived in the worktree's own admin area, which
`git worktree remove` deletes. Concrete reproducer attached.

Lesson: the split is the value. One model was satisfied. The other found a real
hole. If you had run only the satisfied model, you would have shipped it.

## Round 3 - three findings, and the cross-lab catch that defines the method

The detached-HEAD case was fixed. The confirmation round split again, and this is
the round worth remembering. One model confidently returned GO. The other returned
NO-GO with three findings, the sharpest being a top-level pseudo-ref (`FETCH_HEAD`)
that the orphan gate did not scan, complete with a repro repo where the gate passes
and orphans a unique commit. Also flagged: annotated-tag objects slipping through
the reachability test, and a possible missed per-worktree packed-refs file.

The model that said GO reasoned wrong about the exact area the other model proved.
A single reviewer, either one alone, would have shipped a real orphan bug. The
orchestrator adjudicated: the reproducer cleared the bar, so NO-GO stood despite
the split.

Lesson: this is pillar two in one round. Two labs, different blind spots, an
adjudicator who trusts the reproducer over the confident GO.

## Round 4 - two findings, one from each model

The three round-3 findings were fixed. This confirmation round is the counter-proof
that the value is not "one model is always the smart one." Both models found
something, and they found different things:

- One model: a per-worktree ref namespace (`refs/rewritten/*`, used during
  interactive rebase) still unscanned, with a full repro where the sole pin lives
  there and gets deleted.
- The other model: the pseudo-ref scanner read only the first line of its target,
  so a multi-parent (octopus) merge with several `MERGE_HEAD` lines would have its
  later parents silently orphaned.

Two real findings, one from each lab, neither caught by the other.

Lesson: cross-lab is not redundancy. Each model is a different sensor.

## Round 5 - one finding

Both round-4 findings fixed. The confirmation round split one last time: GO from
one model, NO-GO from the other on a single named omission, `REBASE_HEAD`, another
worktree-local pseudo-ref not in the scanned set.

At this point the fixer noticed the pattern. Rounds 3, 4, and 5 were all the same
shape: "here is one more per-worktree admin file you forgot." Patching each named
member was a loop that would never end. Time to close the class.

## Round 6 - zero findings, by class closure

Instead of adding `REBASE_HEAD` to the list, the fix closed the entire enumerable
family two ways at once:

- Every remaining pseudo-ref is scanned line by line (not first-line-only), and
  each embedded object id is type-checked and tested for reachability, refusing if
  anything is pinned only there.
- Any git operation-state artifact whose format embeds object ids in a shape that
  will never be exhaustively parsed (rebase, cherry-pick sequencer, bisect state)
  causes removal to refuse on sight, no parsing attempted. Presence of the category
  equals "leave it alone."

The confirmation round returned **GO from both models, zero findings.** Convergence.
The pseudo-ref game was over, not because every member had been enumerated, but
because the category was closed by construction.

Lesson: this is pillar five. Rounds 3 through 5 were the loop teaching the fixer
that instance-patching does not terminate. Round 6 ended it by fixing the class.

## What the arc teaches

- Concrete reproducers are the whole game. Every finding above came with a repo
  state you could paste into a shell and watch fail. Nothing that failed to
  reproduce survived adjudication.
- The split rounds (2, 3, 5) each had one model satisfied and one model right. That
  gap is the argument for two labs.
- Round 4 shows both models contributing distinct findings, so neither is the
  "good" reviewer; they are complementary sensors.
- The finding count rose (round 3) before it fell. Convergence is double-zero, not
  a tidy downward line.
- The run ended when it ran out of classes to close, not when the humans ran out of
  patience. That is the convergence criterion doing its job.
