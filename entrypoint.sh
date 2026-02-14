#!/bin/bash
set -e

# Railway PostgreSQL may provide URL under different variable names
if [ -z "$DATABASE_URL" ]; then
    if [ -n "$DATABASE_PUBLIC_URL" ]; then
        export DATABASE_URL="$DATABASE_PUBLIC_URL"
    elif [ -n "$POSTGRES_URL" ]; then
        export DATABASE_URL="$POSTGRES_URL"
    fi
fi

echo "DATABASE_URL is set: $([ -n "$DATABASE_URL" ] && echo 'yes' || echo 'NO - migrations will fail')"

echo "Running database migrations..."
alembic upgrade head
echo "Migrations complete."

exec "$@"
