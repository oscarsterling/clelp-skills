-- Harden defaults once for objects created by postgres.
alter default privileges for role postgres revoke execute on functions from public;
alter default privileges for role postgres in schema public revoke execute on functions from anon, authenticated;
alter default privileges for role postgres in schema public revoke insert, update, delete, truncate, references, trigger on tables from anon, authenticated;

-- Create owner-scoped notes and enable RLS in this migration.
create table public.notes (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null default auth.uid() references auth.users (id) on delete cascade,
  body text not null check (char_length(body) <= 10000),
  created_at timestamptz not null default now()
);
alter table public.notes enable row level security;
create index notes_owner_id_idx on public.notes (owner_id);

-- Anon gets no table privilege at all; signed-in owners can write their own rows.
revoke all on public.notes from public, anon, authenticated;
grant select, insert, delete on public.notes to authenticated;
-- Table-wide UPDATE would let callers rewrite every column, such as email on a profile table.
grant update (body) on public.notes to authenticated;
create policy notes_select on public.notes for select to authenticated
  using ((select auth.uid()) = owner_id);
create policy notes_insert on public.notes for insert to authenticated
  with check ((select auth.uid()) = owner_id);
create policy notes_update on public.notes for update to authenticated
  using ((select auth.uid()) = owner_id)
  with check ((select auth.uid()) = owner_id);
create policy notes_delete on public.notes for delete to authenticated
  using ((select auth.uid()) = owner_id);

-- App RPC runs with the caller's RLS and an explicit execute grant.
create function public.note_count() returns bigint
language sql stable security invoker set search_path = ''
as $$ select count(*) from public.notes where owner_id = (select auth.uid()); $$;
revoke execute on function public.note_count() from public, anon;
grant execute on function public.note_count() to authenticated;

-- A definer function belongs in a schema the API does not expose.
create schema notes_private;
-- This schema is created by this migration and is not API exposed.
revoke all on schema notes_private from public, anon, authenticated;
grant usage on schema notes_private to service_role;
create function notes_private.maintenance_note_count() returns bigint
language sql stable security definer set search_path = ''
as $$ select count(*) from public.notes; $$;
revoke execute on function notes_private.maintenance_note_count() from public, anon, authenticated;
grant execute on function notes_private.maintenance_note_count() to service_role;
