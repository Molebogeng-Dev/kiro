# Sprint 8a — Schools, School Admin & Code-Based Registration

## Overview

This sprint builds the boundary the app has needed since attendance. "Any
teacher can see any student" has been a *global* statement; this sprint puts a
`school` on every account so it can become school-scoped. It builds the school
system, the `school_admin` role, and code-gated registration for all roles.

It deliberately does **not** re-scope the existing views (attendance, the
progress dashboard, the marking student picker, classroom materials) to respect
`User.school`. That retrofit is Sprint 8b, kept separate on purpose — so this
sprint is about getting the school model and the sign-up correctness right, and
8b is about applying it everywhere.

## Goals

- Add a `school_admin` role, gated by the existing `role_required` pattern
- Extend `core.School` with a grade range, a creating admin, and two shared
  join codes
- Add a `TeacherInvite` with a unique, genuinely single-use claim code
- A distinct school-registration flow that creates the admin and the school
  together and sets the admin's own `User.school`
- Extend teacher/student/parent registration to require a school and the right
  code, failing clearly on any mismatch — never a silent misassignment

## What was built

- **`school_admin` role** — added to `Role` alongside teacher/student/parent.
  `User.role`'s `max_length` grew from 10 to 20 to fit `"school_admin"`. A
  `is_school_admin` property and a `ROLE_DASHBOARD_URL_NAMES` entry route it like
  every other role.

- **`core.School` (extended)** — the stub gained `min_grade`/`max_grade` (a
  flexible range, not a rigid primary/secondary enum), `created_by` (the admin),
  and `student_join_code` + `parent_student_join_code`, both generated at
  creation from an unambiguous alphabet (no `0/O`, `1/I/L`) so they are easy to
  read off a slip and type. A `grade_range_label` renders "Grades 1–7" for the
  dashboard.

- **`core.TeacherInvite`** — `school`, `teacher_name` (as the admin typed it, so
  the registering teacher recognises their slot), `assigned_grades`, a unique
  `code`, and nullable `claimed_by`/`claimed_at`. `create_for` mints a unique
  code (retrying on the astronomically unlikely collision); `claim` is the
  race-safe single-use guard (below).

- **School registration** (`/accounts/register/school/`) — a distinct
  `SchoolRegistrationForm` that creates the `User(role="school_admin")` and the
  `School` in one transaction, with "Primary (1–7)" / "Secondary (8–12)" presets
  or a custom range, and points the admin's own `User.school` at the new school.

- **School admin dashboard** (`/school/`) — shows the two join codes to share,
  and a form to list a teacher by name and assigned grade(s), which mints their
  single-use code. Lists every invite with its code and claimed/open status. The
  school is always taken from `request.user.school`, never the request body.

- **Extended `RegistrationForm`** — teacher/student/parent now select a school
  and enter a code. Its role choices are restricted to those three; a
  school_admin only exists through the separate flow. One `code` field, resolved
  by role: a teacher's unclaimed invite, or the school's shared student/parent
  code. On success `User.school` is set; on any mismatch the form fails with a
  clear per-field error and no account is created.

## Implementation notes

- **Single-use claiming is race-safe at the database, not just checked.** The
  form's `clean()` gives the friendly common-case error ("that code is not valid
  or already used"), but the real guard is `TeacherInvite.claim`, a single
  conditional `UPDATE` with `claimed_by__isnull=True` in the filter. Two
  concurrent claims cannot both match the still-open row — the database
  serialises the update and exactly one sees a row-count of 1. The claim runs
  inside the same transaction as the account creation, so a lost race rolls the
  whole registration back: no account is left pointing at a code someone else
  took. This is the "get it right the first time" the brief asked for, rather
  than an application-level read-then-write a double-registration could slip
  through.

- **The admin's own `User.school` is set.** The brief was explicit that every
  role, school_admin included, should be queryable via `request.user.school`.
  So school registration creates the user, the school (with `created_by=user`),
  and then back-links `user.school` — all in one transaction.

- **Flag — the two join codes are ambiguous in the brief, and I made a call.**
  The brief describes `student_join_code` / `parent_student_join_code` three
  ways that do not fully agree: "one, shared between the two", then "student code
  for students, parent code for parents", plus "view the student code
  (automatically updating parent_student_join_code)". I implemented the version
  that satisfies every Definition-of-Done bullet cleanly: **two separate,
  shared, reusable codes** generated at creation — students type one, parents the
  other, neither consumed by use. I did **not** build a coupling where viewing
  one regenerates the other, since that is not in the DoD and the phrasing is
  unclear. If you intended the two codes to be linked (or a single shared code
  for both), say so and it is a small change.

- **Flag — two features the brief asked to "add" already existed.** `User.school`
  and a stub `core.School` were already in place (added speculatively in Sprint
  1), so this sprint *extended* the School rather than creating it, and did not
  need to add the FK. Nothing else changed as a result.

- **School selection is a dropdown, not free-text search.** The brief said
  "select by name"; a `ModelChoiceField` (a select of school names) is reliable
  and typo-proof, and the existing type-to-narrow JS can enhance it. A learner
  picks the school explicitly, so a join code only has to match the chosen
  school — which is why the join codes are not globally unique.

- **Registration ripples were updated, not worked around.** Making school + code
  required broke the Sprint 1/5 registration tests (which posted neither). Those
  helpers were updated to create a school and supply the right code per role, so
  they still test what they were about (roles, grade handling) rather than the
  new fields.

## Definition of done

- [x] `school_admin` role exists and is accepted/rejected at the view level
      consistently with the existing pattern
- [x] A school admin registers a school with a grade range and their own
      `User.school` is set correctly
- [x] A school admin lists a teacher by name and grade, generating a unique,
      single-use code
- [x] Teacher registration requires a valid, unclaimed code for the selected
      school; claiming is atomic and two registrations against one code cannot
      both succeed
- [x] Student and parent registration require the correct shared code; the same
      code is reusable by many without being consumed
- [x] Invalid codes, already-claimed teacher codes, and mismatched school/code
      pairs all fail with clear errors, not silent misassignment
- [x] Tests cover school creation, invite creation and single-use claiming
      (including a double-claim attempt), student/parent valid and invalid
      codes, and that `User.school` ends up set in every successful case
- [x] New test module (`core.tests_schools`) added to the run.sh sprint mapping

## Out of scope for this sprint

Re-scoping any existing view to respect `User.school` — that is Sprint 8b. No
attendance, dashboard, marking, or classroom view was touched.

## Known limitations

- **School join is self-declared via a shared code** — anyone with a school's
  student/parent code can join as that role, and there is no code rotation yet.
  The code gates casual sign-up; it is not strong authentication. Teacher
  invites, being single-use and per-person, are stronger. Mirrors the
  self-declared parent/student link from Sprint 1.
- **Grade is not validated against the school's range at registration** — a
  student can register with any grade 1–12 regardless of the school's
  `min_grade`/`max_grade`. Enforcing that fits naturally with the Sprint 8b
  scoping pass.

## Next up — Sprint 8b

Retrofit the existing views to respect `User.school`: attendance lists, the
progress dashboard's student list, the marking student picker, and classroom
materials all become school-scoped, so "any teacher can see any student" finally
means "any teacher at this school".
