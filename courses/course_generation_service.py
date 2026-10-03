"""Request-independent course generation use case.

The management-command worker calls exactly one function from here:
``execute_course_generation(job_id)``.

Design rules this module must keep:

* No ``request`` object, no request-scoped file handles.
* Never hold a database transaction across the AI call -- it can take minutes
  and would pin a write lock (fatal on SQLite).
* Persist progress in small isolated writes so a crash leaves an inspectable
  trail rather than a stuck row.
* Store learner-safe error text only. Provider prompts, raw API responses and
  credentials must never reach ``Course.generation_error`` or the API.
"""
import logging

from django.db import close_old_connections, transaction
from django.utils import timezone

from .course_generation_queue import (
    mark_job_failed,
    mark_job_succeeded,
    set_stage,
    touch_heartbeat,
)
from .models import Course, CourseGenerationJob
from .services import (
    CourseGenerationError,
    SourceBundleError,
    generate_course_journey_from_bundle,
    persist_journey,
)

logger = logging.getLogger(__name__)


class GenerationOutcome:
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


def _safe_message(exc, fallback):
    """Reduce an exception to something safe to show a learner.

    Provider errors can embed request URLs, prompts or key fragments. Anything
    that is not one of our own deliberate, learner-facing messages is replaced
    with a generic string.
    """
    if isinstance(exc, (CourseGenerationError, SourceBundleError)):
        return str(exc)
    return fallback


def _fail(course, message, *, stage=CourseGenerationJob.STAGE_FAILED):
    """Move the course out of ``processing`` and record a safe message."""
    Course.objects.filter(pk=course.pk).update(
        status="failed",
        generation_error=message,
        generation_stage=stage,
        generation_completed_at=timezone.now(),
    )


def execute_course_generation(job_id):
    """Run one generation job end to end.

    Returns one of the ``GenerationOutcome`` values. Never raises for an
    ordinary generation failure -- failures are recorded on the job and the
    course so the status endpoint can report them.
    """
    # A worker process may have been idle for a long time; do not reuse a
    # connection that the database has already dropped.
    close_old_connections()

    try:
        job = CourseGenerationJob.objects.select_related("course").get(pk=job_id)
    except CourseGenerationJob.DoesNotExist:
        logger.error("Generation job %s no longer exists.", job_id)
        return GenerationOutcome.SKIPPED

    if job.status != CourseGenerationJob.STATUS_RUNNING:
        # Already finished or reclaimed by another worker. Do not double-run.
        logger.warning(
            "Job %s is %s, not running; skipping execution.", job_id, job.status,
        )
        return GenerationOutcome.SKIPPED

    course = job.course

    # A job for a course that is no longer processing is stale by definition.
    if course.status != "processing":
        logger.warning(
            "Course %s is %s, not processing; marking job skipped.",
            course.pk, course.status,
        )
        mark_job_failed(
            job, error_code="course_not_processing",
            error_message="This course is no longer being prepared.",
        )
        return GenerationOutcome.SKIPPED

    try:
        # --- Stage: reading sources (already extracted in the request) ---
        set_stage(job, CourseGenerationJob.STAGE_READING_SOURCES)

        # --- Stage: generating (the slow AI call, outside any transaction) ---
        set_stage(job, CourseGenerationJob.STAGE_PREPARING_CONTENT)
        touch_heartbeat(job)

        journey_data = generate_course_journey_from_bundle(
            job.custom_title or course.title,
            job.source_bundle,
            study_goal=(job.generation_profile or {}).get("study_goal", "balanced_review"),
            assessment_formats=(job.generation_profile or {}).get("assessment_formats"),
            study_focus=job.study_focus,
        )

        # --- Stage: validating ---
        set_stage(job, CourseGenerationJob.STAGE_VALIDATING_COURSE)
        journey_data["generation_profile"] = job.generation_profile or {}

        # --- Stage: saving (short, transactional write) ---
        set_stage(job, CourseGenerationJob.STAGE_SAVING_COURSE)
        touch_heartbeat(job)

        _persist_and_activate(job, course, journey_data)

    except CourseGenerationError as err:
        logger.error("[Course Generation Failed] job=%s: %s", job_id, err)
        message = _safe_message(
            err, "StudyQuest could not finish this course. You can try again.",
        )
        _fail(course, message)
        mark_job_failed(job, error_code="generation_failed", error_message=message)
        return GenerationOutcome.FAILED

    except SourceBundleError as err:
        logger.warning("[Source Bundle Error] job=%s: %s", job_id, err)
        message = _safe_message(
            err, "The uploaded study material could not be used to build a course.",
        )
        _fail(course, message)
        mark_job_failed(job, error_code="source_bundle_error", error_message=message)
        return GenerationOutcome.FAILED

    except Exception as err:
        # Never leak the raw exception to the learner.
        logger.exception("[Course Generation Crashed] job=%s", job_id)
        message = "Course generation encountered an unexpected error. Please try again."
        _fail(course, message)
        mark_job_failed(job, error_code="unexpected_error", error_message=message)
        return GenerationOutcome.FAILED

    finally:
        close_old_connections()

    return GenerationOutcome.SUCCEEDED


@transaction.atomic
def _persist_and_activate(job, course, journey_data):
    """Persist chapters/quizzes/questions and flip the course to active.

    Isolated in its own short transaction so a failure here cannot leave a
    half-written course marked active.
    """
    from .credits import consume_generation_credit

    # Re-read under a lock so two workers can never both persist this course.
    locked = Course.objects.select_for_update().get(pk=course.pk)
    if locked.status != "processing":
        raise CourseGenerationError(
            "This course is no longer being prepared."
        )

    # Clear any partial chapters from an earlier interrupted attempt so a retry
    # cannot duplicate content.
    locked.chapters.all().delete()

    persist_journey(
        locked,
        journey_override=journey_data,
        custom_title=job.custom_title or locked.title,
    )

    locked.status = "active"
    locked.generation_stage = CourseGenerationJob.STAGE_COMPLETE
    locked.generation_completed_at = timezone.now()
    locked.generation_error = ""
    locked.save(update_fields=[
        "status", "generation_stage", "generation_completed_at", "generation_error",
    ])

    # Credit is consumed only on success, matching the existing product promise
    # that a failed generation costs the learner nothing. The unique
    # reference_key on the ledger makes this idempotent per course.
    consume_generation_credit(locked.user, locked)

    mark_job_succeeded(job)