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

from django.db import transaction
from django.utils import timezone

from .models import Course, CourseGenerationJob

logger = logging.getLogger(__name__)

# A job that has been "running" with no heartbeat for longer than this is
# assumed dead (dev-server reload, worker crash, host sleep).
STALE_JOB_THRESHOLD = timedelta(minutes=15)


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
    """Return jobs whose worker died to ``interrupted``.

    Without this, a crashed worker leaves a course stuck in ``processing``
    forever -- and because ``get_active_course_count`` counts ``processing``
    courses, that permanently consumes one of the learner's active-course
    slots. Recovery is a correctness requirement, not a nicety.

    Deliberately does *not* re-queue automatically: one silent retry loop is
    how you get runaway AI spend. The learner (or an admin) retries explicitly.
    """
    cutoff = timezone.now() - threshold
    stale = CourseGenerationJob.objects.filter(
        status=CourseGenerationJob.STATUS_RUNNING,
    ).filter(
        heartbeat_at__lt=cutoff,
    )

    recovered = 0
    for job in stale.select_related("course"):
        CourseGenerationJob.objects.filter(
            pk=job.pk, status=CourseGenerationJob.STATUS_RUNNING,
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


def _fail_course_for_job(job, *, interrupted=False):
    """Move the owning course out of ``processing`` so it stops holding a slot."""
    message = (
        "Course preparation was interrupted before it finished. You can try again."
        if interrupted
        else "StudyQuest could not finish this course. You can try again."
    )
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