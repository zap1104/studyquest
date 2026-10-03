# Course Forge — Background Generation

Course generation is **asynchronous**. Submitting Course Creation returns
immediately and the AI work happens in a separate worker process.

This document covers how to run it, what happens when things go wrong, and
what the current architecture does and does not guarantee.

---

## Two processes, always

Course generation cannot happen without the worker. The web server only
*queues* work; the worker *performs* it.

**Terminal 1 — web application**

```powershell
.\run.bat
```

**Terminal 2 — generation worker**

```powershell
.\run_worker.bat
```

Keep both running. The scripts print the URL and the worker id respectively.

Equivalent manual commands:

```bash
python manage.py runserver
python manage.py run_course_generation_worker
```

The worker also accepts `--once` (process at most one job, then exit) and
`--interval N` (poll interval in seconds, default 2).

---

## What the learner sees

1. Learner submits Course Creation.
2. The request returns immediately — the course exists with
   `status="processing"` and a queued job.
3. The browser lands on **Course Forge** (`/courses/<id>/generating/`), which
   polls `/courses/<id>/generation-status/` every 2–5 seconds.
4. When the worker finishes, the course becomes `active` and Course Forge
   offers **Enter Course**.

The learner may:

- **Leave the page.** Generation continues in the background.
- **Refresh.** The page re-reads state from the database. No second job is
  created and generation does not restart.
- **Close the tab and return later.** The dashboard and Course Library both
  show a "Preparing" card linking back to Course Forge.

Progress state lives on the `Course` row and the `CourseGenerationJob` row —
never in the request or the browser session.

---

## If the worker is not running

Nothing breaks, but nothing progresses either.

The course stays `processing` and its job stays `queued`. After ~30 seconds
the Course Forge page stops claiming to be working and says:

> Waiting for the background worker to start.
> You can leave this page and come back later.

Starting the worker later picks up the *same* job and completes the *same*
course. No credit was consumed in the meantime.

This is deliberate: an indefinite unexplained spinner would be dishonest.

---

## Inspecting failed jobs

Failures are recorded, not discarded. Two places to look:

**Django admin** — `/admin/courses/coursegenerationjob/`
Filter by status and stage. Useful columns: `attempt_id`, `status`, `stage`,
`retry_count`, `queued_at`, `completed_at`, `error_code`, `error_message`.

**Shell**

```bash
python manage.py shell
```

```python
from courses.models import CourseGenerationJob
for j in CourseGenerationJob.objects.filter(status="failed"):
    print(j.attempt_id, j.course_id, j.error_code, j.error_message)
```

Job statuses: `queued`, `running`, `succeeded`, `failed`, `cancelled`,
`interrupted`.

**Error text is learner-safe by design.** Provider prompts, raw API responses
and credentials are never written to `error_message` or to the status
endpoint. Unexpected exceptions are replaced with a generic message.

---

## Retrying failed generation

Two ways:

- **Course Forge page** — the failure screen offers **Try Again**.
- **Course Library** — a failed course card offers **Try Again** and
  **Remove Draft**.

Retry **reuses the persisted source bundle**, so the learner does not
re-upload. It creates a *new* job with a new `attempt_id` and increments
`retry_count`; the previous attempt's record is preserved.

Guard rails:

- A retry is refused while another job for the same course is open.
- A failed generation consumes **no** generation credit.
- Credit is consumed exactly once, on success, guarded by a unique
  `reference_key` on the credit ledger — so a retry that succeeds charges
  once, not twice.

---

## Interrupted jobs

If the worker is killed mid-generation (Ctrl+C, machine sleep, deploy), the
job is left `running`. On its next start the worker performs stale-job
recovery: anything `running` whose heartbeat is older than **15 minutes** is
marked `interrupted` and its course is moved to `failed`.

Recovery deliberately does **not** auto-retry. One silent retry loop is how
you get runaway AI spend; the learner or an admin retries explicitly.

A course stuck in `processing` would otherwise occupy one of the learner's
active-course slots forever, so recovery is a correctness requirement rather
than a nicety.

---

## Architecture and its limits

### Current (development)

A management-command worker in its own process, claiming jobs from the
`CourseGenerationJob` table with a conditional `UPDATE` so two workers can
never claim the same job.

Chosen over a spawned thread because a thread inside the web process dies on
every dev-server reload, worker recycle, or deploy — leaving courses stranded.

### Not production-grade yet

- **Single worker, no concurrency tuning.** No parallelism or priority.
- **SQLite.** Fine here; a production deployment wants PostgreSQL.
- **The database *is* the queue.** Polling every 2s is acceptable at this
  scale and would not be at high volume.
- **No broker.** There is no Redis, Celery, or monitoring.

### Migration path

The web layer only ever calls:

```python
enqueue_course_generation(course_id, attempt_id)
```

No view imports `threading`, starts a worker, or touches the queue directly.
Moving to Celery or another durable queue means changing what sits behind that
function — the views do not change.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Course stuck "Preparing", page says worker is not running | Worker not started | Start `run_worker.bat` |
| Course `failed` after a restart | Worker was killed mid-run | Use **Try Again** on the course |
| Job stuck `running` | Same, before recovery ran | Restart the worker; stale recovery will reclaim it |
| "No queued jobs" from the worker | Queue is genuinely empty | Expected |
| `database is locked` | Concurrent writes on SQLite | Retry; the worker keeps transactions short |

---

## Verification commands

```bash
python manage.py check
python manage.py test
python manage.py makemigrations --check
```

The integration contract — that a worker-generated course is topic-grounded —
is covered by `courses/tests/test_course_generation_async.py`.
