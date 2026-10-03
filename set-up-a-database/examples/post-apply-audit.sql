-- Run as the migration owner. Set audit.exposed_schemas before this file when the API exposes more than public.
-- Every row is a finding. Review allowlists against exact relation and function identities.
-- policy-needs-review asks a human to inspect the expression, not to treat it as a vulnerability verdict.
-- Team or role membership checks, such as team_id in (select team_id from team_members where user_id = auth.uid()), appear here and can be safe when the membership table is owner-protected.
-- A view's function calls cannot be followed into SQL function bodies. Review each reachable-function by hand: invokers read with caller rights, definers with owner rights.
-- Functions that belong to extensions, such as Vault or pgsodium decrypt functions, are not listed by reachable-function; review them by hand when an exposed view calls them.
select from (select set_config('audit.exposed_schemas', coalesce(nullif(current_setting('audit.exposed_schemas', true), ''), 'public'), false) as v) s where v is null;

-- Cache root:relation OID pairs so both the policy and view checks use one view walk.
with recursive exposed as (
  select c.oid as root, c.oid as relation, array[c.oid] as visited
  from pg_class c join pg_namespace n on n.oid = c.relnamespace
  where n.nspname = any(string_to_array(replace(current_setting('audit.exposed_schemas'), ' ', ''), ','))
    and c.relkind in ('v', 'm')
), reachable as (
  select * from exposed
  union all
  select r.root, dep.refobjid, r.visited || dep.refobjid
  from reachable r join pg_class source on source.oid = r.relation
  join pg_rewrite rw on rw.ev_class = source.oid
  join pg_depend dep on dep.classid = 'pg_rewrite'::regclass and dep.objid = rw.oid
    and dep.refclassid = 'pg_class'::regclass and dep.refobjid <> source.oid
  where source.relkind in ('v', 'm') and not dep.refobjid = any(r.visited)
)
select from (select set_config('audit.reachable_relations',
  coalesce(string_agg(distinct root::text || ':' || relation::text, ','), ''), false) as v
  from reachable) s where v is null;

select 'rls-off' as finding, n.nspname, c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace
where n.nspname = any(string_to_array(replace(current_setting('audit.exposed_schemas'), ' ', ''), ','))
  and c.relkind in ('r', 'p') and not c.relrowsecurity;

select 'rls-bypass-role' as finding, exposed.rolname, bypass.rolname
from pg_roles exposed cross join pg_roles bypass
where exposed.rolname in ('anon', 'authenticated') and bypass.rolbypassrls
  and pg_has_role(exposed.oid, bypass.oid, 'MEMBER');

select 'owner-member-no-force-rls' as finding, n.nspname, c.relname, exposed.rolname, owner.rolname
from pg_class c join pg_namespace n on n.oid = c.relnamespace
join pg_roles owner on owner.oid = c.relowner cross join pg_roles exposed
where n.nspname = any(string_to_array(replace(current_setting('audit.exposed_schemas'), ' ', ''), ','))
  and c.relkind in ('r', 'p') and c.relrowsecurity and not c.relforcerowsecurity
  and exposed.rolname in ('anon', 'authenticated')
  and pg_has_role(exposed.oid, owner.oid, 'MEMBER');

select 'extension-exposed-schema' as finding, e.extname, n.nspname from pg_extension e
join pg_namespace n on n.oid = e.extnamespace
where n.nspname = any(string_to_array(replace(current_setting('audit.exposed_schemas'), ' ', ''), ','));

