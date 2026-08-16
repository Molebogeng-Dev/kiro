# iSgela

An AI-powered platform connecting South African teachers, students, and parents — cutting the time teachers spend on marking and admin so they can spend more time teaching and tailor guiding invididual students to a future that suits them.


## The problem

Africa's (and other continents) education system has a connection problem, not just a resourcing problem — three groups who all need to work together often aren't in contact at all.

**Parents are disconnected from their child's progress.** 
Many parents work long hours and can't get to the school to ask a teacher how their child is actually doing. Without that connection, a child can be struggling — or thriving — and the parent finds out too late, or not at all.

**Teachers lack a clear picture of where each student is falling behind.** 
Marking is manual and time-consuming, especially in the foundation phase, which leaves little time to actually track *why* a student keeps getting something wrong and step in early.

**Students who don't speak up fall through the cracks.** 
Not every child tells a teacher or parent when they're struggling, skipping homework, or avoiding school. Without a system watching for that pattern, it goes unnoticed until it's a bigger problem.

## Solution
*iSgela* closes these gaps by putting the teacher, student, and parent in one connected loop — so a marked paper/exam, a missed homework assignment, or an absence surfaces to the people who need to know, automatically, instead of depending on everyone finding time to communicate manually.

### Beyond the classroom

At scale, this also becomes a dataset many countries doesn't currently have: real, granular visibility into where students nationally are struggling and why. That's a longer-term direction, not part of this MVP, but it's the reason this problem is worth solving properly from day one rather than as a one-off tool.

## What it does

- **Scan & Mark** — teachers photograph a paper or students submit homework by camera; AI marks it against the memorandum and explains *why* an answer was wrong, not just that it was and also suggests solutions on how to help the student if they struggled. 
- **Attendance** — primary students (grades 1–7) are marked present through a traditional roll-call register; secondary students (grades 8–12) check in and out via a single facial-recognition scan, deliberately mirroring a professional check-in environment. Both feed the same attendance record, visible on the parent and teacher portals.

- **Three connected portals** — teacher, student, and parent each see what matters to them, in one place
- **Parent notifications** — plain-language summaries sent automatically when homework/exam is marked

## Known limitations

- **Parent-student linking is currently self-declared and unverified.**
  A parent can link themselves to a student's account by entering that student's username at registration. There is no confirmation step from the student or a teacher. For an MVP this keeps onboarding simple, but in a production version this link should requireapproval — e.g. a "pending" state confirmed by the student or a teacher — before a parent can view a child's academic records, attendance, or homework results.
  This is a deliberate scope decision for the hackathon timeline, not an oversight.

- **Static files are served by the application process.**
  WhiteNoise sits in the middleware so the interface stylesheet and the Django
  admin both render from a single container. For anything beyond a demo this
  belongs behind a reverse proxy or CDN rather than in the Python process.
  Revisit during the security and hardening pass, along with
  `manage.py check --deploy`, HSTS, and secure cookie settings.

## Tech stack

- Django + Django REST Framework
- Supabase (Postgres, storage)
- Qwen2.5-VL-72B-Instruct (open-weight vision model, via OpenRouter) — paper marking engine
- face-api.js — attendance check-in
- Twilio WhatsApp sandbox — parent notifications

We chose an **open-weight model over a closed commercial API** for the marking engine because the schools this app targets can't absorb unpredictable per-token costs at scale. Qwen2.5-VL is Apache 2.0 licensed and strong at OCR/document understanding.

In practice, marking a single paper costs well under $0.001 at current OpenRouter pricing ($0.25/M input tokens, $0.75/M output tokens) — a school marking 10,000 papers a month lands around $10/month in total. That cost is designed to sit with the operator (a school or the department), not the individual parent or student, so no one at the household level ever needs to pay per use.

Because the model is open-weight, there's also a clear path beyond that: a production deployment could self-host Qwen2.5-VL on subsidized infrastructure, bringing the marginal cost per student toward zero at national scale, rather than the app being permanently dependent on a commercial API bill that grows with every school that adopts it.

## Setup

