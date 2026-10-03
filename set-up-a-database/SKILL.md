---
name: set-up-a-database
description: Security-first checklist for Supabase tables, policies, functions, grants, and migrations.
when_to_use: Creating or altering a table, column, policy, view, function, grant, bucket policy, or migration on Supabase/Postgres with PostgREST; writing or applying a migration; "set up a database", "add a table", or "new RPC".
---

# Set up a database

Tested on PostgreSQL 17 with a non-superuser `postgres` migration role. The audit and probe are a worked example for `notes`; extend them with a behavioral check for every relation, RPC, and storage operation the migration changes.

## Phase 0: Scope

- [ ] List exposed schemas, changed objects, roles, and intended grants. Without it: an overlooked API path can stay open.
- [ ] Record the exact SQL bytes and security review sign-off. Without it: review can cover different code.

## Phase 1: Design access

- [ ] Name each table's anon readers, signed-in owners, and server-only writers. Without it: roles gain unintended access.
- [ ] Put each trust boundary in its own table. Without it: one broad policy can expose data across boundaries.
- [ ] Use `owner_id uuid not null default auth.uid()` for owned rows. Without it: writes can lack a trustworthy owner.
- [ ] Authorize from server-set `app_metadata` or your own tables, never `user_metadata` or `raw_user_meta_data`; any expression outside a simple owner pin needs review; signed-in anonymous users also have the `authenticated` role. Without it: callers can claim privileges they do not own.
- [ ] Keep service-role keys out of clients and route server-only writes through that role. Without it: public callers can impersonate the server.
- [ ] Keep private schemas out of the exposed schema list. Without it: helpers become API endpoints.
- [ ] Check that child rows cannot reference another owner's parent. Without it: a valid child owner can attach data to another user's object.
- [ ] Cap caller-supplied text and jsonb sizes. Without it: unbounded input can exhaust resources.

## Phase 2: Write migration and prepare rollback

Copy `examples/good-migration.sql` to one migration file, for example `migrations/<timestamp>_notes.sql`. Review, hash, stage, and apply that exact path. Write `examples/rollback.sql` before applying.

- [ ] Prepare recovery: write `examples/rollback.sql` before applying. Without it: a failed deployment has no reviewed reversal.
- [ ] Enable RLS in the same migration as every exposed table creation. Without it: rows are exposed before protection exists.
- [ ] Inventory existing policies before adding one; permissive policies combine with OR. Without it: a new policy can bypass an older restriction.
- [ ] Inspect and reconcile effective table privileges, including those inherited through role membership; revoke broad privileges from `public`, `anon`, and `authenticated`, then grant only needed operations. Without it: inherited and PUBLIC grants bypass intended access.
- [ ] Limit INSERT and UPDATE grants to safe columns when callers must not set or change `id`, `created_at`, `owner_id`, or role fields. Without it: table-wide UPDATE lets callers rewrite privileged fields.
- [ ] Pin owned-row SELECT, INSERT, UPDATE, and DELETE policies to `(select auth.uid()) = owner_id`, including both UPDATE expressions. Without it: users can read or change another owner's rows.
- [ ] Never use `with check (true)` or `using (true)` for exposed writes; give each policy an explicit TO role. Without it: a policy with no TO clause applies to public, and callers can claim another owner.
- [ ] Prefer SECURITY INVOKER; put definers outside exposed schemas, pin `search_path = ''`, and qualify object names. Without it: privileged code can resolve attacker-controlled names.
- [ ] Have every SECURITY DEFINER function callable by signed-in users check `auth.uid()` itself. Without it: the function may perform privileged work for the wrong user.
- [ ] Inspect and reconcile an existing function's ACL before granting EXECUTE; CREATE OR REPLACE preserves grants, and revoking from public and anon alone does not exclude other roles. Without it: an old grant can keep the function callable.
- [ ] Revoke EXECUTE on each callable function from public and anon, then grant the intended role. Without it: function access is broader than designed.
- [ ] Harden defaults with `alter default privileges for role postgres revoke execute on functions from public;` and `alter default privileges for role postgres in schema public revoke execute on functions from anon, authenticated;`, plus table write defaults. Without it: new functions may inherit PUBLIC EXECUTE or direct exposed-role grants.
- [ ] Use `with (security_invoker = true)` for views on Postgres 15 or later, and inspect every view and table they read, including those in non-exposed schemas. Without it: an invoker view can still expose an owner-rights view or unprotected table below it.
- [ ] Review every function called by an exposed view, including calls through nested views; inspect what its body reads and whether it runs as invoker or definer. Without it: the view audit cannot follow a function body, which reads with caller rights for invokers or owner rights for definers.
- [ ] Keep materialized views and foreign tables outside exposed schemas or revoke exposed SELECT. Without it: materialized views cannot use `security_invoker`, and RLS cannot filter these reads.
- [ ] Install extensions into a schema the API does not expose, for example `create extension ... schema extensions`, never public. Without it: extension functions such as http or dblink become callable endpoints.
- [ ] Keep user-data buckets private; pin the first folder to `auth.uid()` in SELECT, INSERT, UPDATE USING and WITH CHECK, and DELETE policies on `storage.objects`. Without it: a public bucket exposes files, and a SELECT policy checking only `bucket_id = '...'` lets every signed-in user read every user's files.
- [ ] Never revoke on or drop a schema this migration did not create; inventory an existing schema's ACL first. Without it: a feature migration can break unrelated objects.
- [ ] Take a backup and test restoring it before Apply. Without it: a backup may be unusable when recovery is needed.
- [ ] Review rollback effects and get the data owner's approval before a data-bearing drop. Without it: recovery can destroy data.

