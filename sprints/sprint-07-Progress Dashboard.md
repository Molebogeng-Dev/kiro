# Sprint 7 — Progress Dashboard & Polish

## Overview

Sprint 7 ties the app together: a teacher-facing dashboard that rolls
up marking, attendance, and assignment data per student, so a teacher
sees the full picture instead of isolated data scattered across
separate pages. This sprint is read-only aggregation — it collects no
new data, it only surfaces what already exists.

## Goals

- Group marks meaningfully by normalizing the existing `subject` field
  and backfilling old records
- Let any teacher view any student's rollup — same reasoning as
  attendance's any-teacher access, not marking's per-teacher ownership
- Surface a transparent, rule-based "needs attention" flag so a
  teacher can spot who needs help without opening every student
  individually
- Do a light consistency pass across existing pages, without turning
  it into a redesign

## What was built

- **`Memorandum.subject`** — free-text field (e.g. "Mathematics"),
  normalized on save (stripped, title-cased) so marks group
  consistently; existing memorandums backfilled with a sensible
  default rather than left blank
- **Access** — any teacher can view the student list and any
  student's rollup, matching Sprint 5's attendance reasoning: without
  class/roster structure, a whole-school view is more useful than one
  scoped to "papers I personally marked"
- **Student list (dashboard entry point)** — every student, with a
  scannable "needs attention" flag driven by simple, documented
  threshold rules (low average mark, low recent attendance, multiple
  missed assignments) — no AI call, cheap and explainable
- **Student rollup (detail view)** — per-subject average marks
  (grouped by `Memorandum.subject`, covering both teacher-marked exams
  and student-submitted homework), an attendance summary (reusing the
  existing `Attendance` model regardless of `method`), and assignment
  completion (submitted vs. total, using the existing
  `Paper.assignment` link); the reasons behind a "needs attention"
  flag are visible on this page, not just asserted
- **Polish pass** — spacing, empty states, and wording consistency
  across Sprints 1–6's pages; no new features, no redesign

## Implementation notes

Decisions and two things I want to flag explicitly, since the brief
asked for them but the codebase already had them.

- **`subject` already existed — I enhanced it, I didn't add it.** The
  field has been on `Memorandum` since the marking engine (used in the
  prompt so the model reads subject notation). What was missing, and
  what this sprint added, is the normalization on save (strip +
  title-case), the `"General"` default, and a migration that
  backfills existing rows through the *same* `normalize_subject`
  function the model uses — so the one-off backfill and ongoing saves
  can never drift apart.

- **"Upload a picture" for a memorandum/assignment already exists —
  flagging rather than duplicating.** Both create forms already let a
  teacher photograph a memorandum or an assignment's instructions and
  have the vision model transcribe it into the form to review before
  saving (the Sprint 3 transcription feature, `marking/transcription.py`).
  That is exactly "upload a picture when uploading a memorandum /
  assignment", so I did not build a second path. If what was wanted is
  different — storing the *image itself* as an attachment on the
  memorandum/assignment rather than transcribing it to text — that is a
  new feature (a field, storage, and display) and I have left it out
  pending a steer, because it would change how marking reads a
  memorandum.

- **A "school day" is a day attendance was taken.** There is no school
  calendar in the MVP and absence is implicit (no row means not marked
  present). So the attendance window is the most recent 10 *distinct
  dates on which any attendance was recorded*, and a learner's rate is
  how many of those they were present. This is self-calibrating and
  explainable, and it avoids penalising a learner for weekends and
  holidays that were never school days. The rollup page states the
  window it used.

- **The flag is rule-based and its reasons are returned as text.** All
  three thresholds are constants in `core/progress.py`; each rule
  emits a specific sentence ("Average mark 35% is below 50%") that the
  list and the rollup show verbatim, so "why is this flagged?" is
  always answerable. A rule stays silent when it has no data to judge
  on (no marks yet, no attendance taken).

- **Aggregation is separated from the views.** `core/progress.py` does
  all the rolling-up as plain functions over the models, so the flag
  logic and the averages are tested directly, without a browser. The
  list builds every learner's rollup from a handful of bulk queries
  rather than a query set per learner.

- **Assignment completion is against every assignment, no roster.**
  "Submitted 2 of 40" reflects the standing no-class/roster limitation
  (an assignment is visible to every learner), consistent with the
  student dashboard. Only *past-due, unsubmitted* assignments feed the
  flag, so a learner is never flagged for work that is not due yet.

- **Thresholds are honest defaults, and I'd tune them on real data.**
  50% / 80% / 2-missed are reasonable starting lines, not validated
  pedagogy. They are one-line changes and the reason strings follow
  automatically.

## Definition of done

- [x] `Memorandum.subject` added, normalized, and backfilled on
      existing records
- [x] Any teacher can access the student list and any student's
      rollup
- [x] "Needs attention" flag uses documented, transparent threshold
      rules, no AI call
- [x] Rollup shows per-subject marks, attendance summary, and
      assignment completion, with the flag's reasoning visible
- [x] All dashboard views reject non-teacher roles
- [x] Tests cover subject normalization/backfill, rollup aggregation,
      flag threshold logic (both triggering and non-triggering
      cases), and multi-teacher access
- [x] New test module added to `run.sh`'s sprint mapping

## Out of scope for this sprint

Any new data collection, changes to marking/attendance/assignment
logic itself, parent-facing changes (already covered by Sprint 6), a
full visual redesign.

## Known limitations

- **No class/roster structure yet** (carried over from Sprints 3–6) —
  the dashboard shows every student to every teacher, not a specific
  class.
- **"Needs attention" thresholds are simple defaults, not
  pedagogically validated.** They flag a pattern worth a teacher's
  attention, not a diagnosis — worth tuning with real usage rather
  than treated as authoritative out of the box.
- All previously documented known limitations (parent-linking,
  marking accuracy, facial-matching accuracy, WhatsApp as a
  demo-only channel) remain unchanged by this sprint.

## Next up — Sprint 8

README finalization, demo video recording, a fresh-clone install
test, and submission before the August 23 deadline.