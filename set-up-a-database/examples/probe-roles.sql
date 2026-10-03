-- Run as database owner in one request. Every seeded row rolls back.
begin;
set local lock_timeout = '2s';
set local statement_timeout = '30s';
-- Check the target and all enabled user triggers before any write.
do $$
declare target text := current_setting('probe.target', true);
declare reviewed text := current_setting('probe.reviewed_triggers', true);
declare actual text;
begin
  select coalesce(string_agg(format('%s.%s:%s', n.nspname, c.relname, t.tgname), ',' order by n.nspname, c.relname, t.tgname), 'none')
    into actual
  from pg_trigger t join pg_class c on c.oid = t.tgrelid
    join pg_namespace n on n.oid = c.relnamespace
  where c.oid in ('auth.users'::regclass, 'public.notes'::regclass)
    and not t.tgisinternal and t.tgenabled <> 'D';
  if target not in ('local', 'staging', 'production') or target is null
    or (target = 'production' and reviewed is distinct from actual) then
    raise exception 'REFUSED: set probe.target to local or staging (production needs probe.reviewed_triggers)';
  end if;
end $$;
insert into auth.users (id, aud, role, email) values
  ('10000000-0000-4000-8000-000000000001', 'authenticated', 'authenticated', 'user-a@example.invalid'),
  ('10000000-0000-4000-8000-000000000002', 'authenticated', 'authenticated', 'user-b@example.invalid');
-- The owner bypasses RLS because RLS is not forced. Anon must not see these rows; B's row makes an RLS-blind count differ from A's view.
insert into public.notes (id, owner_id, body) values
  ('20000000-0000-4000-8000-000000000001', '10000000-0000-4000-8000-000000000001', 'seeded by owner'),
  ('20000000-0000-4000-8000-000000000002', '10000000-0000-4000-8000-000000000002', 'seeded for B');

select set_config('request.jwt.claims', '{}', true);
select set_config('request.jwt.claim.sub', '', true);
set local role anon;
do $$
declare actual uuid[];
begin
  select coalesce(array_agg(id order by id), '{}'::uuid[]) into actual from public.notes
    where id in ('20000000-0000-4000-8000-000000000001', '20000000-0000-4000-8000-000000000002', '20000000-0000-4000-8000-000000000003');
  if actual <> '{}'::uuid[] then raise exception 'FAIL: anon saw notes'; end if;
  raise notice 'PASS: anon sees zero notes';
exception when insufficient_privilege then
  raise notice 'PASS: anon cannot read notes';
end $$;
do $$
begin
  insert into public.notes (owner_id, body) values ('10000000-0000-4000-8000-000000000001', 'forbidden');
  raise exception 'FAIL: anon insert succeeded';
exception when insufficient_privilege then
  raise notice 'PASS: anon insert denied';
end $$;

do $$
begin
  perform notes_private.maintenance_note_count();
  raise exception 'FAIL: anon called private maintenance function';
exception when insufficient_privilege then
  raise notice 'PASS: anon cannot call private maintenance function';
end $$;

reset role;
-- The calls above can fail on schema USAGE or table SELECT before EXECUTE is checked, so check function ACLs directly.
do $$
begin
  if has_function_privilege('anon', 'notes_private.maintenance_note_count()', 'EXECUTE')
    or has_function_privilege('authenticated', 'notes_private.maintenance_note_count()', 'EXECUTE') then
    raise exception 'FAIL: an exposed role holds EXECUTE on private maintenance function';
  end if;
  if has_function_privilege('anon', 'public.note_count()', 'EXECUTE') then
    raise exception 'FAIL: anon holds EXECUTE on note_count';
  end if;
  raise notice 'PASS: exposed roles hold only the intended EXECUTE grants';
end $$;
select set_config('request.jwt.claims', json_build_object('sub', '10000000-0000-4000-8000-000000000001', 'role', 'authenticated')::text, true);
select set_config('request.jwt.claim.sub', '10000000-0000-4000-8000-000000000001', true);
set local role authenticated;
do $$
begin
  perform notes_private.maintenance_note_count();
  raise exception 'FAIL: A called private maintenance function';
