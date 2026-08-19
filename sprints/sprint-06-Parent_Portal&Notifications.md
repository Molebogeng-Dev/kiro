# Sprint 6 — Parent Portal & Notifications

## Overview

Sprint 6 closes the loop for the third role. A parent can log in and see a linked
child's marked work and attendance without catching a teacher in person, and gets
a plain-language WhatsApp message the moment a paper is marked. The theme is
reuse: no forked result display, no second marking engine, no re-implemented
attendance rollup. The parent simply sees the same data the teacher and student
already see, read-only, gated by the `ParentStudentLink` from Sprint 1.

## Goals

- Add `phone_number` to `User`, required and E.164-validated for parents at
  registration (the same student-only pattern as `grade`)
- Build a parent dashboard: linked children, a picker when there is more than
  one, and per-child recent marked papers + attendance
- Enforce access via `ParentStudentLink` at the view level — a parent can never
  see another family's data
- Notify linked parents when a paper is marked: a plain-language summary
  (AI, with a templated fallback) sent via Twilio's WhatsApp sandbox
- Trigger from the marking engine's existing success path, best-effort, so it
  never affects a paper's status or the teacher/student view

## What was built

- **`User.phone_number`** — `CharField(max_length=16, null=True, blank=True)`
  validated against an E.164 regex. Nullable on the model (teachers and students
  have none), required for parents by the registration form, not the database —
  the same split as `grade`. A `whatsapp_address` property returns the
  `whatsapp:+27...` form Twilio expects, or `None`.

- **Parent portal (`core/`)** — three views, parent-only:
  - `/parent/` lists linked children; with exactly one child it redirects
    straight to that child's page rather than showing a picker of one.
  - `/parent/child/<id>/` shows that child's recent marked papers and recent
    attendance (the Sprint 5 `Attendance` rollup, untouched), plus a switcher
    when the parent has more than one child.
  - `/parent/paper/<id>/` shows one marked paper, reusing the shared
    `marking/_result_body.html` partial the teacher and student already use.
  Parent views live in `core` alongside the other role dashboards, not a new
  app — `core` is already the cross-cutting hub that imports marking, classroom,
  and attendance.

- **Access control via `ParentStudentLink`** — the child list is
  `request.user.children`; a `child_id` (or paper) that is not linked is filtered
  out *before* the lookup, so it returns 404, never a "forbidden" page that
  confirms the record exists. This mirrors Sprint 4's ownership boundary exactly.

- **`notifications` app** — a new app holding the whole notification concern:
  - **`Notification` model** — `paper` (FK), `parent` (FK), `summary_text`,
    `status` (`sent`/`failed`), `error`, `sent_at`. A
    `UniqueConstraint(paper, parent)` makes it one row per parent per marked
    paper; that single constraint doubles as the dedupe guard.
  - **`summary.py`** — `generate_summary(paper)` turns the `MarkingResult` into
    two or three plain sentences via a **text-only** OpenRouter call (no image,
    so near-free, reusing `OPENROUTER_MODEL`). On any failure it falls back to a
    templated message built from the score alone. It never raises.
  - **`services.py`** — `send_whatsapp(to, body)` is the thin Twilio shim
    (lazily imported, time-bounded); `notify_parents_of_marked_paper(paper)` is
    the policy: guard on `status="marked"` and a known student, iterate the
    student's parents, skip those without a phone, dedupe-check, summarise, send,
    and record a `Notification` either way.

- **Trigger** — `marking/engine.py` calls `notify_parents_of_marked_paper` once
  the result is persisted on the success path, wrapped in a try/except that
  swallows and logs everything. Marking a paper and notifying a parent are two
  separate concerns; a notification problem never turns a successful mark into a
  failure.

- **`complete_text` on `OpenRouterClient`** — a text-only completion path,
  extracted alongside the existing `complete_with_image` from a shared
  `_complete(messages)`, so the summary reuses the same model-fallback and
  error handling as marking without carrying an image.

## Implementation notes

Decisions worth recording, including the deviations I want to flag.

