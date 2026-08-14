#!/bin/sh
#
# Container entrypoint.
#
# Migrations and bucket creation are opt-in rather than automatic. Running
# migrations on container start breaks as soon as there is more than one
# replica, because they all race to apply the same migration. For a single
# demo container it is convenient, so it is available behind a flag.

set -eu

if [ "${RUN_MIGRATIONS_ON_START:-0}" = "1" ]; then
    echo "entrypoint: applying migrations"
    python manage.py migrate --noinput
fi

if [ "${ENSURE_STORAGE_BUCKET_ON_START:-0}" = "1" ]; then
    echo "entrypoint: ensuring the storage bucket exists"
    # Not fatal: the app still serves everything that does not touch storage.
    python manage.py ensure_storage_bucket || \
        echo "entrypoint: WARNING could not ensure the storage bucket; uploads will fail"
fi

exec "$@"
