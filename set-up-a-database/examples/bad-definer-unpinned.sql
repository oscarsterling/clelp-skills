-- BAD EXAMPLE (definer-search-path): a caller can influence name resolution.
create function public.maintenance_note_count() returns bigint
language sql security definer as $$ select count(*) from public.notes; $$;
