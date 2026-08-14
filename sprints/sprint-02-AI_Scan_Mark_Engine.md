# Sprint 2 — AI Scan & Mark Engine

## Overview

Sprint 2 builds iSgela's core feature: the AI marking engine. It is built generically today, independent of any portal UI, so it can be reused by both the teacher's exam-marking flow (Sprint 3) and the student's homework-submission flow (Sprint 4) without being rebuilt for either.

## Goals

- Model exam/homework submissions, memorandums, and marking results
- Accept and process an uploaded paper image, optimized for low-bandwidth users
- Call an AI vision model to mark the paper against its memorandum and return structured, per-question feedback
- Handle AI failures and malformed responses gracefully — never lose a submission silently
- Prove the engine works end-to-end via a minimal test endpoint, without wiring it into any portal yet

## What was built

- **`Paper` model** — an uploaded submission: image, linked `Memorandum`, submitter, status (`pending` / `marked` / `failed`), timestamp
- **`Memorandum` model** — the marking guide (question, expected answer, marks available per question), entered via Django admin for now
- **`MarkingResult` model** — linked to a `Paper`: overall score, and a per-question breakdown (question number, marks awarded, marks available, feedback explaining *why* marks were lost)
- **Image handling** — Pillow validates the upload is a genuine image, auto-orients it (phone photos are frequently rotated), and compresses it before it leaves the server, since we're optimizing for low-bandwidth users, not full-resolution phone photos
- **Storage** — processed images stored in Supabase Storage, not local disk
- **AI marking call** — the processed image and memorandum text are sent to **Qwen2.5-VL via OpenRouter**, prompted to return structured JSON only (overall score + per-question results). The model slug is an environment variable, not a constant, because free-tier availability on OpenRouter changes without notice — see the model note below
- **Defensive parsing** — because a free-tier open-weight model is more prone than a top commercial model to wrapping JSON in extra text or drifting slightly from the requested schema, parsing includes stripping markdown code fences, retry-on-malformed logic, and explicit handling of OpenRouter rate-limit (429) responses alongside outright failures
- **Failure handling** — a failed or malformed AI response sets the `Paper` status to `failed` and stores the error; nothing crashes or silently disappears
- **Minimal test endpoint** — upload an image + select a memorandum → returns the marking result as JSON. Not wired into any portal yet.

## Tech stack (this sprint)

- Qwen2.5-VL (open-weight vision model) via OpenRouter
- Pillow (image processing)
- Supabase Storage
- `requests` (OpenRouter API calls)

## Why an open-weight model

Chosen over a closed commercial API (Claude direct, or via Bedrock) specifically to avoid per-token costs that don't scale well for under-resourced schools. See the main README's Tech Stack section for the full reasoning — the short version is that Qwen2.5-VL is strong at OCR/document understanding, Apache 2.0 licensed, and opens a future path to self-hosting for near-zero marginal cost at scale.

### Model decision: the free Qwen slug no longer exists

This sprint was planned around `qwen/qwen2.5-vl-72b-instruct:free`. That slug has
been withdrawn from OpenRouter — checked against the live model list, and there is
currently **no free Qwen2.5-VL variant at all**. Only the paid one remains.

Three candidates were tested with one real call each, marking the same mock paper:

| Slug | Cost | Outcome |
| --- | --- | --- |
| `qwen/qwen2.5-vl-72b-instruct` | ~$0.25/M input, about $0.0006 per paper | Clean JSON, read the paper accurately, awarded partial credit correctly. **Chosen as the default.** |
| `nvidia/nemotron-nano-12b-v2-vl:free` | free | Clean JSON, but marked a learner 0/3 for working they had actually shown |
| `google/gemma-4-31b-it:free` | free | HTTP 429 on the first attempt |

We stayed on Qwen. The reasoning behind the original choice survives intact where
it counts: Apache 2.0, open weights, and a path to self-hosting for near-zero
marginal cost. Only "free today" broke, and at roughly $0.0006 a paper, a
thousand marked papers costs about 60 cents — still an order of magnitude away
from being the constraint the original decision was guarding against. The free
alternative was rejected on accuracy, not price: a marking engine that penalises
work a learner did show is worse than no marking engine.

`OPENROUTER_FALLBACK_MODELS` supports a fallback chain and is tested, but ships
**empty on purpose**. Falling back mid-demo would mark different papers to
different standards, so every result records the `model_used` that produced it
and a rate limit surfaces as a rate limit rather than being papered over.

## Setup additions this sprint

1. Create an OpenRouter account, generate an API key, add `OPENROUTER_API_KEY` to `.env`
2. Add `SUPABASE_SERVICE_KEY` to `.env` (Supabase → Project Settings → API →
   `service_role`). `SUPABASE_URL` is derived from the project reference already in
   `DATABASE_URL`, so it does not need setting
3. `pip install -r requirements.txt` — this sprint adds `requests` and `Pillow`
4. `python manage.py migrate` for the new `marking` tables
5. `python manage.py ensure_storage_bucket` to create the private papers bucket
6. Confirm the model slug on openrouter.ai/models before a demo. Slugs get
   withdrawn, and `OPENROUTER_MODEL` is the one place to change it

## Definition of done

- [x] A real handwritten paper photo + memorandum returns a structured score with per-question feedback
- [x] Malformed AI responses and rate-limit errors are handled gracefully, not crashed on
- [x] Images are compressed before storage, stored in Supabase Storage, not locally
- [x] At least one automated test covers JSON parsing with a mocked AI response — no real API calls burned in the test suite
- [x] README's setup section updated with new environment variables

## Out of scope for this sprint

Wiring the engine into the teacher or student portal UI, facial recognition, WhatsApp notifications, homework-specific logic. Sprint 3 and 4 give this engine its front door.

## Known limitations

- **Marking is synchronous.** A submission holds the request open for up to a
  minute while the model reads the paper. Fine for a demo and for one classroom;
  a background worker is the answer at scale, not this sprint.
- **Marking accuracy is not verified at scale.** The engine was confirmed
  end-to-end on a generated paper and on manual checks. It has not been measured
  against a set of real handwritten scripts with known marks, so we cannot yet
  state how often it agrees with a human marker. That measurement is worth doing
  before anyone treats a mark as final.
- **Rate limits are real, and now visible.** They surface as HTTP 429 with a
  `retry_after`, and the paper is kept as `failed`/`rate_limited` for retry
  rather than lost. `google/gemma-4-31b-it:free` returned 429 on the very first
  call during testing, which is the clearest argument against depending on a free
  tier during a demo.
- **A marked paper is never overwritten silently** — re-marking replaces the
  previous result, and the raw model reply is stored alongside each result for
  debugging format drift.
- Memorandum authoring is admin-only and plain text for now — no teacher-facing authoring UI yet
- The `service_role` Supabase key bypasses row-level security. It is server-side
  only and never reaches a template, but it does mean the app is trusted with
  full access to the bucket.

## Next up — Sprint 3

Build the teacher portal: upload memorandums, mark exam papers using this engine, view results, post homework/study materials for students.