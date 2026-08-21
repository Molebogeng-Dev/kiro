# Sprint 8b — School-Scoped Access Retrofit

## Overview

Sprint 8a put a `school` on every account but nothing read it yet. This sprint
finishes the job: every view that said "any teacher, any student" now says "any
teacher, any student **at the same school**." It is a retrofit, not new features
— the six views below gained a school filter, and student registration gained a
grade-range check. Nothing was rebuilt or restyled.

It also bundles the housekeeping the user asked for alongside the retrofit: a
full app health check, a `docker-compose.yml`, and a `docker.sh` build/run
script.

## What was re-scoped

All six through `accounts/scoping.py` (see the design note on `None` below):

1. **Attendance roll-call** — the primary (1–7) list is scoped to
   `request.user.school`; a tampered cross-school id in the POST is filtered out
   as before, now against the school's own list.
2. **Attendance check-in / enrollment** — the secondary (8–12) enrollment and
   fallback pickers are scoped, and the facial match only compares against faces
   enrolled *at this school*, so another school's student can never be checked in
   here.
3. **Attendance history** — today's present list is scoped to the school.
4. **Progress dashboard** — the student list and each rollup are scoped. The
   rollup's *denominators* are scoped too (the attendance window counts school
   days at this school; assignment completion is measured against this school's
   assignments), so no cross-school count leaks into a learner's numbers.
5. **Marking student picker** — the "whose paper is this?" dropdown lists only
   the teacher's school. Because a `ModelChoiceField` validates the submitted id
   against its queryset, a tampered cross-school id is also rejected on POST.
6. **Classroom assignments and study materials** — the student-facing lists,
   the material detail, and the homework-submission page are scoped to the
   student's own school (matched on `created_by__school`). The student
   dashboard's "to-do" and "has materials" counts were scoped to match.

**Registration:** a student's `grade` is now validated against the selected
school's `min_grade`–`max_grade`, so a grade-3 learner cannot register at a
secondary-only school. Clear error, no account created.

## Implementation notes

- **The `None` guard is the crux, and it lives in one place.** A bare
  `filter(school=request.user.school)` is a trap: when `user.school` is `None`
  (every pre-8a account), it matches *every* school-less row, so a teacher with
  no school would "share" a school with every unassigned student. So all scoping
  goes through `accounts/scoping.py`, whose rule is **`None` scopes to nothing**
  (`scope_by_school` returns `queryset.none()` for a `None` school). This is
  exactly the guard the brief asked me to flag, and it has its own test.

- **Flag — pre-8a accounts are now scoped out until assigned a school.** The flip
  side of the `None` guard: the three legacy demo accounts on the live database
  (one teacher, one student, one parent, all `school=None`) will see empty
  scoped views until they are given a school (via the admin, or by re-registering
  with a code). This is correct behaviour, not a bug, but it is user-visible, so
  it is called out in the README and worth doing before a demo.

- **Flag — I scoped the progress rollup's denominators too, which is slightly
  more than "only include this school's students."** Once classroom assignments
  became school-scoped (view 6), leaving the progress dashboard's assignment
  *total* global would have shown a learner "2 of 50" counting other schools'
  assignments — a cross-school count leak. So the attendance window and the
  assignment total in a rollup are scoped to the school as well. If you'd rather
  keep those global, it is a small revert, but scoping them is the consistent
  reading of "a teacher cannot see another school's data."

- **Scoping the picker also enforces the boundary on submit.** For the marking
  student picker (and the attendance fallback), scoping the `ModelChoiceField`
  queryset means a hand-crafted POST carrying another school's student id fails
  validation — the scoping is the enforcement, not just a display filter. Tested
  directly.

- **Existing tests needed a school fixture, not behavioural changes.** The
  shared test-support helpers now co-locate the users they build in one default
  school, so a teacher and the learners they mark/roll-call/see are at the same
  school. Cross-school isolation is proved by a dedicated new module that builds
  two schools and checks each view. No test's underlying behaviour changed.

## The health check, docker-compose, and docker.sh

- **Health check.** Full suite green (450 tests), `manage.py check` clean, no
  pending migrations (this sprint changed no models), and a live scoping check
  against Supabase confirmed the two-school isolation and the `None` guard. The
  one real finding was the pre-8a school-less accounts above.
- **`docker-compose.yml`** builds the app image from the existing `Dockerfile`
  and runs it against Supabase (no DB service — Supabase is external). `.env` is
  **mounted read-only** rather than passed via `env_file`, because Compose
  interpolates `env_file` values and our `SECRET_KEY` contains a literal `$` that
  would be silently mangled; the app reads the mounted file directly with
  `python-decouple`. `docker.sh` sets `COMPOSE_ENV_FILES=/dev/null` so Compose
  does not auto-load the secret `.env` for interpolation either.
- **`docker.sh`** is a small `docker compose` wrapper (`build`, `up`, `run`,
  `logs`, `ps`, `shell`, `down`, `restart`), kept separate from `run.sh`.
  Compose config validates; the image build itself was not run in this
  environment (the Docker daemon socket was not accessible to the sandbox user),
  so give `./docker.sh run` a try and shout if the build surfaces anything.

## Definition of done

- [x] All six views filter by `request.user.school`, not globally
- [x] A teacher at one school cannot see, mark, roll-call, or view dashboard data
      for a student at another school — proven by test
- [x] Assignments and study materials are not visible across schools
- [x] Student registration rejects a grade outside the school's range
- [x] Previously passing tests still pass, updated only for school fixtures
- [x] New tests prove cross-school isolation for each of the six views (a
      same-school success and a cross-school 404/empty)
- [x] The `None`-school guard is explicit and tested
- [x] New test module (`core.tests_school_scoping`) added to the run.sh mapping

## Out of scope for this sprint

New school-admin views, any change to how schools or codes are created (8a), and
any UI redesign — this was access-control plumbing.

## Known limitations

- **Pre-8a accounts need a school assigned** before the scoped views work for
  them (the `None` guard, above).
- **No code rotation / self-declared join** (carried from 8a) — unchanged.
- **Grade is validated at registration only** — an admin editing a learner's
  grade in the Django admin is not re-checked against the school range.
