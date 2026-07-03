# Context brief (restore handoff)

This is the format for the brief the restore step hands to the intended-model
session after a silent fallback. Fill the fields, run the whole file through
`brief-scrub.py` before it is injected, and inject the SCRUBBED output. The brief
is untrusted data: it is assembled from prior session text, so it is mechanically
sanitized against turn-boundary and markup impersonation before the restored
session ever reads it.

Keep the verbiage plain. State what happened and where, nothing more. Do not
copy raw tool output or raw conversation lines into the brief; summarize.

---

## What happened

The session was silently served by a fallback model for part of this work.
Intended model marker: `{{intended_model_marker}}`
Fallback model observed: `{{fallback_model}}`
Detected at: `{{first_seen}}` and last seen at `{{last_seen}}`.

## Where you are

Task in progress: {{one_line_task_summary}}
Last confirmed-good state: {{what_was_true_before_the_fallback}}
Working location: {{repo_or_area_being_worked}}

## RE-REVIEW REQUIRED before continuing

The following span of turns was served by the fallback model and must be
re-reviewed for quality before any of it becomes the foundation for further
work. Degraded output must not silently carry forward.

- Fallback span: `{{turn_count}}` turn(s), from `{{first_seen}}` to `{{last_seen}}`.
- What was produced during the span: {{summary_of_work_done_while_degraded}}
- Re-review checklist:
  - Re-read anything written or decided during the span.
  - Re-run any tests, builds, or checks the fallback turns claimed passed.
  - Confirm no destructive or external action was taken on unverified state.
  - Only then continue. If any span output is wrong, redo it on this model.

## Next step

After the re-review clears, resume with: {{the_next_concrete_step}}