-- Check each selected policy expression independently, including both UPDATE and ALL arms.
with policy_expressions as (
  select p.schemaname, p.tablename, p.policyname, e.expr,
    case when p.schemaname = 'storage' then 'storage-policy'
      when e.arm = 'read' then 'open-read-policy' else 'open-write-policy' end as open_finding
  from pg_policies p
  cross join lateral (values
    ('read', case when p.cmd in ('SELECT', 'ALL') then coalesce(p.qual, 'true') end),
    ('write', case when p.cmd in ('UPDATE', 'DELETE', 'ALL') then coalesce(p.qual, 'true') end),
    ('write', case when p.cmd in ('INSERT', 'UPDATE', 'ALL') then coalesce(p.with_check, case when p.cmd in ('UPDATE', 'ALL') then p.qual end, 'true') end)
  ) e(arm, expr)
  where e.expr is not null
    and (p.schemaname = any(string_to_array(replace(current_setting('audit.exposed_schemas'), ' ', ''), ','))
      or p.schemaname = 'storage' and p.tablename = 'objects'
      or to_regclass(format('%I.%I', p.schemaname, p.tablename))::oid in (
        select split_part(pair, ':', 2)::oid
        from unnest(string_to_array(current_setting('audit.reachable_relations'), ',')) pair))
    and exists (select 1 from unnest(p.roles) policy_name
      left join pg_roles policy_role on policy_role.rolname = policy_name
      where policy_name = 'public' or pg_has_role('anon', policy_role.oid, 'MEMBER')
        or pg_has_role('authenticated', policy_role.oid, 'MEMBER'))
), patterns as (
  select *, lower(regexp_replace(expr, '[[:space:]]+', '', 'g')) as compact
  from policy_expressions
), classified as (
  select *, case
    when expr ~* 'user_metadata|raw_user_meta_data' then 'metadata-policy'
    when btrim(expr, ' ()') = 'false' then null
    when compact ~ ('^\(*(' ||
      '[a-z_][a-z_0-9]*=\(*((select)?auth\.uid\(\)(asuid)?)\)*' ||
      '|\(*((select)?auth\.uid\(\)(asuid)?)\)*=[a-z_][a-z_0-9]*' ||
      '|\(*storage\.foldername\(name\)\)*\[1\]=\(*((select)?auth\.uid\(\)(asuid)?)\)*::text' ||
      '|\(*((select)?auth\.uid\(\)(asuid)?)\)*::text=\(*storage\.foldername\(name\)\)*\[1\]' ||
      ')\)*(and\(*bucket_id=''[^'']*''(::text)?\)*)?\)*$')
      or compact ~ ('^\(*bucket_id=''[^'']*''(::text)?\)*and\(*(' ||
      '[a-z_][a-z_0-9]*=\(*((select)?auth\.uid\(\)(asuid)?)\)*' ||
      '|\(*((select)?auth\.uid\(\)(asuid)?)\)*=[a-z_][a-z_0-9]*' ||
      '|\(*storage\.foldername\(name\)\)*\[1\]=\(*((select)?auth\.uid\(\)(asuid)?)\)*::text' ||
      '|\(*((select)?auth\.uid\(\)(asuid)?)\)*::text=\(*storage\.foldername\(name\)\)*\[1\]' ||
      ')\)*$') then null
    when btrim(expr, ' ()') = 'true' or
      (regexp_replace(expr, '\(*[[:space:]]*(SELECT[[:space:]]+)?auth\.uid\(\)([[:space:]]+AS[[:space:]]+uid)?[[:space:]]*\)*[[:space:]]+IS[[:space:]]+(NOT[[:space:]]+)?NULL', '', 'gi') not ilike '%auth.uid()%'
        and expr not ilike '%auth.jwt()%')
      then open_finding
    else 'policy-needs-review' end as finding
  from patterns
)
select distinct finding, schemaname, tablename, policyname from classified where finding is not null;

-- postgres is the role migrations run as here; add any other role your migrations use.
select 'definer-search-path' as finding, n.nspname, p.oid::regprocedure from pg_proc p join pg_namespace n on n.oid = p.pronamespace
where n.nspname not in ('pg_catalog', 'information_schema') and n.nspname not like 'pg_toast%'
  and n.nspname not like 'pg_temp%' and p.proowner = 'postgres'::regrole and p.prosecdef
  and not exists (select 1 from unnest(coalesce(p.proconfig, array[]::text[])) setting
    where setting in ('search_path=""', 'search_path=pg_catalog, pg_temp', 'search_path=pg_catalog,pg_temp'))
  and not exists (select 1 from pg_depend dep where dep.classid = 'pg_proc'::regclass and dep.objid = p.oid and dep.deptype = 'e');

-- postgres is the role migrations run as here; add any other role your migrations use.
select 'definer-exposed-schema' as finding, n.nspname, p.oid::regprocedure from pg_proc p join pg_namespace n on n.oid = p.pronamespace
where n.nspname = any(string_to_array(replace(current_setting('audit.exposed_schemas'), ' ', ''), ','))
  and p.prosecdef and p.proowner = 'postgres'::regrole
  and not exists (select 1 from pg_depend dep where dep.classid = 'pg_proc'::regclass and dep.objid = p.oid and dep.deptype = 'e');