Requires Python 3.12+ and a Supabase project.

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
# create .env (see below), then:
python manage.py migrate
**python manage.py createsuperuser**   # prompts for email and role (might disable permission)
python manage.py ensure_storage_bucket # creates the private Supabase bucket for papers
python manage.py runserver
```

Then visit `/accounts/register/`, create one account per role, and confirm each
lands on its own dashboard.

## Deployment note — timeout configuration required

Marking is a synchronous call and can take up to a few minutes under
load. The application layer is already configured for a 3-minute
timeout, but **this alone is not enough** — whichever platform this
gets deployed to must be explicitly checked and configured to match,
or a shorter platform-level timeout will kill the request regardless
of the application setting.

Before deploying, confirm and align timeouts at every layer in the
request path:

- [ ] **Application/WSGI server** (e.g. gunicorn `--timeout`) — already
      set to 3 minutes, verify it's still in place
- [ ] **Reverse proxy**, if one is used (e.g. Nginx
      `proxy_read_timeout` / `proxy_send_timeout`) — defaults are
      often as low as 60s and will silently override a longer
      application-level setting
- [ ] **Load balancer**, if one is used (e.g. AWS ALB idle timeout,
      default 60s) — must be raised to match
- [ ] **Platform-imposed hard limits** — some hosting platforms cap
      request duration regardless of any config (e.g. Heroku's router
      enforces a fixed 30-second limit that cannot be overridden).
      Confirm the chosen platform actually supports multi-minute
      requests before deploying, not after.

If a mismatch is discovered late, the marking flow will appear to
work in local testing but fail intermittently in production —
exactly the kind of bug that's easy to miss until a demo.

### Environment variables

`.env` in the project root, never committed:

| Variable | Required | Notes |
| --- | --- | --- |
| `SECRET_KEY` | yes | Django signing key. The project refuses to start without it. |
| `DEBUG` | no | Defaults to `False`. |
| `DATABASE_URL` | yes | Supabase Postgres connection string. |
| `ALLOWED_HOSTS` | no | Comma-separated. Defaults to `localhost,127.0.0.1`. |
| `TIME_ZONE` | no | Defaults to `Africa/Johannesburg`. |
| `DB_CONN_MAX_AGE` | no | Seconds to reuse a connection. Defaults to `0`. |
| `TEST_ON_POSTGRES` | no | See Tests below. |
| `OPENROUTER_API_KEY` | yes | From openrouter.ai. Required for marking. |
| `OPENROUTER_MODEL` | no | Vision model slug. Defaults to `qwen/qwen2.5-vl-72b-instruct`. |
| `OPENROUTER_FALLBACK_MODELS` | no | Comma-separated slugs tried if the primary is rate-limited or withdrawn. Empty by default. |
| `OPENROUTER_TIMEOUT` | no | Seconds. Defaults to `120`. |
| `OPENROUTER_MAX_TOKENS` | no | Defaults to `2000`. |
| `SUPABASE_SERVICE_KEY` | yes | `service_role` key, from Supabase → Project Settings → API. Needed to upload papers. |
| `SUPABASE_URL` | no | Derived from `DATABASE_URL`'s project reference. Only set it to override. |
| `SUPABASE_STORAGE_BUCKET` | no | Defaults to `papers`. |
| `SUPABASE_SIGNED_URL_EXPIRY` | no | Seconds a paper's signed URL stays valid. Defaults to `3600`. |
| `MARKING_IMAGE_MAX_DIMENSION` | no | Longest edge after compression. Defaults to `1600`. |
| `MARKING_IMAGE_JPEG_QUALITY` | no | Defaults to `80`. |
| `MARKING_MAX_UPLOAD_BYTES` | no | Rejected above this before processing. Defaults to 15 MB. |

The `service_role` key bypasses row-level security. It stays server-side, is
never rendered into a template, and belongs in `.env` only.

### A note on the Supabase connection string

Copy the **Session pooler** string from Supabase (Connect → Session pooler),
not the direct `db.<ref>.supabase.co` one. The direct host resolves to IPv6
only, so on an IPv4-only network it fails with `Network is unreachable`, which
reads like a credentials problem but is not one.

Two things to watch for when pasting it in:

- Replace the whole `[YOUR-PASSWORD]` placeholder, brackets included.
- Percent-encoding the password is still the safest thing to do (`@` becomes
  `%40`, `?` becomes `%3F`). The parser in `config/db.py` tolerates any one of
  `@`, `#`, `/`, `?`, or `:` left raw, but a password containing both a raw `@`
  and a raw `?` is genuinely ambiguous and is rejected with an explanatory
  error. Other Postgres tools, `psql` included, are stricter than this parser.

