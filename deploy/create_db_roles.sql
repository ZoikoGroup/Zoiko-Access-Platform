-- Creates the two database logins a real deployment needs (docs/DEPLOYMENT.md).
--
--   owner  (default zoiko_owner)  owns the tables and runs migrations. Never used by the running app.
--   app    (default zoiko_app)    what the web server and the worker connect as. It cannot skip
--                                 row-level security (no superuser, no BYPASSRLS), cannot create
--                                 anything, and starts with NO rights: `manage.py db_grants --apply`
--                                 gives it exactly the list in foundation/dbgrants.py.
--
-- Run ONCE, as a PostgreSQL superuser, against the server that holds the database:
--
--   psql -h <host> -U <superuser> -d postgres \
--        -v dbname=zoiko -v owner_password='...' -v app_password='...' \
--        -f deploy/create_db_roles.sql
--
-- Optional: -v owner_role=<name> -v app_role=<name> to use other login names.
-- Pick two long random passwords. Do not put them in this file, in git, or in a shell history you
-- share. The database named by :dbname must already exist (empty).

\set ON_ERROR_STOP on

\if :{?owner_role}
\else
  \set owner_role zoiko_owner
\endif
\if :{?app_role}
\else
  \set app_role zoiko_app
\endif

CREATE ROLE :"owner_role" LOGIN PASSWORD :'owner_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE :"app_role" LOGIN PASSWORD :'app_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;

-- The owner owns the database and its schema, so migrations can create tables.
ALTER DATABASE :"dbname" OWNER TO :"owner_role";
REVOKE ALL ON DATABASE :"dbname" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"dbname" TO :"owner_role", :"app_role";

\connect :"dbname"

ALTER SCHEMA public OWNER TO :"owner_role";
-- Nobody but the owner may create anything in the schema (the app only uses what the owner made).
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO :"app_role";
