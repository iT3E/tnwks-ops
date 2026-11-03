-- Move every object a database's tables live in from one role to another.
-- Inputs (psql -v): old_role, new_role. Runs in the target database AS THE OLD
-- ROLE, no superuser: an owner may give an object to any role it can SET ROLE
-- to, and cnpg makes the old role a member of the new one (managed.roles
-- `inRoles`). The same membership keeps the app working until its own PR
-- switches it to the new role.
-- Idempotent: a second run finds nothing to move.
--
-- Deliberately NOT `REASSIGN OWNED BY`: that also hands over every *other*
-- database and shared object the old role owns (`app` owns several databases),
-- so the first app migrated would take all of them.
\set ON_ERROR_STOP on
select set_config('reassign.old', :'old_role', false), set_config('reassign.new', :'new_role', false);
DO $do$
DECLARE
  o   text := current_setting('reassign.old');
  n   text := current_setting('reassign.new');
  r   record;
  cnt int  := 0;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = n) THEN
    RAISE EXCEPTION 'role % does not exist yet (cnpg managed.roles has not reconciled)', n;
  END IF;
  IF current_user <> o THEN
    RAISE EXCEPTION 'run this as % (connected as %)', o, current_user;
  END IF;
  IF NOT pg_has_role(o, n, 'SET') THEN
    RAISE EXCEPTION '% is not yet a member of % (cnpg inRoles has not reconciled)', o, n;
  END IF;
  -- The database itself is handed over by cnpg (Database CR `owner`), which
  -- runs as superuser: a non-superuser also needs CREATEDB for that.

  FOR r IN SELECT nspname FROM pg_namespace
           WHERE nspowner = o::regrole AND nspname NOT LIKE 'pg\_%' AND nspname <> 'information_schema' LOOP
    EXECUTE format('ALTER SCHEMA %I OWNER TO %I', r.nspname, n); cnt := cnt + 1;
  END LOOP;

  -- Tables first: their indexes and owned (serial/identity) sequences follow.
  FOR r IN SELECT c.oid::regclass AS obj, c.relkind FROM pg_class c JOIN pg_namespace s ON s.oid = c.relnamespace
           WHERE c.relowner = o::regrole AND c.relkind IN ('r','p','v','m','f')
             AND s.nspname NOT IN ('pg_catalog','information_schema') AND s.nspname NOT LIKE 'pg\_toast%'
             AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid = c.oid AND d.deptype = 'e') LOOP
    EXECUTE format('ALTER %s %s OWNER TO %I',
      CASE r.relkind WHEN 'v' THEN 'VIEW' WHEN 'm' THEN 'MATERIALIZED VIEW' WHEN 'f' THEN 'FOREIGN TABLE' ELSE 'TABLE' END,
      r.obj, n); cnt := cnt + 1;
  END LOOP;

  FOR r IN SELECT c.oid::regclass AS obj FROM pg_class c JOIN pg_namespace s ON s.oid = c.relnamespace
           WHERE c.relowner = o::regrole AND c.relkind = 'S' AND s.nspname NOT IN ('pg_catalog','information_schema')
             AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid = c.oid AND d.deptype IN ('e','a','i')) LOOP
    EXECUTE format('ALTER SEQUENCE %s OWNER TO %I', r.obj, n); cnt := cnt + 1;
  END LOOP;

  FOR r IN SELECT p.oid::regprocedure AS obj FROM pg_proc p JOIN pg_namespace s ON s.oid = p.pronamespace
           WHERE p.proowner = o::regrole AND s.nspname NOT IN ('pg_catalog','information_schema')
             AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid = p.oid AND d.deptype = 'e') LOOP
    EXECUTE format('ALTER ROUTINE %s OWNER TO %I', r.obj, n); cnt := cnt + 1;
  END LOOP;

  -- Standalone enum, domain, range and composite types (not a table's row type).
  FOR r IN SELECT t.oid::regtype AS obj, t.typtype FROM pg_type t JOIN pg_namespace s ON s.oid = t.typnamespace
           WHERE t.typowner = o::regrole AND t.typtype IN ('e','d','r','c')
             AND s.nspname NOT IN ('pg_catalog','information_schema')
             AND (t.typtype <> 'c' OR EXISTS (SELECT 1 FROM pg_class c WHERE c.oid = t.typrelid AND c.relkind = 'c'))
             AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid = t.oid AND d.deptype = 'e') LOOP
    EXECUTE format('ALTER %s %s OWNER TO %I', CASE r.typtype WHEN 'd' THEN 'DOMAIN' ELSE 'TYPE' END, r.obj, n); cnt := cnt + 1;
  END LOOP;

  IF EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace s ON s.oid = c.relnamespace
             WHERE c.relowner = o::regrole AND s.nspname NOT IN ('pg_catalog','information_schema') AND s.nspname NOT LIKE 'pg\_toast%'
               AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid = c.oid AND d.deptype = 'e')) THEN
    RAISE EXCEPTION 'relations in % are still owned by %', current_database(), o;
  END IF;
  RAISE NOTICE 'reassign: % objects in database % moved from % to %', cnt, current_database(), o, n;
END
$do$;
