#!/bin/sh
# Seed teams (idempotent — only inserts missing) and create the schema, then run the bot.
set -e

if [ "${SEED_ON_START:-true}" = "true" ]; then
    echo "Seeding teams / initializing schema…"
    wcsweep-seed || echo "seed step failed (continuing)"
fi

exec "$@"
