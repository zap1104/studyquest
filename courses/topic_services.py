"""Service functions for course topics and question-topic associations (TE-1A).

Enforces same-Course boundaries and provides safe methods for linking, unlinking,
and querying course-scoped topics and questions.
"""

from typing import Optional
from django.core.exceptions import ValidationError
from django.db.models import QuerySet
from courses.models import Course, CourseTopic, Question, QuestionTopic, Quiz, Chapter


def link_question_to_topic(*, question: Question, topic: CourseTopic) -> QuestionTopic:
    """Safely links a Question to a CourseTopic, enforcing that both belong to the same Course.

    Returns the created or existing QuestionTopic instance.
    Raises ValidationError if question and topic belong to different courses, or if
    either is not attached to a valid course.
    """
    if not question:
        raise ValueError("question is required.")
    if not topic:
        raise ValueError("topic is required.")

    try:
        question_course_id = question.quiz.chapter.course_id
    except (AttributeError, Quiz.DoesNotExist, Chapter.DoesNotExist):
        question_course_id = None

    if question_course_id is None:
        raise ValidationError(
            f"Question {question.pk} is not linked to a valid Quiz/Chapter/Course hierarchy."
        )

    if topic.course_id is None:
        raise ValidationError(
            f"CourseTopic {topic.pk} is not linked to a valid Course."
        )

    if question_course_id != topic.course_id:
        raise ValidationError(
            f"Cannot link Question {question.pk} (Course {question_course_id}) "
            f"to CourseTopic {topic.pk} (Course {topic.course_id}). Both must belong to the same Course."
        )

    link, _ = QuestionTopic.objects.get_or_create(
        question=question,
        topic=topic,
    )
    return link


def unlink_question_from_topic(*, question: Question, topic: CourseTopic) -> bool:
    """Removes the link between a Question and a CourseTopic if it exists.

    Returns True if a link was deleted, False otherwise.
    """
    if not question or not topic:
        return False
    deleted_count, _ = QuestionTopic.objects.filter(question=question, topic=topic).delete()
    return deleted_count > 0


def create_course_topic(
    *,
    course: Course,
    key: str,
    name: str,
    description: str = "",
    order: int = 1,
) -> CourseTopic:
    """Creates a new CourseTopic strictly scoped to the given Course."""
    if not course:
        raise ValueError("course is required.")

    clean_key = (key or "").strip().lower()
    clean_name = (name or "").strip()

    if not clean_key:
        raise ValueError("Topic key cannot be blank.")
    if not clean_name:
        raise ValueError("Topic name cannot be blank.")

    existing = CourseTopic.objects.filter(course=course, key=clean_key).first()
    if existing:
        if existing.name.lower() != clean_name.lower():
            import logging
            logging.getLogger(__name__).warning(
                f"CourseTopic key '{clean_key}' already declared with name '{existing.name}'. "
                f"Ignoring conflicting name '{clean_name}' from subsequent declaration."
            )
        return existing

    return CourseTopic.objects.create(
        course=course,
        key=clean_key,
        name=clean_name,
        description=(description or "").strip(),
        order=order,
    )


def get_course_topics(course: Course) -> QuerySet[CourseTopic]:
    """Returns all topics for a course ordered by order and id."""
    if not course:
        return CourseTopic.objects.none()
    return CourseTopic.objects.filter(course=course).order_by("order", "id")


def get_question_topics(question: Question) -> QuerySet[CourseTopic]:
    """Returns all CourseTopic records linked to a given question."""
    if not question:
        return CourseTopic.objects.none()
    return CourseTopic.objects.filter(question_links__question=question).order_by("order", "id")

