from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from courses.models import Course, Chapter, Quiz, Question, Choice, CourseTopic, QuestionTopic
from courses.topic_services import (
    create_course_topic,
    get_course_topics,
    get_question_topics,
    link_question_to_topic,
    unlink_question_from_topic,
)


class TopicModelsTests(TestCase):
    """Test suite for TE-1A CourseTopic and QuestionTopic models and service layer."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="topic_test_user", password="test-password-123"
        )
        self.course_a = Course.objects.create(
            user=self.user,
            title="Course A: Technopreneurship",
            description="Testing Course A",
        )
        self.course_b = Course.objects.create(
            user=self.user,
            title="Course B: Software Architecture",
            description="Testing Course B",
        )

        # Setup chapter, quiz, question for Course A
        self.chapter_a = Chapter.objects.create(
            course=self.course_a, title="Chapter 1A", order=1
        )
        self.quiz_a = Quiz.objects.create(chapter=self.chapter_a, title="Quiz 1A")
        self.question_a = Question.objects.create(
            quiz=self.quiz_a,
            order=1,
            question_type="multiple_choice",
            text="Question A1?",
            explanation="Explanation A1.",
        )
        Choice.objects.create(question=self.question_a, text="Correct", is_correct=True)

        # Setup chapter, quiz, question for Course B
        self.chapter_b = Chapter.objects.create(
            course=self.course_b, title="Chapter 1B", order=1
        )
        self.quiz_b = Quiz.objects.create(chapter=self.chapter_b, title="Quiz 1B")
        self.question_b = Question.objects.create(
            quiz=self.quiz_b,
            order=1,
            question_type="multiple_choice",
            text="Question B1?",
            explanation="Explanation B1.",
        )
        Choice.objects.create(question=self.question_b, text="Correct", is_correct=True)

    # 1. Create CourseTopic success
    def test_create_course_topic_success(self):
        topic = create_course_topic(
            course=self.course_a,
            key="market-validation",
            name="Market Validation",
            description="Validating real market demand.",
            order=1,
        )
        self.assertEqual(topic.course, self.course_a)
        self.assertEqual(topic.key, "market-validation")
        self.assertEqual(topic.name, "Market Validation")
        self.assertEqual(topic.description, "Validating real market demand.")
        self.assertEqual(topic.order, 1)

    # 2. UniqueConstraint(course, key) prevents duplicate keys within same course
    def test_unique_course_topic_key_constraint(self):
        CourseTopic.objects.create(
            course=self.course_a,
            key="market-validation",
            name="Market Validation",
        )
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                CourseTopic.objects.create(
                    course=self.course_a,
                    key="market-validation",
                    name="Market Validation Duplicate",
                )

    # 3. Same topic key allowed across different courses
    def test_same_topic_key_allowed_across_different_courses(self):
        topic_a = CourseTopic.objects.create(
            course=self.course_a,
            key="shared-concept",
            name="Shared Concept A",
        )
        topic_b = CourseTopic.objects.create(
            course=self.course_b,
            key="shared-concept",
            name="Shared Concept B",
        )
        self.assertNotEqual(topic_a.pk, topic_b.pk)
        self.assertEqual(topic_a.key, topic_b.key)
        self.assertEqual(topic_a.course, self.course_a)
        self.assertEqual(topic_b.course, self.course_b)

    # 4. Service rejects empty course, key, or name
    def test_create_course_topic_validation(self):
        with self.assertRaises(ValueError):
            create_course_topic(course=None, key="key", name="Name")
        with self.assertRaises(ValueError):
            create_course_topic(course=self.course_a, key="", name="Name")
        with self.assertRaises(ValueError):
            create_course_topic(course=self.course_a, key="   ", name="Name")
        with self.assertRaises(ValueError):
            create_course_topic(course=self.course_a, key="valid-key", name="")

    # 5. QuestionTopic links question to topic successfully
    def test_create_question_topic_link_success(self):
        topic = create_course_topic(
            course=self.course_a,
            key="customer-interviews",
            name="Customer Interviews",
        )
        link = link_question_to_topic(question=self.question_a, topic=topic)
        self.assertEqual(link.question, self.question_a)
        self.assertEqual(link.topic, topic)
        self.assertTrue(QuestionTopic.objects.filter(question=self.question_a, topic=topic).exists())

    # 6. Duplicate question-topic link is idempotent via service and raises on direct insert
    def test_unique_question_topic_constraint(self):
        topic = create_course_topic(
            course=self.course_a,
            key="ideation",
            name="Ideation",
        )
        link1 = link_question_to_topic(question=self.question_a, topic=topic)
        link2 = link_question_to_topic(question=self.question_a, topic=topic)
        self.assertEqual(link1.pk, link2.pk)

        # Direct model save() triggers full_clean() -> raises ValidationError
        with self.assertRaises(ValidationError):
            QuestionTopic.objects.create(question=self.question_a, topic=topic)

        # bulk_create() bypasses full_clean() -> hits DB UniqueConstraint -> IntegrityError
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                QuestionTopic.objects.bulk_create([QuestionTopic(question=self.question_a, topic=topic)])

    # 7. Model clean() prevents cross-course link
    def test_cross_course_link_prevention_via_model_clean(self):
        topic_b = create_course_topic(
            course=self.course_b,
            key="arch-styles",
            name="Architecture Styles",
        )
        cross_link = QuestionTopic(question=self.question_a, topic=topic_b)
        with self.assertRaises(ValidationError) as cm:
            cross_link.save()
        self.assertIn("same Course", str(cm.exception))

    # 8. Service link_question_to_topic rejects cross-course link
    def test_cross_course_link_prevention_via_service(self):
        topic_b = create_course_topic(
            course=self.course_b,
            key="arch-styles",
            name="Architecture Styles",
        )
        with self.assertRaises(ValidationError) as cm:
            link_question_to_topic(question=self.question_a, topic=topic_b)
        self.assertIn("Both must belong to the same Course", str(cm.exception))

    # 9. Unlink question from topic
    def test_unlink_question_from_topic(self):
        topic = create_course_topic(
            course=self.course_a,
            key="lean-startup",
            name="Lean Startup",
        )
        link_question_to_topic(question=self.question_a, topic=topic)
        self.assertTrue(unlink_question_from_topic(question=self.question_a, topic=topic))
        self.assertFalse(QuestionTopic.objects.filter(question=self.question_a, topic=topic).exists())
        # Second unlink returns False
        self.assertFalse(unlink_question_from_topic(question=self.question_a, topic=topic))

    # 10. get_question_topics returns linked topics
    def test_get_question_topics(self):
        t1 = create_course_topic(course=self.course_a, key="t1", name="Topic 1", order=2)
        t2 = create_course_topic(course=self.course_a, key="t2", name="Topic 2", order=1)
        link_question_to_topic(question=self.question_a, topic=t1)
        link_question_to_topic(question=self.question_a, topic=t2)

        topics = list(get_question_topics(self.question_a))
        self.assertEqual(len(topics), 2)
        # Ordered by order, id -> t2 (order=1) then t1 (order=2)
        self.assertEqual(topics[0], t2)
        self.assertEqual(topics[1], t1)

    # 11. get_course_topics returns all topics for course
    def test_get_course_topics(self):
        t1 = create_course_topic(course=self.course_a, key="t1", name="Topic 1", order=2)
        t2 = create_course_topic(course=self.course_a, key="t2", name="Topic 2", order=1)
        create_course_topic(course=self.course_b, key="t3", name="Topic 3", order=1)

        course_a_topics = list(get_course_topics(self.course_a))
        self.assertEqual(len(course_a_topics), 2)
        self.assertEqual(course_a_topics[0], t2)
        self.assertEqual(course_a_topics[1], t1)

    # 12. Cascade delete on Course deletes CourseTopics and QuestionTopics
    def test_cascade_delete_course(self):
        topic = create_course_topic(course=self.course_a, key="cascade-test", name="Cascade Test")
        link_question_to_topic(question=self.question_a, topic=topic)

        self.course_a.delete()
        self.assertEqual(CourseTopic.objects.filter(key="cascade-test").count(), 0)
        self.assertEqual(QuestionTopic.objects.count(), 0)

    # 13. Cascade delete on Question deletes QuestionTopics but preserves CourseTopic
    def test_cascade_delete_question(self):
        topic = create_course_topic(course=self.course_a, key="preserve-test", name="Preserve Test")
        link_question_to_topic(question=self.question_a, topic=topic)

        self.question_a.delete()
        self.assertEqual(QuestionTopic.objects.count(), 0)
        self.assertTrue(CourseTopic.objects.filter(key="preserve-test").exists())
