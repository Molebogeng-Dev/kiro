#!/usr/bin/env bash
#
# docker.sh — build and run the iSgela container image.
#
# A thin, friendly wrapper over `docker compose` for the demo stack defined in
# docker-compose.yml. Kept separate from run.sh (which is for local, non-Docker
# development) so the two never get confused.
#
#   ./docker.sh build     # build the image
#   ./docker.sh up        # start it in the background
#   ./docker.sh run       # build then up (the usual one-shot)
#   ./docker.sh logs      # follow the container logs
#   ./docker.sh ps        # show status
#   ./docker.sh shell     # open a shell inside the running container
#   ./docker.sh down      # stop and remove the container
#   ./docker.sh restart   # down then up
#
# Configuration is read from .env (DATABASE_URL, SECRET_KEY, OpenRouter/Twilio
# keys, etc.). Override the published port with HOST_PORT, e.g.
#   HOST_PORT=9000 ./docker.sh up

set -euo pipefail

cd "$(dirname "$0")"

HOST_PORT="${HOST_PORT:-8000}"
export HOST_PORT

# Stop Compose auto-loading the secret .env for interpolation: it would try to
# expand a literal "$" inside SECRET_KEY and print noisy warnings. The container
# still receives .env through a read-only mount (see docker-compose.yml), read
# directly by python-decouple. Compose reads ${HOST_PORT} and friends from the
# shell environment instead.
export COMPOSE_ENV_FILES="${COMPOSE_ENV_FILES:-/dev/null}"

# --------------------------------------------------------------------------- #
# Preconditions
# --------------------------------------------------------------------------- #

if ! command -v docker >/dev/null 2>&1; then
    echo "docker is not installed or not on PATH." >&2
    exit 1
fi

# Prefer Compose v2 ("docker compose"); fall back to the legacy "docker-compose".
if docker compose version >/dev/null 2>&1; then
    COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE=(docker-compose)
else
    echo "docker compose (v2) or docker-compose is required." >&2
    exit 1
fi

require_env() {
    if [ ! -f .env ]; then
        echo "No .env file found in $(pwd)." >&2
        echo "iSgela needs SECRET_KEY and DATABASE_URL (Supabase) at minimum." >&2
        exit 1
    fi
}

url() {
    echo "iSgela is starting on http://localhost:${HOST_PORT}"
    echo "Follow logs with: ./docker.sh logs"
}

# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #

cmd="${1:-run}"

case "$cmd" in
    build)
        "${COMPOSE[@]}" build
        ;;
    up)
        require_env
        "${COMPOSE[@]}" up -d
        url
        ;;
    run)
        require_env
        "${COMPOSE[@]}" up -d --build
        url
        ;;
    logs)
        "${COMPOSE[@]}" logs -f
        ;;
    ps)
        "${COMPOSE[@]}" ps
        ;;
    shell)
        "${COMPOSE[@]}" exec web /bin/sh
        ;;
    down)
        "${COMPOSE[@]}" down
        ;;
    restart)
        "${COMPOSE[@]}" down
        require_env
        "${COMPOSE[@]}" up -d
        url
        ;;
    *)
        echo "Usage: ./docker.sh {build|up|run|logs|ps|shell|down|restart}" >&2
        exit 1
        ;;
esac
