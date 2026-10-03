-- BAD EXAMPLE (default-privileges): ineffective hardening when the global PUBLIC grant exists.
alter default privileges for role postgres in schema public revoke execute on functions from public;
