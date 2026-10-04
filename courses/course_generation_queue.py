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
    """Reclaim jobs that were abandoned, so a course never reads "Preparing" forever.

    Two distinct failure modes are handled:

    1. A worker crashed mid-generation (job ``running``, heartbeat gone).
    2. No worker ever picked the job up (job ``queued`` past a short window).
       This is the common development case -- the learner submits a course with
       the worker not running.

    Without this, a stranded course also keeps occupying one of the learner's
    active-course slots, because ``get_active_course_count`` counts
    ``processing`` courses. Recovery is a correctness requirement.

    Deliberately does *not* re-queue automatically: one silent retry loop is
    how you get runaway AI spend. The learner (or an admin) retries explicitly.
    """
    # Two distinct ways a job gets abandoned:
    #
    # 1. RUNNING with a dead heartbeat -- the worker crashed mid-generation.
    # 2. QUEUED and untouched past a short window -- no worker was ever
    #    running when the learner submitted the course.
    stale = CourseGenerationJob.objects.filter(
        models.Q(
            status=CourseGenerationJob.STATUS_RUNNING,
            heartbeat_at__lt=timezone.now() - threshold,
        )
        | models.Q(
            status=CourseGenerationJob.STATUS_QUEUED,
            queued_at__lt=timezone.now() - STALE_QUEUED_THRESHOLD,
        )
    )

    recovered = 0
    for job in stale.select_related("course"):
        was_running = job.status == CourseGenerationJob.STATUS_RUNNING
        CourseGenerationJob.objects.filter(
            pk=job.pk,
            status__in=[
                CourseGenerationJob.STATUS_RUNNING,
                CourseGenerationJob.STATUS_QUEUED,
            ],
        ).update(
            status=CourseGenerationJob.STATUS_INTERRUPTED,
            stage=CourseGenerationJob.STAGE_FAILED,
            completed_at=timezone.now(),
            error_code=(
                "worker_interrupted" if was_running else "worker_never_started"
            ),
            error_message=(
                "Course preparation was interrupted before it finished. "
                "You can try again."
                if was_running
                else "Course preparation never started because the background "
                     "worker was not running. You can try again."
            ),
        )
        _fail_course_for_job(job, interrupted=was_running, never_started=not was_running)
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