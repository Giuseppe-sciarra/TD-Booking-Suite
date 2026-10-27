#!/bin/sh
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --set=app_pass="$APP_DB_PASSWORD" <<'SQL'
CREATE ROLE tdbooking_app LOGIN PASSWORD :'app_pass' NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
GRANT CONNECT ON DATABASE tdbooking TO tdbooking_app;
SQL
