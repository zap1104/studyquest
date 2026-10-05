import logging
from unittest.mock import patch
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import OperationalError
from django.test import TestCase

from courses.models import Course, Chapter, Quiz, Question, Choice, CourseTopic, QuestionTopic
from courses.schemas import GeneratedJourney, GeneratedChapter, GeneratedTopic, GeneratedQuestion, GeneratedQuiz
from courses.services import _generate_mock_journey
from courses.topic_services import create_course_topic, link_question_to_topic
from courses.services import persist_journey as _build_journey
from courses.grading import _grade_question


class GenerationTopicTests(TestCase):
    """Comprehensive test suite for Phase TE-1B: Generation Topic Tagging."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="gen_topic_user", password="test-password-123"
        )
        self.course = Course.objects.create(
            user=self.user,
            title="Software Architecture",
            description="Testing generation topic features.",
        )

    def _sample_journey_dict(self):
        """Constructs a minimal valid journey dictionary for testing."""
        return {
            "schema_version": "1.0",
            "course": {
                "title": "Software Architecture",
                "description": "Valid curriculum",
                "source_type": "reviewer",
                "difficulty": "intermediate",
                "source_bundle_count": 1,
                "source_filenames": ["arch.txt"],
            },
            "chapters": [
                {
                    "order": 1,
                    "title": "Chapter 1: Patterns",
                    "overview": "Overview of architectural patterns",
                    "topics": [
                        {
                            "key": "ch1-topic",
                            "name": "Chapter 1 Topic",
                            "description": "Foundational pattern concepts.",
                        }
                    ],
                    "quiz": {
                        "title": "Quiz 1",
                        "questions": [
                            {
                                "order": 1,
                                "type": "multiple_choice",
                                "difficulty": "medium",
                                "text": "What is a pattern?",
                                "explanation": "A general reusable solution.",
                                "topic_keys": ["ch1-topic"],
                                "choices": [
                                    {"text": "A reusable solution", "is_correct": True},
                                    {"text": "An anti-pattern", "is_correct": False},
                                ],
                            }
                        ],
                    },
                },
                {
                    "order": 2,
                    "title": "Chapter 2: Governance",
                    "overview": "Overview of architectural governance",
                    "topics": [
                        {
                            "key": "ch2-topic",
                            "name": "Chapter 2 Topic",
                            "description": "Architecture governance frameworks.",
                        }
                    ],
                    "quiz": {
                        "title": "Quiz 2",
                        "questions": [
                            {
                                "order": 1,
                                "type": "multiple_choice",
                                "difficulty": "medium",
                                "text": "What is governance?",
                                "explanation": "Ensuring architectural compliance.",
                                "topic_keys": ["ch2-topic"],
                                "choices": [
                                    {"text": "Compliance and direction", "is_correct": True},
                                    {"text": "Ignore standards", "is_correct": False},
                                ],
                            }
                        ],
                    },
                },
            ],
        }

    # -------------------------------------------------------------------------
    # Test 1: GeneratedTopic Schema Validation & Normalization
    # -------------------------------------------------------------------------
    def test_generated_topic_schema_validation(self):
        """Topic key normalizes to slug, falls back to name slug, or raises if blank."""
        # 1. Standard key normalization
        t1 = GeneratedTopic(
            key="  Market Validation ",
            name="Market Validation",
            description="Testing customer demand",
        )
        self.assertEqual(t1.key, "market-validation")
        self.assertEqual(t1.name, "Market Validation")

        # 2. Key empty, falls back to slugified name
        t2 = GeneratedTopic(
            key="",
            name="Customer Discovery",
            description="Validating hypotheses",
        )
        self.assertEqual(t2.key, "customer-discovery")

        # 3. Key whitespace, falls back to slugified name
        t3 = GeneratedTopic(
            key="   ",
            name="Value Proposition",
        )
        self.assertEqual(t3.key, "value-proposition")

        # 4. Both key and name cannot produce a slug -> raises ValueError
        with self.assertRaises(ValueError):
            GeneratedTopic(key="???", name="!!!")

    # -------------------------------------------------------------------------
    # Test 2: Chapter-Local Topic Scoping
    # -------------------------------------------------------------------------
    def test_question_cannot_reference_topic_from_another_chapter(self):
        """A question referencing a topic declared only in another chapter has that key pruned."""
        data = self._sample_journey_dict()
        # Ch 1 question attempts to reference Ch 2 topic
        data["chapters"][0]["quiz"]["questions"][0]["topic_keys"] = [
            "ch1-topic",
            "ch2-topic",
        ]
        # Ch 2 question attempts to reference Ch 1 topic
        data["chapters"][1]["quiz"]["questions"][0]["topic_keys"] = [
            "ch1-topic",
            "ch2-topic",
        ]

        with self.assertLogs("courses.schemas", level="WARNING") as log_cm:
            validated = GeneratedJourney.model_validate(data)

        ch1_q = validated.chapters[0].quiz.questions[0]
        ch2_q = validated.chapters[1].quiz.questions[0]

        # Chapter 1 retains only ch1-topic
        self.assertEqual(ch1_q.topic_keys, ["ch1-topic"])
        # Chapter 2 retains only ch2-topic
        self.assertEqual(ch2_q.topic_keys, ["ch2-topic"])

        # Warnings were logged for both cross-chapter references
        log_output = "\n".join(log_cm.output)
        self.assertIn("referenced undeclared topic key: 'ch2-topic'", log_output)
        self.assertIn("referenced undeclared topic key: 'ch1-topic'", log_output)

    # -------------------------------------------------------------------------
    # Test 3: Chapter Deduplicates Normalized Topic Keys Safely
    # -------------------------------------------------------------------------
    def test_duplicate_normalized_topic_keys_are_handled_safely(self):
        """If a chapter defines two topics with the same normalized key, only the first is kept."""
        chapter_dict = {
            "order": 1,
            "title": "Chapter 1",
            "overview": "Overview",
            "topics": [
                {
                    "key": "Market Validation",
                    "name": "Market Validation Initial",
                    "description": "First definition",
                },
                {
                    "key": "market-validation",
                    "name": "Market Validation Duplicate",
                    "description": "Second definition",
                },
            ],
            "quiz": {
                "title": "Quiz",
                "questions": [
                    {
                        "order": 1,
                        "type": "multiple_choice",
                        "text": "Question 1?",
                        "explanation": "Valid educational explanation for question 1.",
                        "topic_keys": ["market-validation"],
                        "choices": [
                            {"text": "A", "is_correct": True},
                            {"text": "B", "is_correct": False},
                        ],
                    }
                ],
            },
        }

        with self.assertLogs("courses.schemas", level="WARNING") as log_cm:
            chapter = GeneratedChapter.model_validate(chapter_dict)

        self.assertEqual(len(chapter.topics), 1)
        self.assertEqual(chapter.topics[0].key, "market-validation")
        self.assertEqual(chapter.topics[0].name, "Market Validation Initial")
        self.assertIn("duplicate topic key 'market-validation'", log_cm.output[0])

    # -------------------------------------------------------------------------
    # Test 4: Same Topic Key Across Chapters Reuses One CourseTopic
    # -------------------------------------------------------------------------
    def test_same_topic_key_across_chapters_reuses_one_course_topic(self):
        """Identical topic key in multiple chapters results in exactly one CourseTopic record."""
        data = self._sample_journey_dict()
        # Both chapters declare the same shared topic
        data["chapters"][0]["topics"] = [
            {"key": "shared-concept", "name": "Shared Concept", "description": "Shared across chapters."}
        ]
        data["chapters"][0]["quiz"]["questions"][0]["topic_keys"] = ["shared-concept"]

        data["chapters"][1]["topics"] = [
            {"key": "shared-concept", "name": "Shared Concept", "description": "Shared across chapters."}
        ]
        data["chapters"][1]["quiz"]["questions"][0]["topic_keys"] = ["shared-concept"]

        _build_journey(self.course, journey_override=data)

        # Only 1 CourseTopic created for this course
        self.assertEqual(
            CourseTopic.objects.filter(course=self.course, key="shared-concept").count(),
            1,
        )
        topic = CourseTopic.objects.get(course=self.course, key="shared-concept")

        # Both questions link to that single CourseTopic
        q_topics = QuestionTopic.objects.filter(topic=topic)
        self.assertEqual(q_topics.count(), 2)

        questions = [qt.question for qt in q_topics]
        self.assertEqual(len(set(questions)), 2)

    # -------------------------------------------------------------------------
    # Test 5: Conflicting Names for Same Key Do Not Overwrite Existing Topic
    # -------------------------------------------------------------------------
    def test_conflicting_names_for_same_key_do_not_overwrite_existing_topic(self):
        """Subsequent declaration with differing name logs warning and keeps original name."""
        t1 = create_course_topic(
            course=self.course,
            key="cloud-patterns",
            name="Cloud Patterns",
            description="Original description",
            order=1,
        )

        with self.assertLogs("courses.topic_services", level="WARNING") as log_cm:
            t2 = create_course_topic(
                course=self.course,
                key="cloud-patterns",
                name="Cloud Architecture Patterns Differing",
                description="New description",
                order=2,
            )

        self.assertEqual(t1.id, t2.id)
        t1.refresh_from_db()
        self.assertEqual(t1.name, "Cloud Patterns")
        self.assertEqual(t1.description, "Original description")
        self.assertIn("already declared with name 'Cloud Patterns'", log_cm.output[0])

    # -------------------------------------------------------------------------
    # Test 6: Legacy Journey Without Topic Fields Still Validates & Persists
    # -------------------------------------------------------------------------
    def test_legacy_generated_journey_without_topic_fields_still_validates(self):
        """Journeys lacking topics or topic_keys parse gracefully and leave questions untagged."""
        legacy_data = {
            "schema_version": "1.0",
            "course": {
                "title": "Legacy Course",
                "description": "Legacy curriculum",
                "source_type": "reviewer",
                "difficulty": "intermediate",
                "source_bundle_count": 1,
                "source_filenames": ["legacy.txt"],
            },
            "chapters": [
                {
                    "order": 1,
                    "title": "Legacy Chapter",
                    "overview": "Legacy overview",
                    # No 'topics' key
                    "quiz": {
                        "title": "Legacy Quiz",
                        "questions": [
                            {
                                "order": 1,
                                "type": "multiple_choice",
                                "difficulty": "medium",
                                "text": "Legacy question?",
                                "explanation": "Legacy explanation.",
                                # No 'topic_keys' key
                                "choices": [
                                    {"text": "Choice A", "is_correct": True},
                                    {"text": "Choice B", "is_correct": False},
                                ],
                            }
                        ],
                    },
                }
            ],
        }

        validated = GeneratedJourney.model_validate(legacy_data)
        self.assertEqual(validated.chapters[0].topics, [])
        self.assertEqual(validated.chapters[0].quiz.questions[0].topic_keys, [])

        # Build journey into DB
        built_course = _build_journey(self.course, journey_override=legacy_data)
        self.assertEqual(built_course.topics.count(), 0)
        self.assertEqual(
            QuestionTopic.objects.filter(question__quiz__chapter__course=self.course).count(),
            0,
        )
        self.assertEqual(
            Question.objects.filter(quiz__chapter__course=self.course).count(),
            1,
        )

    # -------------------------------------------------------------------------
    # Test 7: Topic Metadata Failure Does Not Invalidate Valid Question
    # -------------------------------------------------------------------------
    def test_topic_metadata_failure_does_not_invalidate_valid_question(self):
        """If question topic linking triggers a ValidationError, the question still persists."""
        data = self._sample_journey_dict()

        with patch("courses.services.link_question_to_topic", side_effect=ValidationError("Link invalid")):
            with self.assertLogs("courses.services", level="WARNING") as log_cm:
                _build_journey(self.course, journey_override=data)

        # Question was still created and saved
        questions = Question.objects.filter(quiz__chapter__course=self.course)
        self.assertEqual(questions.count(), 2)

        # Topic was created
        self.assertTrue(CourseTopic.objects.filter(course=self.course).exists())

        # But question links were not created due to error
        self.assertEqual(
            QuestionTopic.objects.filter(question__quiz__chapter__course=self.course).count(),
            0,
        )
        self.assertIn("Validation failed linking question", log_cm.output[0])

    # -------------------------------------------------------------------------
    # Test 8: Unexpected DB Persistence Errors Are Not Swallowed
    # -------------------------------------------------------------------------
    def test_unexpected_persistence_error_is_not_silently_swallowed(self):
        """Unexpected database errors during topic creation roll back the entire transaction."""
        data = self._sample_journey_dict()

        with patch("courses.services.create_course_topic", side_effect=OperationalError("Disk full or lock timeout")):
            with self.assertRaises(OperationalError):
                _build_journey(self.course, journey_override=data)

        # Because _build_journey is atomic, nothing must be persisted
        self.assertEqual(Chapter.objects.filter(course=self.course).count(), 0)
        self.assertEqual(Quiz.objects.filter(chapter__course=self.course).count(), 0)
        self.assertEqual(Question.objects.filter(quiz__chapter__course=self.course).count(), 0)
        self.assertEqual(CourseTopic.objects.filter(course=self.course).count(), 0)

    # -------------------------------------------------------------------------
    # Test 9: Topic Tags Do Not Affect Quiz Grading
    # -------------------------------------------------------------------------
    def test_topic_tags_do_not_affect_quiz_grading(self):
        """Grading a tagged question vs an untagged question yields identical results."""
        chapter = Chapter.objects.create(course=self.course, title="Grading Ch", order=1)
        quiz = Quiz.objects.create(chapter=chapter, title="Grading Quiz")

        # Tagged question
        q_tagged = Question.objects.create(
            quiz=quiz,
            order=1,
            question_type="multiple_choice",
            text="Grading question 1?",
            explanation="Grading explanation 1.",
        )
        c1 = Choice.objects.create(question=q_tagged, text="Correct Answer", is_correct=True)
        Choice.objects.create(question=q_tagged, text="Wrong Answer", is_correct=False)

        topic = create_course_topic(course=self.course, key="grading-topic", name="Grading Topic")
        link_question_to_topic(question=q_tagged, topic=topic)

        # Untagged question (identical structure)
        q_untagged = Question.objects.create(
            quiz=quiz,
            order=2,
            question_type="multiple_choice",
            text="Grading question 2?",
            explanation="Grading explanation 2.",
        )
        c2 = Choice.objects.create(question=q_untagged, text="Correct Answer", is_correct=True)
        Choice.objects.create(question=q_untagged, text="Wrong Answer", is_correct=False)

        # Grade correct submission for both
        earned_t, max_t, info_t = _grade_question(q_tagged, {"choice_id": c1.id})
        earned_u, max_u, info_u = _grade_question(q_untagged, {"choice_id": c2.id})

        self.assertEqual(earned_t, earned_u)
        self.assertEqual(earned_t, 1)
        self.assertEqual(max_t, max_u)
        self.assertEqual(info_t["is_correct"], info_u["is_correct"])
        self.assertEqual(info_t["is_correct"], True)

        # Grade incorrect submission for both
        earned_t_wrong, max_t_wrong, info_t_wrong = _grade_question(q_tagged, {"choice_id": 999999})
        earned_u_wrong, max_u_wrong, info_u_wrong = _grade_question(q_untagged, {"choice_id": 999999})

        self.assertEqual(earned_t_wrong, earned_u_wrong)
        self.assertEqual(earned_t_wrong, 0)
        self.assertEqual(max_t_wrong, max_u_wrong)
        self.assertEqual(info_t_wrong["is_correct"], info_u_wrong["is_correct"])
        self.assertEqual(info_t_wrong["is_correct"], False)

    # -------------------------------------------------------------------------
    # Test 10: Mock Journey Validates and Has Topics
    # -------------------------------------------------------------------------
    def test_mock_journey_validates_and_has_topics(self):
        """_generate_mock_journey outputs valid topic metadata and builds correctly."""
        raw_journey = _generate_mock_journey(self.course)

        validated = GeneratedJourney.model_validate(raw_journey)
        ch0 = validated.chapters[0]

        # 3 topics declared in chapter 0
        self.assertEqual(len(ch0.topics), 3)
        topic_keys = {t.key for t in ch0.topics}
        expected_keys = {"bdat-architecture", "togaf-framework", "zachman-framework"}
        self.assertEqual(topic_keys, expected_keys)

        # All 10 questions have at least one topic_key from expected_keys
        questions = ch0.quiz.questions
        self.assertEqual(len(questions), 10)
        for q in questions:
            self.assertTrue(len(q.topic_keys) >= 1)
            for k in q.topic_keys:
                self.assertIn(k, expected_keys)

        # Builds successfully into database with topics & question links
        _build_journey(self.course, journey_override=raw_journey)
        self.assertEqual(self.course.topics.count(), 3)
        self.assertEqual(
            QuestionTopic.objects.filter(question__quiz__chapter__course=self.course).count(),
            10,
        )