-- Allowed entries are semicolon-separated role:schema.function(argtypes) identities.
select 'function-execute' as finding, p.oid::regprocedure, r.rolname from pg_proc p
join pg_namespace n on n.oid = p.pronamespace cross join pg_roles r
where n.nspname = any(string_to_array(replace(current_setting('audit.exposed_schemas'), ' ', ''), ','))
  and r.rolname in ('anon', 'authenticated') and has_function_privilege(r.rolname, p.oid, 'EXECUTE')
  and not exists (select 1 from unnest(string_to_array(coalesce(nullif(current_setting('audit.allowed_execute', true), ''), 'authenticated:public.note_count()'), ';')) entry
    where split_part(entry, ':', 1) = r.rolname and p.oid = to_regprocedure(split_part(entry, ':', 2)))
  and not exists (select 1 from pg_depend dep where dep.classid = 'pg_proc'::regclass and dep.objid = p.oid and dep.deptype = 'e');

-- postgres is the role migrations run as here; add any other role your migrations use.
select 'default-acl' as finding, n.nspname, d.defaclobjtype, coalesce(r.rolname, 'PUBLIC') as grantee, x.privilege_type
from pg_default_acl d left join pg_namespace n on n.oid = d.defaclnamespace
cross join lateral aclexplode(d.defaclacl) x left join pg_roles r on r.oid = x.grantee
where (d.defaclnamespace = 0 or n.nspname = any(string_to_array(replace(current_setting('audit.exposed_schemas'), ' ', ''), ',')))
  and d.defaclrole = 'postgres'::regrole and (x.grantee = 0 or r.rolname in ('anon', 'authenticated'))
  and ((d.defaclobjtype = 'r' and x.privilege_type in ('INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER'))
    or (d.defaclobjtype = 'f' and x.privilege_type = 'EXECUTE')
    or (d.defaclobjtype = 'n' and x.privilege_type = 'CREATE'));

select 'view-owner-rights' as finding, n.nspname, c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace
where n.nspname = any(string_to_array(replace(current_setting('audit.exposed_schemas'), ' ', ''), ',')) and c.relkind = 'v'
  and not exists (select 1 from unnest(coalesce(c.reloptions, array[]::text[])) o where o in ('security_invoker=true', 'security_invoker=on', 'security_invoker=1'));

-- Inspect private relations reached through exposed views.
with reachable as (
  select split_part(pair, ':', 1)::oid as root, split_part(pair, ':', 2)::oid as relation
  from unnest(string_to_array(current_setting('audit.reachable_relations'), ',')) pair
)
select distinct case when target.relkind = 'v' then 'reachable-owner-rights-view'
    when target.relkind in ('r', 'p') then 'reachable-rls-off'
    else 'reachable-unfiltered-relation' end as finding,
  root_ns.nspname, root.relname, target_ns.nspname, target.relname
from reachable r join pg_class root on root.oid = r.root
join pg_namespace root_ns on root_ns.oid = root.relnamespace
join pg_class target on target.oid = r.relation
join pg_namespace target_ns on target_ns.oid = target.relnamespace
where target_ns.nspname <> all(string_to_array(replace(current_setting('audit.exposed_schemas'), ' ', ''), ','))
  and (target.relkind = 'v' and not exists (select 1 from unnest(coalesce(target.reloptions, array[]::text[])) o
        where o in ('security_invoker=true', 'security_invoker=on', 'security_invoker=1'))
    or target.relkind in ('r', 'p') and not target.relrowsecurity
    or target.relkind in ('m', 'f'));

-- SQL function bodies do not expose their table reads through pg_depend.
with reachable as (
  select split_part(pair, ':', 1)::oid as root, split_part(pair, ':', 2)::oid as relation
  from unnest(string_to_array(current_setting('audit.reachable_relations'), ',')) pair
)
select distinct 'reachable-function' as finding, root_ns.nspname, root.relname,
  p.oid::regprocedure, case when p.prosecdef then 'definer' else 'invoker' end
from reachable r join pg_class root on root.oid = r.root
join pg_namespace root_ns on root_ns.oid = root.relnamespace
join pg_class source on source.oid = r.relation
join pg_rewrite rw on rw.ev_class = source.oid
join pg_depend dep on dep.classid = 'pg_rewrite'::regclass and dep.objid = rw.oid
  and dep.refclassid = 'pg_proc'::regclass
join pg_proc p on p.oid = dep.refobjid
join pg_namespace function_ns on function_ns.oid = p.pronamespace
where source.relkind in ('v', 'm') and function_ns.nspname <> 'pg_catalog'
  and not exists (select 1 from unnest(array[
    to_regprocedure('auth.uid()'), to_regprocedure('auth.jwt()'),
    to_regprocedure('auth.role()')]) helper where helper::oid = p.oid)
  and not exists (select 1 from pg_depend extension_dep
    where extension_dep.classid = 'pg_proc'::regclass and extension_dep.objid = p.oid
      and extension_dep.deptype = 'e');

