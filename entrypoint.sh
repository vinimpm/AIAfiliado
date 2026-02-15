#!/bin/bash
set -e

# Ensure PYTHONPATH includes /app/src
export PYTHONPATH="/app/src:${PYTHONPATH:-}"

# Railway PostgreSQL may provide URL under different variable names
if [ -z "$DATABASE_URL" ]; then
    if [ -n "$DATABASE_PUBLIC_URL" ]; then
        export DATABASE_URL="$DATABASE_PUBLIC_URL"
    elif [ -n "$POSTGRES_URL" ]; then
        export DATABASE_URL="$POSTGRES_URL"
    fi
fi

# Railway/Heroku use postgres:// but SQLAlchemy 2.0 requires postgresql://
if [[ "$DATABASE_URL" == postgres://* ]]; then
    export DATABASE_URL="postgresql://${DATABASE_URL#postgres://}"
fi

echo "DATABASE_URL host: $(echo "$DATABASE_URL" | sed 's|.*@\(.*\)/.*|\1|')"
echo "PYTHONPATH: $PYTHONPATH"

# Run migrations from /app where alembic.ini lives
echo "Running database migrations..."
cd /app && alembic upgrade head
echo "Migrations complete."

# Return to /app/src for the application
cd /app/src
exec "$@"