Query parameters on the URL are passed straight through to libpq, so
`?sslmode=verify-full&application_name=isgela` works as expected. The parser is
covered by `config/tests.py`.

## The marking engine

`marking/` holds the Scan & Mark engine, built independently of any portal so the
teacher flow (Sprint 3) and the student homework flow (Sprint 4) can both call it.

The path a submission takes:

1. `core/images.py` validates the upload is genuinely a JPEG or PNG, straightens
   it using its EXIF orientation tag, and compresses it. A 4 MB phone photo
   typically leaves as a few hundred kilobytes, which matters when the person
   uploading is paying for mobile data.
2. `core/storage.py` puts the compressed image in a **private** Supabase Storage
   bucket. Papers are children's schoolwork, so the bucket is not public and
   `url()` returns a time-limited signed URL.
3. `marking/openrouter.py` sends the image and the memorandum to a vision model.
4. `marking/parsing.py` turns the reply into marks, tolerating the formatting
   drift an open-weight model produces.
5. `marking/engine.py` stores the result, or records why it failed.

### Choosing a model

The model slug is configuration, not code, because free-tier availability on
OpenRouter changes without notice. As of this sprint:

| Slug | Cost | Notes |
| --- | --- | --- |
| `qwen/qwen2.5-vl-72b-instruct` | ~$0.25/M input, about $0.0006 per paper | Apache 2.0, self-hostable later. Read a test paper accurately. Default. |
| `nvidia/nemotron-nano-12b-v2-vl:free` | free | Works, returns clean JSON, but read the same paper less accurately. |
| `google/gemma-4-31b-it:free` | free | Returned HTTP 429 on first attempt. |

There is currently **no free Qwen2.5-VL variant** on OpenRouter; the `:free`
slug has been withdrawn. `OPENROUTER_FALLBACK_MODELS` is deliberately empty by
default: falling back mid-demo would mark different papers with different models,
and marks are not comparable across models. Every result records the
`model_used` that produced it.

### When marking fails

A failure never loses the submission. The `Paper` is kept with
`status="failed"` and a `failure_kind`, because the response differs per kind:

| Kind | Cause | HTTP |
| --- | --- | --- |
| `rate_limited` | Provider 429 | 429, with `retry_after` |
| `no_credit` | Provider 402 | 402 |
| `model_unavailable` | Slug withdrawn (404) | 503 |
| `service_error` | Timeout or 5xx, after one retry | 502 |
| `invalid_response` | Unparseable after one corrective retry | 422 |
| `image_error` | Not a usable image | 400 |

Marking runs synchronously and can take up to a minute. Moving it to a
background worker is a scaling concern for after the hackathon.

## The teacher portal

`marking/` and `classroom/` hold the teacher-facing pages added in Sprint 3. None
of them contain marking logic: every upload goes through
`marking/submissions.py`, which is the single path from a file to a marked paper,
shared with Sprint 2's JSON endpoint.

| Page | Path |
| --- | --- |
| Dashboard | `/teacher/` |
| Mark a paper | `/marking/mark/` |
| Marked papers | `/marking/papers/` |
| One paper's feedback | `/marking/papers/<id>/` |
| Memorandums | `/marking/memorandums/` |
| Assignments | `/classroom/assignments/` |
| Study material | `/classroom/materials/` |

