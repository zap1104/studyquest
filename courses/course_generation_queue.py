"""Queue contract for asynchronous course generation.

This module is the *only* seam between the web layer and background work.

Views call ``enqueue_course_generation(...)``. They never import
``threading``, never start a worker, and never touch a queue directly. The
implementation behind this function can change -- a management-command worker
today, Celery or another durable queue later -- without any view changing.

Claiming is done with a conditional UPDATE rather than ``select_for_update``
because SQLite (the project's development database) does not support
``SELECT ... FOR UPDATE`` with ``skip_locked``. A conditional update on the
row's own status is atomic on every backend and lets two workers race safely:
only one of them sees ``rowcount == 1``.
"""
import logging
import uuid
from datetime import timedelta

from django.db import models, transaction
from django.utils import timezone

from .models import Course, CourseGenerationJob

logger = logging.getLogger(__name__)

# A job that has been "running" with no heartbeat for longer than this is
# assumed dead (dev-server reload, worker crash, host sleep).
STALE_JOB_THRESHOLD = timedelta(minutes=15)

# A job that was never claimed at all. Much shorter, because there is no
# legitimate reason for a queued job to sit untouched -- it almost always means
# the learner submitted a course with the worker not running, and they should
# be told so quickly rather than watching "Preparing" indefinitely.
STALE_QUEUED_THRESHOLD = timedelta(minutes=3)


class QueueError(Exception):
    """Raised when a job cannot be created or claimed."""


def enqueue_course_generation(
    course,
    *,
    source_bundle,
    generation_profile=None,
    custom_title="",
    study_focus="",
    plan_name="free",
):
    """Create a queued job for ``course`` and return it.

    The caller must have already extracted and validated ``source_bundle``.
    Extraction happens in the request on purpose: it is fast, it already has
    thorough error handling, and persisting its result means the worker never
    depends on request-scoped file handles.
    """
    if not isinstance(course, Course):
        raise QueueError("enqueue_course_generation requires a Course instance.")

    job = CourseGenerationJob.objects.create(
        course=course,
        attempt_id=uuid.uuid4(),
        status=CourseGenerationJob.STATUS_QUEUED,
        stage=CourseGenerationJob.STAGE_QUEUED,
        source_bundle=source_bundle or {},
        generation_profile=generation_profile or {},
        custom_title=custom_title or "",
        study_focus=study_focus or "",
        plan_name=plan_name or "free",
    )
    logger.info(
        "Enqueued course generation job %s for course %s",
        job.attempt_id, course.pk,
    )
    return job


def claim_next_job(worker_id):
    """Atomically claim the oldest queued job, or return ``None``.

    Two workers calling this concurrently cannot both succeed: the conditional
    ``UPDATE`` only matches while ``status`` is still ``queued``.
    """
    candidate = (
        CourseGenerationJob.objects
        .filter(status=CourseGenerationJob.STATUS_QUEUED)
        .order_by("queued_at")
        .values_list("pk", flat=True)
        .first()
    )
    if candidate is None:
        return None

    now = timezone.now()
    claimed = (
        CourseGenerationJob.objects
        .filter(pk=candidate, status=CourseGenerationJob.STATUS_QUEUED)
        .update(
            status=CourseGenerationJob.STATUS_RUNNING,
            stage=CourseGenerationJob.STAGE_READING_SOURCES,
            claimed_by=worker_id,
            started_at=now,
            heartbeat_at=now,
        )
    )
    if claimed != 1:
        # Another worker won the race.
        return None

    return CourseGenerationJob.objects.select_related("course").get(pk=candidate)


def touch_heartbeat(job):
    """Record liveness so stale detection does not reclaim a live job."""
    CourseGenerationJob.objects.filter(pk=job.pk).update(heartbeat_at=timezone.now())


def set_stage(job, stage):
    """Persist a stage change in a short, isolated write."""
    CourseGenerationJob.objects.filter(pk=job.pk).update(
        stage=stage, heartbeat_at=timezone.now(),
    )
    job.stage = stage


@transaction.atomic
def mark_job_succeeded(job):
    CourseGenerationJob.objects.filter(pk=job.pk).update(
        status=CourseGenerationJob.STATUS_SUCCEEDED,
        stage=CourseGenerationJob.STAGE_COMPLETE,
        completed_at=timezone.now(),
        heartbeat_at=timezone.now(),
        error_code="",
        error_message="",
    )