Bad patterns: `examples/bad-rls-off.sql`, `examples/bad-anon-insert-with-check-true.sql`, `examples/bad-definer-unpinned.sql`, and `examples/bad-schema-only-default-revoke.sql`.

## Phase 3: Probe on local or staging

- [ ] Apply the reviewed file to staging with `psql "$STAGING_DATABASE_URL" -X -v ON_ERROR_STOP=1 --single-transaction -f migrations/<timestamp>_notes.sql` before running the Phase 3 probe there. Without it: the probe tests an older schema instead of the migration.
- [ ] Run `psql "$STAGING_DATABASE_URL" -X -q -v ON_ERROR_STOP=1 -c "set probe.target = 'staging'" -f examples/probe-roles.sql` through a direct or session-mode connection. Check that psql exits with status 0, read its complete error output, and paste every PASS notice into the review. Without it: a failed run can print some PASS lines first, and transaction pooling can discard the target setting before the probe file runs.

The probe writes rows in `auth.users` and `public.notes`, takes locks, fires triggers, and rolls back its rows and cascades. Rollback cannot undo non-transactional effects such as HTTP, dblink, or external queues. Production needs `probe.target=production` and `probe.reviewed_triggers` set to the exact sorted comma list of `schema.table:trigger` entries for enabled user triggers directly on those two tables, for example `auth.users:on_auth_user_created`, or `none` when empty. Also review triggers on tables those triggers or cascades write to; the list does not cover them. Confirm all reviewed triggers have no non-transactional effects before running it there. Use the read-only audit on live databases by default.

On some Supabase Postgres 17.6.1.x images (supabase/postgres#2112), a reserved role calling a function it cannot execute crashes the server, so the probe checks privileges instead of calling. Upgrade the project if a function call by anon or authenticated ever drops the connection.
On affected images, an API caller hitting `/rpc/<function>` for a function its role was denied can crash the server, so do not rely on EXECUTE revokes on functions in exposed schemas there; upgrade the project first.


## Phase 4: Review, then apply

- [ ] Have a security reviewer inspect migration, rollback, and probe evidence; bind sign-off to `shasum -a 256 migrations/<timestamp>_notes.sql` or `sha256sum migrations/<timestamp>_notes.sql`. Without it: a review can authorize different SQL.
- [ ] Re-review after every byte change. Without it: the sign-off no longer identifies the applied migration.
- [ ] Pass any environment DDL gate or checker and honor a refusal. Without it: routing around a refusal applies unreviewed SQL.
- [ ] Apply using `psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 --single-transaction -f migrations/<timestamp>_notes.sql`, or a runner that wraps the whole file in one transaction. Without it: a failure can leave half a migration.

## Phase 5: Audit live state

- [ ] Run `psql "$DATABASE_URL" -X -q -At -v ON_ERROR_STOP=1 -c "set audit.exposed_schemas = 'public,api'" -f examples/post-apply-audit.sql`, through a direct or session-mode connection, adjusting the schema list; transaction pooling can discard these settings. Set `audit.allowed_execute` to `role:schema.function(argtypes)` entries separated by `;` and `audit.allowed_public_reads` to a comma list of `schema.relation` entries for reviewed anon SELECT grants. The read allowlist does not silence open-read-policy. Review each finding. Without it: current grants and policies can differ from the migration.
`policy-needs-review` prompts a human to read the policy; it is not a vulnerability verdict. Team or role membership policies such as `team_id in (select team_id from team_members where user_id = auth.uid())` appear there and can be safe when the membership table is owner-protected. When the storage schema is absent, storage checks are skipped with a notice.
`reachable-function` requires a hand review of the function body. The audit finds functions called by exposed views at any view depth, but cannot follow their table reads: invoker functions read with caller rights, and definer functions read with owner rights.
Functions that belong to extensions, such as Vault or pgsodium decrypt functions, are not listed by `reachable-function`; review them by hand when an exposed view calls them.

- [ ] If rollback is needed, run `psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 --single-transaction -f examples/rollback.sql`. Without it: a failed rollback can leave half-dropped objects.

A static checker is a floor, not a security review. It cannot see SQL inside function bodies or requests sent by other paths.