Every one rejects non-teachers at the view with the Sprint 1 `role_required`
decorator, and each is scoped to the teacher who created the record: papers,
memorandums, assignments, and material are all filtered by owner. Without class
or roster structure, ownership of the submission is the only boundary available,
and it is a better default than letting any teacher read any learner's marked
work.

A `Paper` now records the learner it belongs to as well as who uploaded it. A
teacher marking a class set is not the author of any of it, and a mark that is not
attached to a learner cannot reach them or their parent later.

### Typing, or photographing, a memorandum or assignment

A teacher can write a memorandum or an assignment's instructions by hand, or
upload a photo and have the vision model transcribe it into the form. This reuses
the Sprint 2 image pipeline and the same model, but asks for plain text rather
than JSON, and it lives in `marking/transcription.py`.

Two deliberate differences from marking. First, the result is never saved on its
own: it pre-fills the form for the teacher to review and correct before saving,
because OCR misreads and a silently altered marking guide is worse than one typed
by hand. Second, the source photo is not stored — it is only a means of getting
text. If the model is unreachable or rate limited, the teacher gets a plain
message and the typing fields are still right there, so they are never stuck.

### When marking fails in the interface

The result page doubles as the failure page. It explains what happened in plain
language, with no provider names or status codes, confirms the photo is still
stored, and offers a retry button that re-marks from storage so the teacher does
not have to photograph the page again. The button is withheld for the two
failures that retrying cannot fix — an empty account and a withdrawn model — since
those repeat identically until somebody changes a setting.

### Interface notes

Written for a teacher who may be in their sixties and did not ask for more
software: body text starts at 18px, every control is at least 48px tall, status
is always a word as well as a colour, and there is one obvious primary action per
screen. `static/js/isgela.js` is small and optional — it adds a type-to-narrow
box to the learner picker, confirms which photo was chosen, and shows progress
while marking runs, which matters because marking is synchronous and takes up to
a minute. Every page works with JavaScript blocked.

## The student portal

`classroom/` and `marking/` also hold the student-facing pages (Sprint 4). A
student views what teachers posted and submits homework through the *same*
`marking/submissions.py` path the teacher uses — no marking logic is duplicated.

| Page | Path |
| --- | --- |
| Dashboard | `/student/` |
| My assignments | `/classroom/my-assignments/` |
| Submit homework | `/classroom/my-assignments/<id>/submit/` |
| My results | `/marking/my-results/` |
| One result | `/marking/my-results/<id>/` |
| Study material | `/classroom/study-materials/` |

Two boundaries matter here:

- **Ownership.** A student sees only papers where they are the subject
  (`student=request.user`). Guessing another student's result id returns a 404,
  not a 403 — the filter excludes it before the lookup, so the page never
  confirms it exists.
- **Server-set attribution.** When a student submits homework, `Paper.student`
  is taken from the session, never from the request body. The submission form
  carries only the photo; the assignment comes from the URL. A tampered request
  with someone else's id is ignored, and a test posts exactly that to prove it.

A `Paper` now also records the `assignment` it answers (nullable, `SET_NULL`),
distinct from its memorandum, so the assignments list can show "submitted" or
"not yet" per student. The result page is shared with the teacher through a
single `marking/_result_body.html` partial, so a marked paper looks the same to
whoever is entitled to see it.

### Task runner

`run.sh` wraps the common commands and finds the virtualenv itself:

```bash
./run.sh              # dev server (default port 1097)
./run.sh test         # suite against in-memory SQLite
./run.sh test-pg      # same suite against Supabase Postgres
./run.sh sprint 4     # one sprint's tests (in-memory SQLite)
./run.sh sprint-pg 4  # one sprint's tests against Supabase Postgres
./run.sh sprints      # every sprint's tests, one labelled group at a time
./run.sh sprints-pg   # the same, against Supabase Postgres
./run.sh check        # system checks + unapplied-migration check
./run.sh ci           # check + test, no server. Use this in a pipeline.
```

Exit codes propagate, so `./run.sh ci` fails a build when the suite fails.

### Tests

```bash
python manage.py test
```