- **Parent views in `core`, not a new `parents` app.** `core` already hosts the
  teacher and student dashboards and imports the other domain apps; a dedicated
  app for three views would fragment the role dashboards across two places.
  Notifications, by contrast, *are* their own app — they are a genuinely separate
  concern (model, AI summary, Twilio) with their own tests.

- **Reusing `_result_body.html` worked cleanly — flagging as asked.** The sprint
  brief said to flag if the read-only parent reuse needed more than hiding a
  retry button. It needed nothing: the partial's *marked* branch has no
  teacher/student actions in it (retry lives only in the *failed* branch, and
  parents only ever reach marked papers), so `parent_paper.html` just includes it
  with a back link. No fork.

- **The summary never raises, by construction.** `generate_summary` catches
  `OpenRouterError` and also a bare `Exception`, because the one thing worse than
  a jargon-free AI summary is no notification at all. A failure there degrades to
  a templated "X's work in Maths was marked: 7/10 (70%)" message, which is still
  genuinely useful.

- **Notification is synchronous, and I'm flagging the latency.** It hangs off the
  marking request, so a marked paper now also spends time on a text completion
  and a Twilio call before the request returns. Both are bounded (the Twilio send
  by `TWILIO_TIMEOUT`), and the whole step is best-effort, but moving it to a
  background worker is the clear next improvement — the same flag Sprint 2 raised
  about marking itself.

- **The dedupe guard is the unique constraint, checked twice.** The database
  constraint is the real guarantee; `notify_parents_of_marked_paper` also checks
  `exists()` first, purely to avoid generating a summary it would then throw
  away on a re-mark.

- **Only parents with a stored number are contacted, filtered in Python.** The
  `children`/`parents` properties return querysets; the phone check is a simple
  truthiness test per parent, and a parent without a number is skipped silently
  rather than recorded as a failure — there was never a send to fail.

- **Twilio is a lazy import.** `services.py` imports `twilio` only inside
  `send_whatsapp`, so nothing else in the app — and no test that never sends —
  depends on the package being importable.

## Definition of done

- [x] Parent can log in and see their linked children
- [x] Parent can view a child's marked papers using the same result display the
      teacher and student see
- [x] Parent can view a child's attendance
- [x] Access is enforced via `ParentStudentLink` — a parent cannot view another
      family's child's data (404 before the lookup, not 403)
- [x] A successful marking event generates a plain-language summary and attempts
      a WhatsApp send to every linked parent with a phone number
- [x] A failed summary generation falls back to a templated message, never
      silently skipping the notification
- [x] A failed WhatsApp send is recorded on the `Notification` without affecting
      the paper's status or the teacher/student's view
- [x] Re-marking a paper does not send a duplicate notification to the same parent
- [x] All parent views reject non-parent roles at the view level
- [x] Tests cover access control via `ParentStudentLink`, multi-child parents,
      notification only on success, the fallback summary path, dedupe on re-mark,
      and a send failure recorded without affecting the paper — all AI and Twilio
      calls mocked
- [x] New test modules (`notifications`, `core.tests_parent_portal`) added to the
      sprint-to-module mapping in `run.sh` so `./run.sh sprint 6` picks them up

## Out of scope for this sprint

Attendance-triggered notifications (only marking triggers one for now), the
cross-subject teacher dashboard (Sprint 7), and any change to how
`ParentStudentLink` is created.

## Known limitations

- **Parent-student linking is still self-declared and unverified** (carried over
  from Sprint 1) — a parent links by entering a student's username at
  registration, with no confirmation step. Unchanged by this sprint, and
  documented in the README.
- **The Twilio sandbox requires manual recipient opt-in.** Each number must join
  the sandbox (a WhatsApp join code) before it can receive messages — an
  operational step for demo day, not something the code handles.
- **WhatsApp is a demonstration channel, not a production one.** The in-app parent
  portal is the real $0 channel; production WhatsApp needs Meta's Business
  Platform with verification, out of scope here.
- **Notification is synchronous** — see the implementation note above; a
  background worker is the intended next step.

## Next up — Sprint 7

The cross-subject teacher dashboard: patterns across a class and over time —
which topics a cohort keeps losing marks on, and which students are trending down
across subjects rather than in one.
