-- BAD EXAMPLE (anon-write): anonymous callers can insert rows for anyone.
alter table public.notes enable row level security;
grant insert on public.notes to anon;
create policy notes_open_insert on public.notes for insert to anon with check (true);