Runs against in-memory SQLite, with file storage swapped for an in-memory
backend and every OpenRouter call mocked. The suite makes no network requests,
burns no API quota, and leaves nothing in the storage bucket. To run the same
suite against Supabase:

```bash
TEST_ON_POSTGRES=True python manage.py test --keepdb
```

`--keepdb` is required: Supabase's pooler holds a session open, which stops
Django from dropping the test database afterwards.

To read results grouped by sprint rather than as one combined total, use
`./run.sh sprints` (or `./run.sh sprint <n>` for a single one), with `-pg`
variants (`./run.sh sprints-pg`, `./run.sh sprint-pg <n>`) to run each group
against Supabase Postgres. The mapping of test modules to sprints lives in
`run.sh`; every module belongs to exactly one sprint, so a failure points
straight at the sprint that owns it.

These sprint commands use a concise test runner (`config/test_runner.py`) that
stays silent about passing tests and names only the ones that fail, error, or
are skipped — with full tracebacks and a count summary underneath, the way
pytest's short summary reads. `./run.sh test`, `test-pg`, and `ci` keep Django's
default output.

## Continuous integration

GitHub Actions, defined in `.github/workflows/ci.yml`.

Only testing is switched on. Lint, security scanning, image packaging, release,
and deploy jobs are written out in full but commented, to be enabled as the
project needs them.

| Job | What it proves | Needs secrets? |
| --- | --- | --- |
| Tests (in-memory SQLite) | `manage.py check`, then the suite | No |
| Tests (Postgres) | Migration drift, then the same suite on a real Postgres | No |
| Tests (live Supabase) | The same suite against the actual project database | Yes, and skips itself without them |

The first two need **no secrets**. Under the test runner the settings module
supplies its own throwaway `SECRET_KEY`, swaps in in-memory SQLite and in-memory
file storage, and forces a fake OpenRouter key, so a test that forgot to mock
cannot spend real quota.

They are not entirely configuration-free, though. `check` and `makemigrations`
are not tests: they import the real settings module, which refuses to start
without a `SECRET_KEY` and a `DATABASE_URL`. That refusal is deliberate, so the
workflow supplies a throwaway key and, for the SQLite job, a `DATABASE_URL`
pointing at a closed port that nothing connects to. The alternative would be
loosening the settings module so a missing key is tolerated outside tests, which
is exactly the mistake the check is there to catch.

`makemigrations --check` runs in the Postgres job rather than the SQLite one
because it consults the database for migration history: against a real database
it is silent and meaningful, against a placeholder it warns and proves little.

The Postgres job uses an ephemeral service container rather than Supabase. It
needs no credentials, cannot collide with another pipeline running at the same
time, and exercises the test-database teardown path that Supabase's pooler
blocks. To also run against real Supabase, set a `SUPABASE_TEST_DATABASE_URL`
secret; that job never fails the pipeline, since it depends on a third party
being up.

## Running from a container image

For demos, in case a hosted deployment is not ready in time.

```bash
docker build -t isgela:latest .

docker run --rm -p 8000:8000 \
  --env-file .env \
  --env ALLOWED_HOSTS=localhost,127.0.0.1 \
  --env RUN_MIGRATIONS_ON_START=1 \
  --env ENSURE_STORAGE_BUCKET_ON_START=1 \
  isgela:latest
```

Notes on the image:

- Two stages, so pip and its caches never reach the runtime layer. Runs as a
  non-root user.
- `.env` is in `.dockerignore`: configuration is passed in at run time, never
  baked into a layer.
- Migrations and bucket creation are **opt-in** via the two flags above. They are
  off by default because more than one replica starting at once would race to
  apply the same migration. For a single demo container, switch them on.
- Gunicorn runs with `--timeout 180`. Marking is synchronous and a paper can take
  up to a minute, which the 30 second default would kill.
- WhiteNoise serves collected static files. Without it the interface loads
  unstyled, so this is not cosmetic.

## Built with Kiro

See `.kiro/sprints/*.md` for the full sprint-by-sprint breakdown of how this was built.

## Demo video

[Link once recorded]