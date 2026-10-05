import glob
import io
import os
import re

from django.conf import settings
from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from courses.models import Course, UserProfile


class TemplateCommentHygieneTests(SimpleTestCase):
    """Django's {# #} is SINGLE-LINE ONLY.

    A multi-line {# #} is not recognised as a comment, so it renders into the
    page as visible text. This bug shipped three separate times during the
    Course Forge work, so it is now enforced by a test rather than by care.
    """

    def test_no_multiline_hash_comments_in_templates(self):
        root = os.path.join(settings.BASE_DIR, "courses", "templates")
        offenders = []

        for path in glob.glob(os.path.join(root, "**", "*.html"), recursive=True):
            source = io.open(path, encoding="utf-8").read()
            for match in re.finditer(r"\{#(?:(?!#\}).)*\n(?:(?!#\}).)*#\}", source, re.S):
                line = source[: match.start()].count("\n") + 1
                offenders.append(
                    f"{os.path.relpath(path, settings.BASE_DIR)}:{line}"
                )

        self.assertEqual(
            offenders,
            [],
            "Multi-line {# #} comments render as visible page text. "
            "Use {% comment %}...{% endcomment %} instead. Found: "
            + ", ".join(offenders),
        )


class CourseTitleFromGenerationTests(TestCase):
    """A learner-supplied title wins; otherwise the generated one is used.

    Async generation creates the Course row before the AI has produced
    anything, so without this every background course kept the placeholder
    title ("Untitled Course") even after the model supplied a real one.
    """

    def setUp(self):
        self.user = User.objects.create_user(username="titler", password="pw12345678")
        UserProfile.objects.get_or_create(user=self.user)

    def _journey(self, title):
        return {
            "course": {"title": title, "description": "Generated description."},
            "chapters": [],
        }

    def test_generated_title_replaces_placeholder(self):
        from courses.services import persist_journey

        course = Course.objects.create(
            user=self.user, title="Untitled Course", status="processing",
        )
        persist_journey(course, journey_override=self._journey("Technopreneurship"))
        course.refresh_from_db()
        self.assertEqual(course.title, "Technopreneurship")
        self.assertEqual(course.description, "Generated description.")

    def test_learner_supplied_title_is_never_overwritten(self):
        """The real flow: the course is created WITH the learner's title.

        In production the worker sets the title at creation time, so by the
        time persist_journey runs the learner's title is already on the row.
        It must survive.
        """
        from courses.services import persist_journey

        course = Course.objects.create(
            user=self.user, title="My Own Title", status="processing",
        )
        persist_journey(
            course,
            journey_override=self._journey("AI Chose This"),
            custom_title="My Own Title",
        )
        course.refresh_from_db()
        self.assertEqual(course.title, "My Own Title")

    def test_existing_description_is_not_clobbered(self):
        from courses.services import persist_journey

        course = Course.objects.create(
            user=self.user, title="Untitled Course", status="processing",
            description="Learner wrote this.",
        )
        persist_journey(course, journey_override=self._journey("New Title"))
        course.refresh_from_db()
        self.assertEqual(course.description, "Learner wrote this.")

    def test_worker_never_feeds_the_placeholder_title_to_the_generator(self):
        """The worker must pass only a learner-supplied title.

        Passing the course's current title fed the creation-time placeholder
        ("Untitled Course") into the prompt, and the generated journey echoed
        that placeholder straight back as the final course title. Fixing
        persist_journey alone did not help; the placeholder had to stop being
        sent to the generator in the first place.
        """
        from unittest.mock import patch
        from django.test import override_settings
        from courses.course_generation_queue import (
            enqueue_course_generation, claim_next_job,
        )
        from courses.course_generation_service import execute_course_generation

        course = Course.objects.create(
            user=self.user, title="Untitled Course", status="processing",
        )
        job = enqueue_course_generation(
            course,
            source_bundle={"bundled_text": "x" * 200, "filenames": ["a.txt"]},
        )
        claim_next_job("worker-a")

        captured = {}

        def fake_generate(course_title, *args, **kwargs):
            captured["title"] = course_title
            return {
                "course": {"title": "Real Generated Title", "description": "d"},
                "chapters": [],
            }

        with override_settings(USE_MOCK_COURSE_GENERATION=True):
            with patch(
                "courses.course_generation_service.generate_course_journey_from_bundle",
                side_effect=fake_generate,
            ):
                execute_course_generation(job.pk)

        self.assertEqual(
            captured.get("title"), "",
            "The worker must not pass the placeholder course title to the "
            "generator, or the generated journey echoes it back.",
        )
        course.refresh_from_db()
        self.assertEqual(course.title, "Real Generated Title")

    def test_worker_passes_a_learner_supplied_title_through(self):
        """A real learner title IS passed to the generator and preserved."""
        from unittest.mock import patch
        from django.test import override_settings
        from courses.course_generation_queue import (
            enqueue_course_generation, claim_next_job,
        )
        from courses.course_generation_service import execute_course_generation

        course = Course.objects.create(
            user=self.user, title="My Chosen Title", status="processing",
        )
        job = enqueue_course_generation(
            course,
            source_bundle={"bundled_text": "x" * 200, "filenames": ["a.txt"]},
            custom_title="My Chosen Title",
        )
        claim_next_job("worker-a")

        with override_settings(USE_MOCK_COURSE_GENERATION=True):
            with patch(
                "courses.course_generation_service.generate_course_journey_from_bundle",
                return_value={
                    "course": {"title": "AI Suggestion", "description": "d"},
                    "chapters": [],
                },
            ):
                execute_course_generation(job.pk)

        course.refresh_from_db()
        self.assertEqual(course.title, "My Chosen Title")


