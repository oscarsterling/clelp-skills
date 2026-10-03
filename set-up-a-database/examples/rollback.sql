-- Write and review this before apply. Take a backup first. Dropping a table with data needs the data owner's explicit yes.
drop function notes_private.maintenance_note_count();
drop function public.note_count();
drop policy notes_delete on public.notes;
drop policy notes_update on public.notes;
drop policy notes_insert on public.notes;
drop policy notes_select on public.notes;
drop table public.notes;
drop schema notes_private;

-- Optional hardening rollback, only with a separate review:
-- alter default privileges for role postgres in schema public grant insert, update, delete, truncate, references, trigger on tables to anon, authenticated;
-- alter default privileges for role postgres in schema public grant execute on functions to anon, authenticated;
-- alter default privileges for role postgres grant execute on functions to public;
