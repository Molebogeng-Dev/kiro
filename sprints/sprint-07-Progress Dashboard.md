# Sprint 7 — Progress Dashboard & Polish

## Overview

Sprint 7 ties the app together: a teacher-facing dashboard that rolls
up marking, attendance, and assignment data per student, so a teacher
sees the full picture instead of isolated data scattered across
separate pages. This sprint is read-only aggregation — it collects no
new data, it only surfaces what already exists.

## Goals

- Group marks meaningfully by adding a lightweight `subject` field
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