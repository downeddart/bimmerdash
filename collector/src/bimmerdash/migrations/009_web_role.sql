-- A least-privilege database role for the web UI. It reads everything but writes only the
-- tables the UI owns (login, sessions, settings, places), so a bug in the web app can't
-- change recorded data or run server-side programs. Roles are cluster-wide, so create it
-- only if missing. It can't log in until the collector sets a password
-- (BIMMERDASH_WEB_DB_PASSWORD); until then the web UI keeps using the main role.
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'bimmerdash_web') THEN
        CREATE ROLE bimmerdash_web NOLOGIN;
    END IF;
END
$$;

GRANT USAGE ON SCHEMA public TO bimmerdash_web;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO bimmerdash_web;
GRANT INSERT, UPDATE, DELETE ON web_users, web_sessions, settings, places TO bimmerdash_web;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO bimmerdash_web;
-- Tables added by later migrations are readable too.
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO bimmerdash_web;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO bimmerdash_web;