-- postgres is the role migrations run as here; add any other role your migrations use.
select 'global-function-default' as finding
where not exists (select 1 from pg_default_acl d where d.defaclrole = 'postgres'::regrole
  and d.defaclnamespace = 0 and d.defaclobjtype = 'f'
  and not exists (select 1 from aclexplode(d.defaclacl) x where x.grantee = 0 and x.privilege_type = 'EXECUTE'));

select 'matview-or-foreign-readable' as finding, n.nspname, c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace
where n.nspname = any(string_to_array(replace(current_setting('audit.exposed_schemas'), ' ', ''), ',')) and c.relkind in ('m', 'f')
  and (has_table_privilege('anon', c.oid, 'SELECT') or has_any_column_privilege('anon', c.oid, 'SELECT')
    or has_table_privilege('authenticated', c.oid, 'SELECT') or has_any_column_privilege('authenticated', c.oid, 'SELECT'));

do $$ begin
  if to_regclass('storage.buckets') is null then
    raise notice 'SKIPPED: storage.buckets absent; storage bucket check did not run';
  end if;
end $$;

select 'public-bucket' as finding, bucket.id
from xmltable('/table/row' passing
  (case when to_regclass('storage.buckets') is not null
    then query_to_xml('select id from storage.buckets where public', false, false, '') end)
  columns id text path 'id') bucket;

select 'no-to-clause' as finding, schemaname, tablename, policyname from pg_policies
where schemaname = any(string_to_array(replace(current_setting('audit.exposed_schemas'), ' ', ''), ',')) and roles = '{public}';

select 'dangerous-table-privilege' as finding, n.nspname, c.relname, r.rolname, v.privilege
from pg_class c join pg_namespace n on n.oid = c.relnamespace cross join pg_roles r
cross join (values ('TRUNCATE'), ('REFERENCES'), ('TRIGGER')) v(privilege)
where n.nspname = any(string_to_array(replace(current_setting('audit.exposed_schemas'), ' ', ''), ','))
  and c.relkind in ('r', 'p', 'v', 'm', 'f') and r.rolname in ('anon', 'authenticated')
  and has_table_privilege(r.rolname, c.oid, v.privilege);

select 'anon-table-privilege' as finding, n.nspname, c.relname, v.privilege
from pg_class c join pg_namespace n on n.oid = c.relnamespace
cross join (values ('SELECT'), ('INSERT'), ('UPDATE'), ('DELETE')) v(privilege)
where n.nspname = any(string_to_array(replace(current_setting('audit.exposed_schemas'), ' ', ''), ','))
  and c.relkind in ('r', 'p', 'v', 'm', 'f')
  and (v.privilege <> 'SELECT' or n.nspname || '.' || c.relname <> all(string_to_array(coalesce(current_setting('audit.allowed_public_reads', true), ''), ',')))
  and (has_table_privilege('anon', c.oid, v.privilege)
    or case when v.privilege in ('SELECT', 'INSERT', 'UPDATE')
      then has_any_column_privilege('anon', c.oid, v.privilege) else false end);

select 'public-table-grant' as finding, n.nspname, c.relname, x.privilege_type
from pg_class c join pg_namespace n on n.oid = c.relnamespace cross join lateral aclexplode(coalesce(c.relacl, '{}'::aclitem[])) x
where n.nspname = any(string_to_array(replace(current_setting('audit.exposed_schemas'), ' ', ''), ','))
  and c.relkind in ('r', 'p', 'v', 'm', 'f') and x.grantee = 0;

select 'schema-create' as finding, n.nspname, r.rolname from pg_namespace n cross join pg_roles r
where n.nspname = any(string_to_array(replace(current_setting('audit.exposed_schemas'), ' ', ''), ','))
  and r.rolname in ('anon', 'authenticated') and has_schema_privilege(r.rolname, n.oid, 'CREATE')
union all
select 'schema-create' as finding, n.nspname, 'PUBLIC' from pg_namespace n
where n.nspname = any(string_to_array(replace(current_setting('audit.exposed_schemas'), ' ', ''), ','))
  and exists (select 1 from aclexplode(coalesce(n.nspacl, '{}'::aclitem[])) x where x.grantee = 0 and x.privilege_type = 'CREATE');
