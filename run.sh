#!/usr/bin/env bash
#
#*************************iSgela task runner.*********************************
#
#   ./run.sh              start the dev server (default)
#   ./run.sh serve [port] start the dev server
#   ./run.sh test [args]  run the suite against in-memory SQLite
#   ./run.sh test-pg      run the same suite against Supabase Postgres
#   ./run.sh check        system checks and unapplied-migration check
#   ./run.sh migrate      apply migrations
#   ./run.sh ci           check + test, no server. Use this in a pipeline.
#   ./run.sh all          check + test, then serve
#
# Exit codes are propagated, so `./run.sh ci` fails the build when anything
# fails. That is the whole point of the file: a green pipeline on a red test
# suite is worse than no pipeline.
#*****************************************************************************
# -e  stop at the first failing command
# -u  a typo in a variable name is an error, not an empty string
# -o pipefail  a failure anywhere in a pipe fails the pipe
set -Eeuo pipefail

cd "$(dirname "$0")"

PORT="${PORT:-1097}"

# --------------------------------------------------------------------------- #
# Output helpers
# --------------------------------------------------------------------------- #

if [ -t 1 ]; then
    BOLD=$(printf '\033[1m'); GREEN=$(printf '\033[32m')
    RED=$(printf '\033[31m'); YELLOW=$(printf '\033[33m'); RESET=$(printf '\033[0m')
else
    BOLD=""; GREEN=""; RED=""; YELLOW=""; RESET=""
fi

banner() {
    # Note the space after echo. Without it, bash looks for a command called
    # echo"=====" and every line of the banner fails silently.
    echo ""
    echo "${BOLD}=========================================================${RESET}"
    echo "${BOLD}  $1${RESET}"
    echo "${BOLD}=========================================================${RESET}"
    echo ""
}

fail() { echo "${RED}error:${RESET} $1" >&2; exit 1; }
warn() { echo "${YELLOW}warning:${RESET} $1" >&2; }

on_error() {
    local exit_code=$?
    echo ""
    echo "${RED}FAILED${RESET} at line ${BASH_LINENO[0]} (exit ${exit_code})" >&2
    exit "${exit_code}"
}
trap on_error ERR

# --------------------------------------------------------------------------- #
# Environment
# --------------------------------------------------------------------------- #

find_python() {
    local candidate
    for candidate in venv .venv env; do
        if [ -x "${candidate}/bin/python" ]; then
            echo "${candidate}/bin/python"
            return 0
        fi
    done
    return 1
}

# The venv's interpreter is called directly rather than sourcing activate.
# It needs no shell state, works the same under cron and CI, and cannot be
# confused by an already-active virtualenv.
if ! PYTHON=$(find_python); then
    fail "No virtual environment found (looked for venv/, .venv/, env/).
       Create one with: python3 -m venv venv && venv/bin/pip install -r requirements.txt"
fi

[ -f manage.py ] || fail "manage.py not found. Run this from the project root."

# A missing .env is only a problem when the environment does not already supply
# the configuration. CI has no .env file and does not need one: the test suite
# provides its own throwaway settings.
if [ ! -f .env ] && [ -z "${SECRET_KEY:-}" ]; then
    warn "no .env file and no SECRET_KEY in the environment.
         The test suite will still run; anything that touches the database will not.
         See the Setup section of README.md."
fi

# --------------------------------------------------------------------------- #
# Tasks
# --------------------------------------------------------------------------- #

cmd_check() {
    banner "CHECKS: configuration and migration state"
    "${PYTHON}" manage.py check
    # --check exits non-zero when a model change has no migration, which is the
    # single easiest thing to forget before pushing.
    "${PYTHON}" manage.py makemigrations --check --dry-run
    echo "${GREEN}configuration and migrations are in order${RESET}"
}

cmd_test() {
    banner "TESTS: in-memory SQLite, no network"
    "${PYTHON}" manage.py test "$@"
    echo ""
    echo "${GREEN}tests passed${RESET}"
}

cmd_test_pg() {
    banner "TESTS: Supabase Postgres"
    # --keepdb is required: Supabase's pooler holds a session open, which stops
    # Django from dropping the test database afterwards.
    TEST_ON_POSTGRES=True "${PYTHON}" manage.py test --keepdb "$@"
    echo ""
    echo "${GREEN}tests passed against Postgres${RESET}"
}

cmd_migrate() {
    banner "MIGRATE"
    "${PYTHON}" manage.py migrate
}

cmd_serve() {
    local port="${1:-$PORT}"
    banner "SERVER: http://127.0.0.1:${port}/"
    # exec so signals reach Django directly and Ctrl-C stops the server cleanly
    # rather than being caught by this script.
    exec "${PYTHON}" manage.py runserver "${port}"
}

cmd_ci() {
    cmd_check
    cmd_test
    banner "CI PASSED"
}

cmd_all() {
    cmd_check
    cmd_test
    cmd_test_pg
    cmd_serve
}

usage() {
    # Lines 2-17 are the usage block at the top of this file.
    sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'
}

# --------------------------------------------------------------------------- #
# Dispatch
# --------------------------------------------------------------------------- #

command="${1:-serve}"
[ $# -gt 0 ] && shift

case "${command}" in
    serve|server|run)  cmd_serve "$@" ;;
    test)              cmd_test "$@" ;;
    test-pg|test-postgres) cmd_test_pg "$@" ;;
    check)             cmd_check ;;
    migrate)           cmd_migrate ;;
    ci)                cmd_ci ;;
    all)               cmd_all ;;
    -h|--help|help)    usage ;;
    *)                 echo "unknown command: ${command}" >&2; echo "" >&2; usage >&2; exit 64 ;;
esac