@transaction.atomic
def mark_job_failed(job, *, error_code="generation_failed", error_message=""):
    CourseGenerationJob.objects.filter(pk=job.pk).update(
        status=CourseGenerationJob.STATUS_FAILED,
        stage=CourseGenerationJob.STAGE_FAILED,
        completed_at=timezone.now(),
        heartbeat_at=timezone.now(),
        error_code=error_code,
        error_message=error_message,
    )


def recover_stale_jobs(threshold=STALE_JOB_THRESHOLD):
    """Reclaim jobs whose worker died mid-generation.

    Handles exactly one case: a job in ``running`` whose heartbeat has gone
    stale. That means a worker claimed it and then crashed, was killed, or the
    host slept.

    Deliberately does NOT touch ``queued`` jobs. An earlier version did, and it
    was wrong: the worker calls this at startup, before it claims work, so it
    would kill the very job it was about to process. A job that has been
    sitting queued is not necessarily abandoned -- it may simply be waiting for
    a worker that is starting right now.

    The "no worker ever ran" case is handled by ``reconcile_abandoned_queued``,
    which is called from a place that knows a worker is not coming.

    Never re-queues automatically: one silent retry loop is how you get runaway
    AI spend. The learner retries explicitly.
    """
    stale = CourseGenerationJob.objects.filter(
        status=CourseGenerationJob.STATUS_RUNNING,
        heartbeat_at__lt=timezone.now() - threshold,
    )

    recovered = 0
    for job in stale.select_related("course"):
        CourseGenerationJob.objects.filter(
            pk=job.pk,
            status=CourseGenerationJob.STATUS_RUNNING,
        ).update(
            status=CourseGenerationJob.STATUS_INTERRUPTED,
            stage=CourseGenerationJob.STAGE_FAILED,
            completed_at=timezone.now(),
            error_code="worker_interrupted",
            error_message=(
                "Course preparation was interrupted before it finished. "
                "You can try again."
            ),
        )
        _fail_course_for_job(job, interrupted=True)
        recovered += 1

    if recovered:
        logger.warning("Recovered %s stale course generation job(s).", recovered)
    return recovered


def _fail_course_for_job(job, *, interrupted=False, never_started=False):
    """Move the owning course out of ``processing`` so it stops holding a slot."""
    if never_started:
        message = (
            "Course preparation never started because the background worker "
            "was not running. You can try again."
        )
    elif interrupted:
        message = (
            "Course preparation was interrupted before it finished. "
            "You can try again."
        )
    else:
        message = "StudyQuest could not finish this course. You can try again."

    Course.objects.filter(
        pk=job.course_id, status="processing",
    ).update(
        status="failed",
        generation_error=message,
        generation_stage=CourseGenerationJob.STAGE_FAILED,
        generation_completed_at=timezone.now(),
    )


def has_open_job(course):
    """True when the course already has work queued or in flight.

    Guards against a double-submitted form creating two jobs and two AI calls.
    """
    return CourseGenerationJob.objects.filter(
        course=course,
        status__in=[
            CourseGenerationJob.STATUS_QUEUED,
            CourseGenerationJob.STATUS_RUNNING,
        ],
    ).exists()


def reconcile_abandoned_queued(threshold=STALE_QUEUED_THRESHOLD):
    """Report queued jobs that no worker has touched, without killing them.

    Called from the learner-facing status endpoint, which is the right place:
    it runs only when someone is actually looking at a waiting course, and it
    can safely ask "has a worker been seen at all?" rather than "is this job
    old?".

    Returns the number of courses that appear to have no worker running, so the
    UI can tell the learner the honest reason. It does NOT change job state --
    a queued job stays queued, because the correct resolution is to start the
    worker, not to discard the learner's work.

    Why this is separate from ``recover_stale_jobs``: the worker calls recovery
    at startup, before claiming work. Reclaiming queued jobs there would kill
    the very job the worker was about to process. This was a real bug.
    """
    cutoff = timezone.now() - threshold
    return CourseGenerationJob.objects.filter(
        status=CourseGenerationJob.STATUS_QUEUED,
        queued_at__lt=cutoff,
    ).count()