class FocusCoursePickerTests(TestCase):
    """'Select From My Courses' must list the learner's real courses.

    It previously reused the dashboard's trimmed `recent_courses`, which is
    capped at 3 AND excludes the featured course -- so a learner whose only
    course was currently featured was told they had none.
    """

    def setUp(self):
        self.user = User.objects.create_user(username="picker", password="pw12345678")
        UserProfile.objects.get_or_create(user=self.user)
        self.client.login(username="picker", password="pw12345678")

    def test_single_course_still_offered_even_if_featured(self):
        Course.objects.create(user=self.user, title="Only Course", status="active")
        resp = self.client.get(reverse("courses:dashboard"))
        self.assertContains(resp, "Select From My Courses")
        self.assertContains(resp, "Only Course")
        self.assertNotContains(resp, "No courses yet")

    def test_all_courses_offered_not_just_recent(self):
        for i in range(5):
            Course.objects.create(
                user=self.user, title=f"Course {i}", status="active",
            )
        resp = self.client.get(reverse("courses:dashboard"))
        body = resp.content.decode()
        for i in range(5):
            self.assertIn(f"Course {i}", body)

    def test_processing_only_learner_sees_materials_route_not_course_picker(self):
        """A processing course is not yet selectable as a focus."""
        Course.objects.create(user=self.user, title="Still Cooking", status="processing")
        resp = self.client.get(reverse("courses:dashboard"))
        body = resp.content.decode()
        self.assertIn("Start With My Materials", body)
        self.assertNotIn('data-route="courses"', body)


class DuplicateFocusInvitationTests(TestCase):
    """One Study Focus invitation per screen."""

    def setUp(self):
        self.user = User.objects.create_user(username="focused", password="pw12345678")
        UserProfile.objects.get_or_create(user=self.user)
        self.client.login(username="focused", password="pw12345678")

    def test_returning_learner_without_a_focus_sees_one_explanatory_invitation(self):
        """Exactly one invitation -- and it must be the explanatory one.

        A bare "Set Study Focus" pill in the status-chip row reads as a label,
        not an action, and explains nothing. The focus card states what a Study
        Focus is and why it helps, so the card is what survives.
        """
        Course.objects.create(user=self.user, title="Existing Course", status="active")
        resp = self.client.get(reverse("courses:dashboard"))
        body = resp.content.decode()

        # The explanatory invitation is present, exactly once.
        self.assertIn("SET A STUDY FOCUS", body)
        self.assertEqual(body.count("SET A STUDY FOCUS"), 1)
        self.assertIn("Find My Study Focus", body)
        self.assertIn("tailor practice runs", body)

        # The bare status-row pill is gone.
        self.assertNotIn("Set Study Focus", body)

    def test_new_learner_onboarding_has_one_invitation(self):
        """The Welcome panel already explains Study Focus; no banner on top."""
        resp = self.client.get(reverse("courses:dashboard"))
        body = resp.content.decode()
        self.assertIn("Welcome to StudyQuest", body)
        self.assertIn("Find My Study Focus", body)
        # No duplicate panel stacked above the Welcome card.
        self.assertNotIn("SET A STUDY FOCUS", body)

    def test_active_focus_card_is_still_shown(self):
        """The card carries priority topics the greeting pill does not."""
        from courses.models import LearningFocus
        LearningFocus.objects.create(
            user=self.user,
            source=LearningFocus.SOURCE_MANUAL,
            status=LearningFocus.STATUS_ACTIVE,
            subject_name="Enterprise Systems",
            topic_names=["SOA", "Microservices"],
        )
        Course.objects.create(user=self.user, title="Existing Course", status="active")
        resp = self.client.get(reverse("courses:dashboard"))
        self.assertContains(resp, "SOA")
        self.assertContains(resp, "Microservices")

    def test_pending_focus_card_still_shown(self):
        """A pending school result carries information the pill does not."""
        from courses.models import LearningFocus
        LearningFocus.objects.create(
            user=self.user,
            source=LearningFocus.SOURCE_EXTERNAL,
            status=LearningFocus.STATUS_PENDING,
            subject_name="Pending Subject",
        )
        Course.objects.create(user=self.user, title="Existing Course", status="active")
        resp = self.client.get(reverse("courses:dashboard"))
        self.assertContains(resp, "Pending Subject")