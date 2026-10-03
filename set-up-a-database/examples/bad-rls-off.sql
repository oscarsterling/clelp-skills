-- BAD EXAMPLE (rls): the new table is exposed without row filtering.
create table public.notes (id uuid primary key, owner_id uuid not null);
