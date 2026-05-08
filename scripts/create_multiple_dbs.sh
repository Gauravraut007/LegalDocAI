#!/bin/bash
# Bootstraps multiple databases on first Postgres init.
# Triggered by POSTGRES_MULTIPLE_DATABASES (comma-separated).
# Mounted into postgres container at /docker-entrypoint-initdb.d/.
set -euo pipefail

create_database() {
    local database="$1"
    echo "  Creating database '$database' (if not exists)"
    psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" \
        -tc "SELECT 1 FROM pg_database WHERE datname = '$database'" \
        | grep -q 1 \
        || psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" \
            -c "CREATE DATABASE \"$database\";"
    psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" \
        -c "GRANT ALL PRIVILEGES ON DATABASE \"$database\" TO \"$POSTGRES_USER\";"
}

if [ -n "${POSTGRES_MULTIPLE_DATABASES:-}" ]; then
    echo "Multiple database creation requested: $POSTGRES_MULTIPLE_DATABASES"
    IFS=',' read -ra DBS <<< "$POSTGRES_MULTIPLE_DATABASES"
    for db in "${DBS[@]}"; do
        db_trimmed="$(echo "$db" | xargs)"
        [ -n "$db_trimmed" ] && create_database "$db_trimmed"
    done
    echo "Multiple databases created."
fi
