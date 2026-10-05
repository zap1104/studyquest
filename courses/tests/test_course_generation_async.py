"""Tests for asynchronous course generation (Course Forge Sprint 2).

Covers the durable job boundary: queueing, atomic claiming, idempotency,
worker execution, failure recording, stale-job recovery, and the ownership-safe
status endpoint.
"""
import json
import uuid
from datetime import timedelta

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from unittest.mock import patch
from django.urls import reverse
from django.utils import timezone

from courses.course_generation_queue import (
    claim_next_job,
    enqueue_course_generation,
    has_open_job,
    mark_job_failed,
    recover_stale_jobs,
)
from courses.course_generation_service import (
    GenerationOutcome,
    execute_course_generation,
)
from courses.models import (
    Course,
    CourseCreditTransaction,
    CourseGenerationJob,
    UserProfile,
)
from courses.services import SourceBundleError, extract_and_bundle_sources


def make_bundle(text="Enterprise architecture lecture notes covering TOGAF and BDAT domains."):
    f = SimpleUploadedFile("notes.txt", text.encode(), content_type="text/plain")
    return extract_and_bundle_sources([f], plan_name="free")


class JobModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="jobstudent", password="pw12345678")
        self.course = Course.objects.create(
            user=self.user, title="Test Course", status="processing",
        )

    def test_enqueue_creates_queued_job(self):
        job = enqueue_course_generation(
            self.course, source_bundle=make_bundle(), plan_name="free",
        )
        self.assertEqual(job.status, CourseGenerationJob.STATUS_QUEUED)
        self.assertEqual(job.stage, CourseGenerationJob.STAGE_QUEUED)
        self.assertFalse(job.is_finished)
        self.assertTrue(has_open_job(self.course))

    def test_attempt_id_is_unique(self):
        a = enqueue_course_generation(self.course, source_bundle={})
        b = enqueue_course_generation(self.course, source_bundle={})
        self.assertNotEqual(a.attempt_id, b.attempt_id)

    def test_enqueue_requires_a_course_instance(self):
        from courses.course_generation_queue import QueueError
        with self.assertRaises(QueueError):
            enqueue_course_generation("not-a-course", source_bundle={})

    def test_source_bundle_is_persisted(self):
        """The worker must never need the original upload handles."""
        bundle = make_bundle()
        job = enqueue_course_generation(self.course, source_bundle=bundle)
        job.refresh_from_db()
        self.assertIn("TOGAF", job.source_bundle["bundled_text"])
        self.assertEqual(job.source_bundle["filenames"], ["notes.txt"])

    def test_learner_stage_messages_never_leak_implementation(self):
        job = enqueue_course_generation(self.course, source_bundle={})
        for stage, _ in CourseGenerationJob.STAGE_CHOICES:
            job.stage = stage
            message = job.learner_stage_message().lower()
            for forbidden in ("gemini", "pydantic", "_build_journey", "traceback"):
                self.assertNotIn(forbidden, message)


class JobClaimTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="claimstudent", password="pw12345678")
        self.course = Course.objects.create(
            user=self.user, title="Claim Course", status="processing",
        )

    def test_claim_marks_running_and_records_worker(self):
        job = enqueue_course_generation(self.course, source_bundle={})
        claimed = claim_next_job("worker-a")
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed.pk, job.pk)
        self.assertEqual(claimed.status, CourseGenerationJob.STATUS_RUNNING)
        self.assertEqual(claimed.claimed_by, "worker-a")
        self.assertIsNotNone(claimed.started_at)

    def test_two_workers_cannot_claim_the_same_job(self):
        """The conditional UPDATE is the whole safety mechanism."""
        enqueue_course_generation(self.course, source_bundle={})
        first = claim_next_job("worker-a")
        second = claim_next_job("worker-b")
        self.assertIsNotNone(first)
        self.assertIsNone(second)

    def test_claim_returns_none_on_empty_queue(self):
        self.assertIsNone(claim_next_job("worker-a"))

    def test_oldest_job_is_claimed_first(self):
        other = Course.objects.create(user=self.user, title="Second", status="processing")
        first_job = enqueue_course_generation(self.course, source_bundle={})
        enqueue_course_generation(other, source_bundle={})
        self.assertEqual(claim_next_job("worker-a").pk, first_job.pk)


class WorkerExecutionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="workerstudent", password="pw12345678")
        UserProfile.objects.get_or_create(user=self.user)
        self.course = Course.objects.create(
            user=self.user, title="Worker Course", status="processing",
            generation_started_at=timezone.now(),
        )

    @override_settings(USE_MOCK_COURSE_GENERATION=True)
    @patch('courses.services.client', None)
    def test_successful_generation_activates_course(self):
        job = enqueue_course_generation(self.course, source_bundle=make_bundle())
        claim_next_job("worker-a")

        outcome = execute_course_generation(job.pk)
        self.assertEqual(outcome, GenerationOutcome.SUCCEEDED)

        self.course.refresh_from_db()
        job.refresh_from_db()
        self.assertEqual(self.course.status, "active")
        self.assertEqual(job.status, CourseGenerationJob.STATUS_SUCCEEDED)
        self.assertEqual(job.stage, CourseGenerationJob.STAGE_COMPLETE)
        self.assertTrue(self.course.chapters.exists())
        self.assertIsNotNone(self.course.generation_completed_at)

    @override_settings(USE_MOCK_COURSE_GENERATION=True)
    @patch('courses.services.client', None)
    def test_credit_consumed_exactly_once_on_success(self):
        job = enqueue_course_generation(self.course, source_bundle=make_bundle())
        claim_next_job("worker-a")
        execute_course_generation(job.pk)
        self.assertEqual(
            CourseCreditTransaction.objects.filter(
                user=self.user, related_course=self.course,
            ).count(),
            1,
        )

    @override_settings(USE_MOCK_COURSE_GENERATION=True)
    @patch('courses.services.client', None)
    def test_job_not_running_is_skipped(self):
        """A queued job must not execute before it is claimed."""
        job = enqueue_course_generation(self.course, source_bundle=make_bundle())
        outcome = execute_course_generation(job.pk)
        self.assertEqual(outcome, GenerationOutcome.SKIPPED)
        self.course.refresh_from_db()
        self.assertEqual(self.course.status, "processing")

    @override_settings(USE_MOCK_COURSE_GENERATION=True)
    @patch('courses.services.client', None)
    def test_executing_twice_is_idempotent(self):
        """A double POST or replayed job must not duplicate chapters."""
        job = enqueue_course_generation(self.course, source_bundle=make_bundle())
        claim_next_job("worker-a")
        execute_course_generation(job.pk)
        chapters_after_first = self.course.chapters.count()

        second = execute_course_generation(job.pk)
        self.assertEqual(second, GenerationOutcome.SKIPPED)
        self.course.refresh_from_db()
        self.assertEqual(self.course.chapters.count(), chapters_after_first)

    @override_settings(USE_MOCK_COURSE_GENERATION=False)
    def test_insufficient_source_text_fails_safely(self):
        """Short bundles fail before any provider call, so this stays offline."""
        job = enqueue_course_generation(
            self.course, source_bundle={"bundled_text": "tiny", "filenames": ["a.txt"]},
        )
        claim_next_job("worker-a")
        outcome = execute_course_generation(job.pk)

        self.assertEqual(outcome, GenerationOutcome.FAILED)
        self.course.refresh_from_db()
        job.refresh_from_db()
        self.assertEqual(self.course.status, "failed")
        self.assertEqual(job.status, CourseGenerationJob.STATUS_FAILED)
        self.assertTrue(self.course.generation_error)
        # Failed generation costs the learner nothing.
        self.assertEqual(
            CourseCreditTransaction.objects.filter(user=self.user).count(), 0,
        )

    @override_settings(USE_MOCK_COURSE_GENERATION=True)
    @patch('courses.services.client', None)
    def test_worker_crash_records_safe_message(self):
        """A raw exception must never reach the learner verbatim."""
        job = enqueue_course_generation(self.course, source_bundle=make_bundle())
        claim_next_job("worker-a")

        from unittest.mock import patch
        with patch(
            "courses.course_generation_service.generate_course_journey_from_bundle",
            side_effect=RuntimeError("SECRET internal detail /api/key=abc123"),
        ):
            outcome = execute_course_generation(job.pk)

        self.assertEqual(outcome, GenerationOutcome.FAILED)
        self.course.refresh_from_db()
        self.assertNotIn("SECRET", self.course.generation_error)
        self.assertNotIn("abc123", self.course.generation_error)

    @override_settings(USE_MOCK_COURSE_GENERATION=True)
    @patch('courses.services.client', None)
    def test_job_for_non_processing_course_is_skipped(self):
        self.course.status = "active"
        self.course.save(update_fields=["status"])
        job = enqueue_course_generation(self.course, source_bundle=make_bundle())
        claim_next_job("worker-a")
        outcome = execute_course_generation(job.pk)
        self.assertEqual(outcome, GenerationOutcome.SKIPPED)

    @override_settings(USE_MOCK_COURSE_GENERATION=True)
    @patch('courses.services.client', None)
    def test_missing_job_is_skipped_not_crashed(self):
        self.assertEqual(
            execute_course_generation(uuid.uuid4()), GenerationOutcome.SKIPPED,
        )


class StaleJobRecoveryTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="stalestudent", password="pw12345678")
        self.course = Course.objects.create(
            user=self.user, title="Stale Course", status="processing",
            generation_started_at=timezone.now() - timedelta(hours=1),
        )

    def test_stale_running_job_is_interrupted_and_frees_the_course(self):
        job = enqueue_course_generation(self.course, source_bundle=make_bundle())
        claim_next_job("worker-a")

        # Simulate a worker that died an hour ago.
        CourseGenerationJob.objects.filter(pk=job.pk).update(
            heartbeat_at=timezone.now() - timedelta(hours=1),
        )

        recovered = recover_stale_jobs()
        self.assertEqual(recovered, 1)

        job.refresh_from_db()
        self.course.refresh_from_db()
        self.assertEqual(job.status, CourseGenerationJob.STATUS_INTERRUPTED)
        self.assertEqual(self.course.status, "failed")
        self.assertTrue(self.course.generation_error)

    def test_fresh_running_job_is_left_alone(self):
        job = enqueue_course_generation(self.course, source_bundle=make_bundle())
        claim_next_job("worker-a")
        self.assertEqual(recover_stale_jobs(), 0)
        job.refresh_from_db()
        self.assertEqual(job.status, CourseGenerationJob.STATUS_RUNNING)

    def test_recovery_does_not_auto_retry(self):
        """Silent retry loops are how runaway AI spend happens."""
        job = enqueue_course_generation(self.course, source_bundle=make_bundle())
        claim_next_job("worker-a")
        CourseGenerationJob.objects.filter(pk=job.pk).update(
            heartbeat_at=timezone.now() - timedelta(hours=1),
        )
        recover_stale_jobs()
        self.assertEqual(
            CourseGenerationJob.objects.filter(
                status=CourseGenerationJob.STATUS_QUEUED,
            ).count(),
            0,
        )

    def test_stale_processing_course_stops_holding_an_active_slot(self):
        """A genuinely stranded course must not consume the course allowance.

        Age alone is not abandonment -- a course is only released once its job
        is finished and it is still stuck in ``processing``.
        """
        from courses.credits import get_active_course_count

        # Fresh processing course with a live queued job: counts.
        Course.objects.filter(pk=self.course.pk).update(
            generation_started_at=timezone.now(),
        )
        job = enqueue_course_generation(self.course, source_bundle=make_bundle())
        self.assertEqual(get_active_course_count(self.user), 1)

        # Old, but still has a live queued job: still counts.
        Course.objects.filter(pk=self.course.pk).update(
            generation_started_at=timezone.now() - timedelta(hours=2),
        )
        self.assertEqual(get_active_course_count(self.user), 1)

        # Now the worker dies and the job is recovered: the course stops
        # holding a slot, so the learner can create another course.
        claim_next_job("worker-a")
        CourseGenerationJob.objects.filter(pk=job.pk).update(
            heartbeat_at=timezone.now() - timedelta(hours=1),
        )
        recover_stale_jobs()
        self.assertEqual(get_active_course_count(self.user), 0)


class NonBlockingCourseCreateTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="asyncstudent", password="pw12345678")
        UserProfile.objects.get_or_create(user=self.user)
        self.client.login(username="asyncstudent", password="pw12345678")

    @override_settings(USE_MOCK_COURSE_GENERATION=True)
    @patch('courses.services.client', None)
    def test_post_returns_immediately_with_processing_course(self):
        f = SimpleUploadedFile("notes.txt", b"Data structures lecture notes.", content_type="text/plain")
        resp = self.client.post(
            reverse("courses:course_create"),
            {"custom_title": "Async Course", "content_files": [f]},
        )
        # Redirect, not a rendered page after a multi-minute wait.
        self.assertEqual(resp.status_code, 302)
        course = Course.objects.get(title="Async Course")
        self.assertIn(reverse("courses:course_generating", args=[course.pk]), resp.url)

        # The course is processing with no chapters yet, and a job is queued.
        self.assertEqual(course.status, "processing")
        self.assertFalse(course.chapters.exists())
        self.assertEqual(course.generation_jobs.count(), 1)
        self.assertEqual(
            course.generation_jobs.first().status,
            CourseGenerationJob.STATUS_QUEUED,
        )

    @override_settings(USE_MOCK_COURSE_GENERATION=True)
    @patch('courses.services.client', None)
    def test_processing_course_does_not_consume_a_credit(self):
        f = SimpleUploadedFile("notes.txt", b"Data structures lecture notes.", content_type="text/plain")
        self.client.post(
            reverse("courses:course_create"),
            {"custom_title": "No Credit Yet", "content_files": [f]},
        )
        self.assertEqual(CourseCreditTransaction.objects.filter(user=self.user).count(), 0)

    @override_settings(USE_MOCK_COURSE_GENERATION=True)
    @patch('courses.services.client', None)
    def test_extraction_failure_creates_no_course_and_no_job(self):
        f = SimpleUploadedFile("bad.sh", b"#!/bin/bash", content_type="text/plain")
        resp = self.client.post(
            reverse("courses:course_create"),
            {"custom_title": "Bad Course", "content_files": [f]},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context["generation_failed"])
        self.assertFalse(Course.objects.filter(title="Bad Course").exists())
        self.assertEqual(CourseGenerationJob.objects.count(), 0)

    @override_settings(USE_MOCK_COURSE_GENERATION=True)
    @patch('courses.services.client', None)
    def test_unauthenticated_post_is_rejected(self):
        self.client.logout()
        resp = self.client.post(reverse("courses:course_create"), {"custom_title": "X"})
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login/", resp.url)


class GenerationStatusEndpointTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="statusstudent", password="pw12345678")
        self.other = User.objects.create_user(username="nosy", password="pw12345678")
        self.course = Course.objects.create(
            user=self.user, title="Status Course", status="processing",
            generation_started_at=timezone.now(),
        )
        self.client.login(username="statusstudent", password="pw12345678")

    def test_owner_sees_processing_status(self):
        job = enqueue_course_generation(self.course, source_bundle=make_bundle())
        resp = self.client.get(reverse("courses:generation_status", args=[self.course.pk]))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "processing")
        self.assertIsNone(data["ready_url"])
        self.assertFalse(data["failed"])
        self.assertTrue(data["message"])

    def test_active_course_returns_ready_url(self):
        self.course.status = "active"
        self.course.save(update_fields=["status"])
        resp = self.client.get(reverse("courses:generation_status", args=[self.course.pk]))
        data = resp.json()
        self.assertEqual(data["status"], "active")
        self.assertEqual(data["ready_url"], reverse("courses:course_detail", args=[self.course.pk]))

    def test_failed_course_returns_retry_url_and_safe_message(self):
        self.course.status = "failed"
        self.course.generation_error = "StudyQuest could not finish this course."
        self.course.save(update_fields=["status", "generation_error"])
        resp = self.client.get(reverse("courses:generation_status", args=[self.course.pk]))
        data = resp.json()
        self.assertTrue(data["failed"])
        self.assertEqual(data["retry_url"], reverse("courses:course_retry", args=[self.course.pk]))
        self.assertNotIn("traceback", data["message"].lower())

    def test_non_owner_gets_404(self):
        self.client.logout()
        self.client.login(username="nosy", password="pw12345678")
        resp = self.client.get(reverse("courses:generation_status", args=[self.course.pk]))
        self.assertEqual(resp.status_code, 404)

    def test_endpoint_is_get_only(self):
        resp = self.client.post(reverse("courses:generation_status", args=[self.course.pk]))
        self.assertEqual(resp.status_code, 405)

    def test_response_never_includes_structured_content(self):
        self.course.structured_content = {"secret": "answer key"}
        self.course.save(update_fields=["structured_content"])
        resp = self.client.get(reverse("courses:generation_status", args=[self.course.pk]))
        self.assertNotIn("answer key", resp.content.decode())

    def test_long_queued_job_reports_worker_waiting(self):
        job = enqueue_course_generation(self.course, source_bundle=make_bundle())
        CourseGenerationJob.objects.filter(pk=job.pk).update(
            queued_at=timezone.now() - timedelta(minutes=5),
        )
        resp = self.client.get(reverse("courses:generation_status", args=[self.course.pk]))
        self.assertTrue(resp.json().get("worker_waiting"))


class CourseGeneratingPageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="pagestudent", password="pw12345678")
        self.course = Course.objects.create(
            user=self.user, title="Forge Page Course", status="processing",
            generation_started_at=timezone.now(),
        )
        self.client.login(username="pagestudent", password="pw12345678")

    def test_page_renders_for_owner(self):
        resp = self.client.get(reverse("courses:course_generating", args=[self.course.pk]))
        self.assertEqual(resp.status_code, 200)
        # The page is about preparing the course; "Course Forge" names the
        # optional game and appears only as the game's own label.
        self.assertContains(resp, "PREPARING YOUR COURSE")
        self.assertContains(resp, "data-forge-status")
        self.assertContains(resp, "Course Forge")

    def test_page_works_without_javascript(self):
        """A status page, not an app: it must render meaningfully server-side."""
        enqueue_course_generation(self.course, source_bundle=make_bundle())
        resp = self.client.get(reverse("courses:course_generating", args=[self.course.pk]))
        self.assertContains(resp, "You can leave this page")
        self.assertContains(resp, "<noscript>")

    def test_active_course_redirects_to_detail(self):
        self.course.status = "active"
        self.course.save(update_fields=["status"])
        resp = self.client.get(reverse("courses:course_generating", args=[self.course.pk]))
        self.assertEqual(resp.status_code, 302)

    def test_non_owner_gets_404(self):
        other = User.objects.create_user(username="peeker", password="pw12345678")
        self.client.logout()
        self.client.login(username="peeker", password="pw12345678")
        resp = self.client.get(reverse("courses:course_generating", args=[self.course.pk]))
        self.assertEqual(resp.status_code, 404)

    def test_failed_course_redirects_to_library_not_the_game(self):
        """A failed course has nothing to wait for, so the game screen is wrong.

        The learner is sent to the library, where the failed card offers
        Try Again and Remove Draft.
        """
        self.course.status = "failed"
        self.course.generation_error = "Generation did not finish."
        self.course.save(update_fields=["status", "generation_error"])

        resp = self.client.get(reverse("courses:course_generating", args=[self.course.pk]))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("courses:course_list"))

    def test_failed_redirect_does_not_render_the_minigame(self):
        self.course.status = "failed"
        self.course.save(update_fields=["status"])
        resp = self.client.get(
            reverse("courses:course_generating", args=[self.course.pk]), follow=True
        )
        body = resp.content.decode()
        self.assertNotIn("data-camp-root", body)
        self.assertNotIn("data-forge-status", body)

    def test_processing_course_still_renders_the_game(self):
        """The Forge remains the destination while generation is in flight."""
        resp = self.client.get(reverse("courses:course_generating", args=[self.course.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "data-camp-root")
        self.assertContains(resp, "data-forge-status")

    def test_archived_course_redirects_to_library(self):
        self.course.status = "archived"
        self.course.save(update_fields=["status"])
        resp = self.client.get(reverse("courses:course_generating", args=[self.course.pk]))
        self.assertEqual(resp.status_code, 302)


class CourseRetryTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="retrystudent", password="pw12345678")
        UserProfile.objects.get_or_create(user=self.user)
        self.course = Course.objects.create(
            user=self.user, title="Retry Course", status="processing",
            generation_started_at=timezone.now(),
        )
        self.client.login(username="retrystudent", password="pw12345678")

    @override_settings(USE_MOCK_COURSE_GENERATION=True)
    @patch('courses.services.client', None)
    def test_retry_requeues_using_persisted_bundle(self):
        from unittest.mock import patch

        job = enqueue_course_generation(self.course, source_bundle=make_bundle())
        claim_next_job("worker-a")

        # Force the first attempt to fail without touching the network.
        with patch(
            "courses.course_generation_service.generate_course_journey_from_bundle",
            side_effect=SourceBundleError("Source material could not be used."),
        ):
            execute_course_generation(job.pk)

        self.course.refresh_from_db()
        self.assertEqual(self.course.status, "failed")

        resp = self.client.post(reverse("courses:course_retry", args=[self.course.pk]))
        self.assertEqual(resp.status_code, 302)

        self.course.refresh_from_db()
        self.assertEqual(self.course.status, "processing")
        self.assertEqual(self.course.generation_jobs.count(), 2)

        newest = self.course.generation_jobs.order_by("-queued_at").first()
        self.assertEqual(newest.retry_count, 1)
        # The bundle is reused, so the learner never re-uploads.
        self.assertIn("TOGAF", newest.source_bundle["bundled_text"])

    def test_retry_on_active_course_redirects_to_detail(self):
        self.course.status = "active"
        self.course.save(update_fields=["status"])
        resp = self.client.post(reverse("courses:course_retry", args=[self.course.pk]))
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse("courses:course_detail", args=[self.course.pk]), resp.url)

    def test_retry_with_no_previous_bundle_sends_to_create(self):
        resp = self.client.post(reverse("courses:course_retry", args=[self.course.pk]))
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse("courses:course_create"), resp.url)

    def test_retry_does_not_duplicate_an_open_job(self):
        enqueue_course_generation(self.course, source_bundle=make_bundle())
        before = self.course.generation_jobs.count()
        self.client.post(reverse("courses:course_retry", args=[self.course.pk]))
        self.assertEqual(self.course.generation_jobs.count(), before)

    def test_retry_is_post_only(self):
        resp = self.client.get(reverse("courses:course_retry", args=[self.course.pk]))
        self.assertEqual(resp.status_code, 405)


class WorkerCommandTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="cmdstudent", password="pw12345678")
        UserProfile.objects.get_or_create(user=self.user)
        self.course = Course.objects.create(
            user=self.user, title="Command Course", status="processing",
            generation_started_at=timezone.now(),
        )

    @override_settings(USE_MOCK_COURSE_GENERATION=True)
    @patch('courses.services.client', None)
    def test_worker_command_processes_one_job(self):
        from io import StringIO
        from django.core.management import call_command

        enqueue_course_generation(self.course, source_bundle=make_bundle())
        out = StringIO()
        call_command("run_course_generation_worker", "--once", stdout=out)

        self.course.refresh_from_db()
        self.assertEqual(self.course.status, "active")
        self.assertIn("succeeded", out.getvalue().lower())

    def test_worker_command_exits_cleanly_on_empty_queue(self):
        from io import StringIO
        from django.core.management import call_command

        out = StringIO()
        call_command("run_course_generation_worker", "--once", stdout=out)
        self.assertIn("no queued jobs", out.getvalue().lower())


class CourseForgeCampTemplateTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="campstudent", password="pw12345678")
        UserProfile.objects.get_or_create(user=self.user)
        self.client.login(username="campstudent", password="pw12345678")
        self.course = Course.objects.create(
            user=self.user, title="SIA Architecture", status="processing"
        )

    def test_course_generating_renders_camp_slot_and_scripts(self):
        resp = self.client.get(reverse("courses:course_generating", args=[self.course.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "data-camp-root")
        self.assertContains(resp, f'data-course-id="{self.course.pk}"')
        self.assertContains(resp, "courses/js/forge/forge_state.js")
        self.assertContains(resp, "courses/js/forge/forge_input.js")
        self.assertContains(resp, "courses/js/forge/forge_camera.js")
        self.assertContains(resp, "courses/js/forge/forge_spatial.js")
        self.assertContains(resp, "courses/js/forge/forge_entities.js")
        self.assertContains(resp, "courses/js/forge/forge_renderer.js")
        self.assertContains(resp, "courses/js/forge/forge_camp.js")
        self.assertContains(resp, "courses/js/course_forge.js")

    def test_course_list_renders_processing_and_failed_courses(self):
        failed_course = Course.objects.create(
            user=self.user,
            title="Calculus Failure",
            status="failed",
            generation_error="Study material was empty.",
        )
        resp = self.client.get(reverse("courses:course_list"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "SIA Architecture")
        self.assertContains(resp, "Preparing")
        self.assertContains(resp, reverse("courses:course_generating", args=[self.course.pk]))
        self.assertContains(resp, "Calculus Failure")
        self.assertContains(resp, "Generation Incomplete")
        # The failed card must NOT link to the game screen: there is nothing to
        # watch, so it offers Try Again and Remove Draft instead.
        self.assertNotContains(
            resp, reverse("courses:course_generating", args=[failed_course.pk])
        )
        self.assertContains(resp, reverse("courses:course_retry", args=[failed_course.pk]))

    def test_dashboard_renders_processing_course_banner(self):
        resp = self.client.get(reverse("courses:dashboard"))
        self.assertEqual(resp.status_code, 200)
        # The processing section is grouped under one heading, with the course
        # title in a compact row rather than its own full-width card.
        self.assertContains(resp, "Courses Being Prepared")
        self.assertContains(resp, "SIA Architecture")
        self.assertContains(resp, reverse("courses:course_generating", args=[self.course.pk]))