#!/usr/bin/env bash
#
#*************************iSgela task runner.*********************************
#
#   ./run.sh              start the dev server (default)
#   ./run.sh serve [port] start the dev server
#   ./run.sh test [args]  run the suite against in-memory SQLite
#   ./run.sh test-pg      run the same suite against Supabase Postgres
#   ./run.sh sprint <n>    run one sprint's tests (in-memory SQLite)
#   ./run.sh sprint-pg <n> run one sprint's tests against Supabase Postgres
#   ./run.sh sprints       run every sprint's tests, one labelled group at a time
#   ./run.sh sprints-pg    the same, against Supabase Postgres
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

# --------------------------------------------------------------------------- #
# Per-sprint test grouping
# --------------------------------------------------------------------------- #
#
# The suite grows every sprint, and "289 tests OK" hides which sprint a failure
# belongs to. These commands run the tests one sprint at a time, with a banner
# and a per-sprint pass/fail summary, so a regression points straight at the
# sprint that owns it. The mapping of test modules to sprints lives here, in one
# readable place; each module below belongs to exactly one sprint.

LATEST_SPRINT=5

sprint_labels() {
    case "$1" in
        1) echo "accounts config.tests" ;;
        2) echo "core.tests marking.tests.test_parsing marking.tests.test_openrouter marking.tests.test_engine marking.tests.test_images marking.tests.test_views" ;;
        3) echo "marking.tests.test_teacher_portal marking.tests.test_transcription classroom.tests.test_views classroom.tests.test_transcription" ;;
        4) echo "classroom.tests.test_student_portal marking.tests.test_student_results" ;;
        5) echo "attendance" ;;
        *) return 1 ;;
    esac
}

sprint_title() {
    case "$1" in
        1) echo "Foundation — accounts, roles, database config" ;;
        2) echo "AI Scan & Mark engine" ;;
        3) echo "Teacher portal" ;;
        4) echo "Student portal" ;;
        5) echo "Attendance (grade-based)" ;;
        *) echo "Sprint $1" ;;
    esac
}

# The concise runner reports only the tests that fail, error, or are skipped,
# and stays silent about passes. Defined in config/test_runner.py.
SPRINT_RUNNER="config.test_runner.ConciseTestRunner"

_db_label() { [ "$1" = "pg" ] && echo "Supabase Postgres" || echo "in-memory SQLite"; }

# Run one sprint's tests. $1 = sprint number, $2 = mode (sqlite|pg); any further
# arguments pass through to manage.py test. Returns the test command's exit code.
_sprint_tests() {
    local n="$1" mode="$2"; shift 2
    local labels; labels=$(sprint_labels "${n}")
    if [ "${mode}" = "pg" ]; then
        # --keepdb: Supabase's pooler blocks the test-database teardown.
        TEST_ON_POSTGRES=True "${PYTHON}" manage.py test --keepdb \
            --testrunner "${SPRINT_RUNNER}" ${labels} "$@"
    else
        "${PYTHON}" manage.py test --testrunner "${SPRINT_RUNNER}" ${labels} "$@"
    fi
}

# One sprint. Fails hard (via the ERR trap) if its tests do not pass.
_run_sprint() {
    local mode="$1"; shift
    [ $# -ge 1 ] || fail "Which sprint? For example: ./run.sh sprint 4"
    local n="$1"; shift
    sprint_labels "${n}" >/dev/null 2>&1 || fail "Unknown sprint '${n}' (1 to ${LATEST_SPRINT})."

    banner "SPRINT ${n} — $(sprint_title "${n}")  ($(_db_label "${mode}"))"
    _sprint_tests "${n}" "${mode}" "$@"
    echo ""
    echo "${GREEN}Sprint ${n} passed${RESET}"
}

cmd_sprint()    { _run_sprint sqlite "$@"; }
cmd_sprint_pg() { _run_sprint pg "$@"; }

# Every sprint in turn. Records failures instead of aborting so each sprint gets
# to report, prints a summary, and exits non-zero if any sprint failed.
_run_sprints() {
    local mode="$1"
    # A string, not an array, so `set -u` stays happy when nothing has failed.
    local failed="" n

    for n in $(seq 1 "${LATEST_SPRINT}"); do
        banner "SPRINT ${n} — $(sprint_title "${n}")  ($(_db_label "${mode}"))"
        # Inside an `if` so a failing sprint is recorded, not fatal.
        if _sprint_tests "${n}" "${mode}"; then
            echo "${GREEN}Sprint ${n}: passed${RESET}"
        else
            echo "${RED}Sprint ${n}: FAILED${RESET}"
            failed="${failed} ${n}"
        fi
    done

    banner "SPRINT TEST SUMMARY  ($(_db_label "${mode}"))"
    for n in $(seq 1 "${LATEST_SPRINT}"); do
        case " ${failed} " in
            *" ${n} "*) echo "  ${RED}Sprint ${n}: FAILED${RESET}" ;;
            *)          echo "  ${GREEN}Sprint ${n}: passed${RESET}" ;;
        esac
    done
    echo ""

    if [ -n "${failed# }" ]; then
        fail "Sprints with failures:${failed}"
    fi
    echo "${GREEN}All ${LATEST_SPRINT} sprints passed.${RESET}"
}

cmd_sprints()    { _run_sprints sqlite; }
cmd_sprints_pg() { _run_sprints pg; }

usage() {
    # Lines 2-21 are the usage block at the top of this file.
    sed -n '2,21p' "$0" | sed 's/^# \{0,1\}//'
}

# --------------------------------------------------------------------------- #
# Dispatch
# --------------------------------------------------------------------------- #

command="${1:-serve}"
[ $# -gt 0 ] && shift

case "${command}" in
    # app info
    -h|--help|help)    usage ;;

    # app
    serve|server|run)  cmd_serve "$@" ;;

    # overall test
    test)              cmd_test "$@" ;;
    test-pg|test-postgres) cmd_test_pg "$@" ;; # server

    # individual iteration test
    sprint)            cmd_sprint "$@" ;;
    sprints)           cmd_sprints ;;
    sprint-pg|sprint-postgres) cmd_sprint_pg "$@" ;; # server
    sprints-pg|sprints-postgres) cmd_sprints_pg ;; # server

    # sync shema with database
    migrate)           cmd_migrate ;;

    # local continuous integration
    ci)                cmd_ci ;;

    # configuration and migration state check. RUN BEFORE PUSH
    check)             cmd_check ;;

    # running everything at once
    all)               cmd_all ;;

    *)                 echo "unknown command: ${command}" >&2; echo "" >&2; usage >&2; exit 64 ;;
esac
