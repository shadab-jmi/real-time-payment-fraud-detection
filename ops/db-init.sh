#!/bin/sh
set -eu
psql -X -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
\getenv m DB_MIGRATOR_PASSWORD
\getenv r DB_RUNTIME_PASSWORD
\getenv v DB_READER_PASSWORD
CREATE ROLE fraud_migrator LOGIN PASSWORD :'m';
CREATE ROLE fraud_runtime LOGIN PASSWORD :'r';
CREATE ROLE fraud_reader LOGIN PASSWORD :'v';
REVOKE ALL ON DATABASE payments FROM PUBLIC;
ALTER DATABASE payments OWNER TO fraud_migrator;
GRANT CONNECT ON DATABASE payments TO fraud_runtime, fraud_reader;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
SQL
