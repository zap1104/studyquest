"""Dashboard and Course Library presentation for in-flight generation.

Covers the state problems found in manual testing:
  * a developer comment rendered as visible text
  * a course stuck reading "Preparing" forever
  * the library claiming to be empty while drafts were being prepared
  * a duplicated Study Focus invitation

Presentation only -- no generation, topic, or Starting Knowledge Check logic.
"""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta

from courses.models import Course, CourseGenerationJob, UserProfile


class DashboardProcessingPresentationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="dashlearner", password="pw12345678")
        UserProfile.objects.get_or_create(user=self.user)
        self.client.login(username="dashlearner", password="pw12345678")

    def _processing(self, title, *, age_minutes=0):
        return Course.objects.create(
            user=self.user,
            title=title,
            status="processing",
            generation_started_at=timezone.now() - timedelta(minutes=age_minutes),
        )

    def test_developer_comment_is_not_rendered(self):
        """A multi-line {# #} comment renders as literal text in Django.

        Django's {# #} syntax is single-line only. A multi-line one is not
        recognised as a comment and leaks into the page, which is exactly what
        happened here. Assert on the leaked prose, not on the delimiter: other
        templates legitimately contain single-line {# #} comments.
        """
        self._processing("Comment Probe")
        resp = self.client.get(reverse("courses:dashboard"))
        body = resp.content.decode()
        self.assertNotIn("Courses currently being generated", body)
        self.assertNotIn("Rendered OUTSIDE the no_courses branch", body)
        self.assertNotIn("would otherwise never appear", body)

    def test_processing_course_appears_for_a_learner_with_no_active_course(self):
        """A learner whose only course is processing must still see it.

        Their next_action.state is 'no_courses', so the section has to live
        outside that branch.
        """
        self._processing("First Ever Course")
        resp = self.client.get(reverse("courses:dashboard"))
        self.assertContains(resp, "Courses Being Prepared")
        self.assertContains(resp, "First Ever Course")

    def test_no_native_confirm_dialogs(self):
        """Destructive actions use the inline confirm, never window.confirm."""
        self._processing("Confirm Probe")
        resp = self.client.get(reverse("courses:dashboard"))
        body = resp.content.decode()
        self.assertNotIn("onsubmit=\"return confirm", body)
        self.assertNotIn("window.confirm", body)
        # The inline confirm is present instead.
        self.assertIn("data-confirm-trigger", body)
        self.assertIn("data-confirm-panel", body)

    def test_processing_row_uses_progress_wording_not_game_name(self):
        """'Course Forge' names the game; the process is 'Preparing Your Course'."""
        self._processing("Naming Probe")
        resp = self.client.get(reverse("courses:dashboard"))
        body = resp.content.decode()
        self.assertIn("View Progress", body)
        self.assertNotIn("Open Course Forge", body)
        self.assertNotIn("Course Forge: Preparing", body)

    def test_multiple_processing_courses_are_grouped_with_a_count(self):
        self._processing("Alpha")
        self._processing("Beta")
        resp = self.client.get(reverse("courses:dashboard"))
        self.assertContains(resp, "Courses Being Prepared")
        self.assertContains(resp, "Alpha")
        self.assertContains(resp, "Beta")
        # One grouped panel, not one full-width card per course.
        self.assertEqual(resp.content.decode().count("prep-panel-head"), 1)

    def test_failed_course_offers_retry_and_remove(self):
        course = Course.objects.create(
            user=self.user, title="Broken Course", status="failed",
            generation_error="Generation did not finish.",
        )
        resp = self.client.get(reverse("courses:dashboard"))
        self.assertContains(resp, "Broken Course")
        self.assertContains(resp, "Try Again")
        self.assertContains(resp, "Remove Draft")
        self.assertContains(resp, reverse("courses:course_retry", args=[course.pk]))

    def test_no_duplicate_study_focus_invitation_for_new_learner(self):
        """The Welcome panel already offers Find My Study Focus.

        Counts rendered CTA buttons, not raw text, so comments and helper copy
        do not produce false positives.
        """
        resp = self.client.get(reverse("courses:dashboard"))
        body = resp.content.decode()
        self.assertIn("Welcome to StudyQuest", body)

        # The focus banner must not also be present for a brand-new learner.
        # Assert on the banner's own heading, not on the shared modal-trigger
        # class (the modal's own JS also references it).
        self.assertNotIn("SET A STUDY FOCUS", body)
        self.assertEqual(body.count("SET A STUDY FOCUS"), 0)

    def test_focus_banner_still_shows_when_a_pending_result_exists(self):
        """A pending school result is a state the Welcome panel does not cover."""
        from courses.models import LearningFocus
        LearningFocus.objects.create(
            user=self.user,
            source=LearningFocus.SOURCE_EXTERNAL,
            status=LearningFocus.STATUS_PENDING,
            subject_name="Pending Subject",
        )
        resp = self.client.get(reverse("courses:dashboard"))
        self.assertContains(resp, "Pending Subject")

    def test_processing_learner_still_gets_a_processing_section(self):
        self._processing("Waiting Course")
        resp = self.client.get(reverse("courses:dashboard"))
        body = resp.content.decode()
        self.assertIn("Courses Being Prepared", body)
        self.assertIn("Waiting Course", body)


class CourseLibraryProcessingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="liblearner", password="pw12345678")
        UserProfile.objects.get_or_create(user=self.user)
        self.client.login(username="liblearner", password="pw12345678")

    def test_empty_library_message_suppressed_when_processing_exists(self):
        Course.objects.create(user=self.user, title="Draft One", status="processing")
        resp = self.client.get(reverse("courses:course_list"))
        self.assertNotContains(resp, "Your library is empty")
        self.assertContains(resp, "being prepared")

    def test_empty_library_message_suppressed_when_failed_exists(self):
        Course.objects.create(
            user=self.user, title="Draft Two", status="failed",
            generation_error="Did not finish.",
        )
        resp = self.client.get(reverse("courses:course_list"))
        self.assertNotContains(resp, "Your library is empty")

    def test_truly_empty_library_still_shows_empty_state(self):
        resp = self.client.get(reverse("courses:course_list"))
        self.assertContains(resp, "Your library is empty")

    def test_preparing_count_shown(self):
        Course.objects.create(user=self.user, title="Draft A", status="processing")
        Course.objects.create(user=self.user, title="Draft B", status="processing")
        resp = self.client.get(reverse("courses:course_list"))
        self.assertContains(resp, "Preparing (2)")

    def test_failed_card_offers_only_retry_and_remove(self):
        """No link to the game screen for a course that will never generate."""
        course = Course.objects.create(
            user=self.user, title="Dead Course", status="failed",
            generation_error="Generation did not finish.",
        )
        resp = self.client.get(reverse("courses:course_list"))
        body = resp.content.decode()
        self.assertIn("Try Again", body)
        self.assertIn("Remove Draft", body)
        self.assertNotIn(
            reverse("courses:course_generating", args=[course.pk]), body
        )

    def test_processing_card_still_links_to_the_game(self):
        course = Course.objects.create(
            user=self.user, title="Live Course", status="processing",
        )
        resp = self.client.get(reverse("courses:course_list"))
        self.assertContains(
            resp, reverse("courses:course_generating", args=[course.pk])
        )

    def test_library_ownership_is_preserved(self):
        """Another learner's drafts must never appear."""
        other = User.objects.create_user(username="otherlearner", password="pw12345678")
        Course.objects.create(user=other, title="Not Yours", status="processing")
        resp = self.client.get(reverse("courses:course_list"))
        self.assertNotContains(resp, "Not Yours")


class StaleQueuedJobRecoveryTests(TestCase):
    """A job that was never claimed must not read 'Preparing' forever."""

    def setUp(self):
        self.user = User.objects.create_user(username="stalec", password="pw12345678")
        self.course = Course.objects.create(
            user=self.user, title="Never Started", status="processing",
            generation_started_at=timezone.now(),
        )

    def test_never_claimed_job_is_left_queued(self):
        """A queued job must NOT be reclaimed by the worker's recovery pass.

        The worker runs recovery at startup, before claiming work. An earlier
        version reclaimed queued jobs there and destroyed the very job the
        worker was about to process.
        """
        from courses.course_generation_queue import (
            enqueue_course_generation, recover_stale_jobs,
        )
        job = enqueue_course_generation(self.course, source_bundle={})
        CourseGenerationJob.objects.filter(pk=job.pk).update(
            queued_at=timezone.now() - timedelta(minutes=30),
        )

        # Recovery must leave it alone...
        self.assertEqual(recover_stale_jobs(), 0)
        job.refresh_from_db()
        self.assertEqual(job.status, CourseGenerationJob.STATUS_QUEUED)

        # ...and the worker must still be able to claim it afterwards.
        from courses.course_generation_queue import claim_next_job
        claimed = claim_next_job("worker-a")
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed.pk, job.pk)

    def test_abandoned_queued_jobs_are_reported_not_killed(self):
        """A job waiting for a worker is reported, never discarded."""
        from courses.course_generation_queue import (
            enqueue_course_generation, reconcile_abandoned_queued,
        )
        job = enqueue_course_generation(self.course, source_bundle={})
        CourseGenerationJob.objects.filter(pk=job.pk).update(
            queued_at=timezone.now() - timedelta(minutes=10),
        )

        self.assertEqual(reconcile_abandoned_queued(), 1)
        job.refresh_from_db()
        # Still queued: the learner's work is preserved.
        self.assertEqual(job.status, CourseGenerationJob.STATUS_QUEUED)
        self.course.refresh_from_db()
        self.assertEqual(self.course.status, "processing")

    def test_recently_queued_job_is_not_reported(self):
        """A job queued moments ago is legitimately waiting."""
        from courses.course_generation_queue import (
            enqueue_course_generation, reconcile_abandoned_queued,
        )
        enqueue_course_generation(self.course, source_bundle={})
        self.assertEqual(reconcile_abandoned_queued(), 0)

    def test_running_job_with_dead_heartbeat_still_recovered(self):
        """The original case must keep working."""
        from courses.course_generation_queue import (
            enqueue_course_generation, claim_next_job, recover_stale_jobs,
        )
        job = enqueue_course_generation(self.course, source_bundle={})
        claim_next_job("worker-a")
        CourseGenerationJob.objects.filter(pk=job.pk).update(
            heartbeat_at=timezone.now() - timedelta(hours=1),
        )
        self.assertEqual(recover_stale_jobs(), 1)
        job.refresh_from_db()
        self.assertEqual(job.error_code, "worker_interrupted")