exception when insufficient_privilege then
  raise notice 'PASS: A cannot call private maintenance function';
end $$;
do $$
declare actual uuid[];
begin
  insert into public.notes (id, owner_id, body) values ('20000000-0000-4000-8000-000000000003', '10000000-0000-4000-8000-000000000001', 'owned by A');
  select coalesce(array_agg(id order by id), '{}'::uuid[]) into actual from public.notes
    where id in ('20000000-0000-4000-8000-000000000001', '20000000-0000-4000-8000-000000000002', '20000000-0000-4000-8000-000000000003');
  if actual <> array['20000000-0000-4000-8000-000000000001'::uuid, '20000000-0000-4000-8000-000000000003'::uuid] then
    raise exception 'FAIL: A sees wrong fixture rows'; end if;
  raise notice 'PASS: A inserted and read own note';
end $$;
do $$
declare via_rpc bigint; visible bigint;
begin
  select public.note_count() into via_rpc;
  select count(*) into visible from public.notes;
  if via_rpc is null or via_rpc is distinct from visible then raise exception 'FAIL: note_count disagrees with RLS for A'; end if;
  raise notice 'PASS: A can call note_count';
end $$;
do $$
begin
  update public.notes set owner_id = '10000000-0000-4000-8000-000000000002' where body = 'owned by A';
  raise exception 'FAIL: A reassigned a note to B';
exception when insufficient_privilege then
  raise notice 'PASS: A cannot hand a note to B';
end $$;
do $$
begin
  insert into public.notes (owner_id, body) values ('10000000-0000-4000-8000-000000000002', 'spoofed by A');
  raise exception 'FAIL: A inserted for B';
exception when insufficient_privilege then
  raise notice 'PASS: A cannot insert for B';
end $$;

reset role;
select set_config('request.jwt.claims', json_build_object('sub', '10000000-0000-4000-8000-000000000002', 'role', 'authenticated', 'user_metadata', json_build_object('is_admin', true, 'role', 'service_role'))::text, true);
select set_config('request.jwt.claim.sub', '10000000-0000-4000-8000-000000000002', true);
set local role authenticated;
do $$
declare actual uuid[];
begin
  select coalesce(array_agg(id order by id), '{}'::uuid[]) into actual from public.notes
    where id in ('20000000-0000-4000-8000-000000000001', '20000000-0000-4000-8000-000000000002', '20000000-0000-4000-8000-000000000003');
  if actual <> array['20000000-0000-4000-8000-000000000002'::uuid] then raise exception 'FAIL: B saw A note'; end if;
  raise notice 'PASS: B sees zero A notes';
end $$;
-- No WHERE: a column-reading filter also applies B's SELECT policy and could hide an open write policy.
update public.notes set body = 'changed by B';
reset role;
do $$
begin
  if not exists (select 1 from public.notes where id = '20000000-0000-4000-8000-000000000001' and body = 'seeded by owner')
    or not exists (select 1 from public.notes where id = '20000000-0000-4000-8000-000000000003' and body = 'owned by A') then
    raise exception 'FAIL: B updated A note';
  end if;
  if not exists (select 1 from public.notes where id = '20000000-0000-4000-8000-000000000002' and body = 'changed by B') then
    raise exception 'FAIL: B could not update own note';
  end if;
  raise notice 'PASS: B updates zero A notes';
end $$;
set local role authenticated;
delete from public.notes;
reset role;
do $$
begin
  if not exists (select 1 from public.notes where id = '20000000-0000-4000-8000-000000000001' and body = 'seeded by owner')
    or not exists (select 1 from public.notes where id = '20000000-0000-4000-8000-000000000003' and body = 'owned by A') then
    raise exception 'FAIL: B deleted A note';
  end if;
  if exists (select 1 from public.notes where id = '20000000-0000-4000-8000-000000000002') then
    raise exception 'FAIL: B could not delete own note';
  end if;
  raise notice 'PASS: B deletes zero A notes';
end $$;
select set_config('request.jwt.claims', '{}', true);
select set_config('request.jwt.claim.sub', '', true);
rollback;
