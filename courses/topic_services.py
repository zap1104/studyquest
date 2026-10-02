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


# -----------------------------------------------------------------------------
# TOPIC EVIDENCE AGGREGATION ENGINE (TE-2)
# -----------------------------------------------------------------------------

def determine_evidence_state(
    *,
    unique_questions_assessed: int,
    accuracy: float,
    unresolved_count: int,
    correct_count: int,
) -> tuple[str, str]:
    """Calculates the evidence state and learner-facing explanation according to TE-2 rules."""
    if unique_questions_assessed == 0:
        return "Not Assessed", "No questions attempted yet for this topic."

    if unique_questions_assessed < 3:
        # 1-2 unique questions
        return (
            "Early Evidence",
            f"{correct_count} of {unique_questions_assessed} questions answered correctly. "
            "More practice needed to establish a progress level.",
        )

    acc_pct = int(round(accuracy))

    # At least 3 questions
    if accuracy < 60.0 or unresolved_count >= 2:
        unit = "question" if unresolved_count == 1 else "questions"
        return (
            "Needs Attention",
            f"{correct_count} of {unique_questions_assessed} questions correct ({acc_pct}%), "
            f"with {unresolved_count} {unit} needing review.",
        )

    if accuracy < 80.0:
        # accuracy in [60.0, 80.0) and unresolved_count < 2 (i.e. 0 or 1)
        return (
            "Developing",
            f"{correct_count} of {unique_questions_assessed} questions correct ({acc_pct}%). "
            "Showing steady progress.",
        )

    # accuracy >= 80.0
    if unique_questions_assessed >= 5 and accuracy >= 85.0 and unresolved_count == 0:
        return (
            "Consistently Demonstrated",
            f"{correct_count} of {unique_questions_assessed} questions consistently answered correctly ({acc_pct}%).",
        )

    # At least 3 questions, accuracy >= 80%, but unresolved mistakes remain OR < 5 questions
    if unresolved_count > 0:
        unit = "question" if unresolved_count == 1 else "questions"
        return (
            "Showing Progress",
            f"{correct_count} of {unique_questions_assessed} questions correct ({acc_pct}%). "
            f"{unresolved_count} {unit} still to review.",
        )

    return (
        "Showing Progress",
        f"{correct_count} of {unique_questions_assessed} questions correct ({acc_pct}%). "
        "Keep practicing to demonstrate consistent mastery.",
    )


def _build_topic_evidence_dict(
    *,
    topic: CourseTopic,
    unique_questions_assessed: int,
    total_answer_events: int,
    correct_count: int,
    partial_count: int,
    incorrect_count: int,
    unresolved_count: int,
    accuracy: float,
    latest_activity_at,
) -> dict:
    evidence_state, explanation = determine_evidence_state(
        unique_questions_assessed=unique_questions_assessed,
        accuracy=accuracy,
        unresolved_count=unresolved_count,
        correct_count=correct_count,
    )
    return {
        "topic_id": topic.id,
        "topic_key": topic.key,
        "topic_name": topic.name,
        "topic_description": topic.description,
        "unique_questions_assessed": unique_questions_assessed,
        "total_answer_events": total_answer_events,
        "correct_count": correct_count,
        "partial_count": partial_count,
        "incorrect_count": incorrect_count,
        "unresolved_count": unresolved_count,
        "accuracy": accuracy,
        "latest_activity_at": latest_activity_at,
        "evidence_state": evidence_state,
        "explanation": explanation,
    }


def get_course_topics_evidence_map(user, course: Course) -> dict[int, dict]:
    """Batch-computes topic evidence for all topics belonging to a Course.

    Returns a dict mapping topic_id -> topic_evidence_dict.
    Uses a single batch query pass across QuizAttempt and DungeonRun records.
    Classic Dungeon results are strictly excluded.
    """
    if not course:
        return {}

    topics = list(get_course_topics(course))
    if not topics:
        return {}

    # Initialize empty evidence structure for all topics
    evidence_by_topic_id = {}
    topic_question_ids_map = {t.id: set() for t in topics}
    question_to_topics_map = {}

    # Map question IDs to topics
    q_links = QuestionTopic.objects.filter(topic__in=topics).values_list("question_id", "topic_id")
    for qid, tid in q_links:
        topic_question_ids_map[tid].add(qid)
        question_to_topics_map.setdefault(qid, []).append(tid)

    if not user or not getattr(user, "is_authenticated", False) or not question_to_topics_map:
        for t in topics:
            evidence_by_topic_id[t.id] = _build_topic_evidence_dict(
                topic=t,
                unique_questions_assessed=0,
                total_answer_events=0,
                correct_count=0,
                partial_count=0,
                incorrect_count=0,
                unresolved_count=0,
                accuracy=0.0,
                latest_activity_at=None,
            )
        return evidence_by_topic_id

    topic_events_count = {t.id: 0 for t in topics}
    topic_question_outcomes = {t.id: {} for t in topics}

    quizzes = Quiz.objects.filter(chapter__course=course)

    # 1. Inspect standard QuizAttempt history
    from courses.models import QuizAttempt
    attempts = QuizAttempt.objects.filter(
        user=user,
        quiz__in=quizzes,
    ).order_by("completed_at", "pk")

    for attempt in attempts:
        items = (attempt.review_data or {}).get("review_items", [])
        if not items:
            continue
        ts = attempt.completed_at
        for item in items:
            qid = item.get("question_id")
            if qid not in question_to_topics_map:
                continue

            earned_raw = item.get("earned_points")
            max_raw = item.get("maximum_points")
            if earned_raw is not None and max_raw is not None:
                try:
                    earned = float(earned_raw)
                    maximum = float(max_raw)
                    ratio = min(max(earned / maximum, 0.0), 1.0) if maximum > 0 else (1.0 if item.get("is_correct") else 0.0)
                except (TypeError, ValueError, ZeroDivisionError):
                    ratio = 1.0 if item.get("is_correct") else 0.0
            else:
                ratio = 1.0 if item.get("is_correct") else 0.0

            if ratio >= 1.0:
                outcome = "correct"
            elif ratio > 0.0:
                outcome = "partial"
            else:
                outcome = "incorrect"

            for tid in question_to_topics_map[qid]:
                topic_events_count[tid] += 1
                curr = topic_question_outcomes[tid].get(qid)
                if curr is None or ts >= curr["timestamp"]:
                    topic_question_outcomes[tid][qid] = {
                        "timestamp": ts,
                        "ratio": ratio,
                        "outcome": outcome,
                    }

    # 2. Inspect completed Dungeon Review Runs (Classic Dungeon strictly excluded)
    try:
        from dungeon.models import DungeonRun
        review_runs = DungeonRun.objects.filter(
            user=user,
            quiz__in=quizzes,
            run_type=DungeonRun.TYPE_REVIEW,
            finished_at__isnull=False,
        ).order_by("finished_at", "pk")

        for rrun in review_runs:
            ts = rrun.finished_at
            mastered_set = set(rrun.review_mastered_question_ids or [])
            targeted = rrun.review_question_ids or []
            for qid in targeted:
                if qid not in question_to_topics_map:
                    continue
                is_mastered = qid in mastered_set
                ratio = 1.0 if is_mastered else 0.0
                outcome = "correct" if is_mastered else "incorrect"

                for tid in question_to_topics_map[qid]:
                    topic_events_count[tid] += 1
                    curr = topic_question_outcomes[tid].get(qid)
                    if curr is None or ts >= curr["timestamp"]:
                        topic_question_outcomes[tid][qid] = {
                            "timestamp": ts,
                            "ratio": ratio,
                            "outcome": outcome,
                        }
    except ImportError:
        pass

    # 3. Assemble final topic evidence dicts
    for t in topics:
        tid = t.id
        q_outcomes = topic_question_outcomes[tid]
        unique_assessed = len(q_outcomes)
        total_events = topic_events_count[tid]

        if unique_assessed == 0:
            evidence_by_topic_id[tid] = _build_topic_evidence_dict(
                topic=t,
                unique_questions_assessed=0,
                total_answer_events=total_events,
                correct_count=0,
                partial_count=0,
                incorrect_count=0,
                unresolved_count=0,
                accuracy=0.0,
                latest_activity_at=None,
            )
        else:
            correct_cnt = sum(1 for o in q_outcomes.values() if o["outcome"] == "correct")
            partial_cnt = sum(1 for o in q_outcomes.values() if o["outcome"] == "partial")
            incorrect_cnt = sum(1 for o in q_outcomes.values() if o["outcome"] == "incorrect")
            unresolved_cnt = partial_cnt + incorrect_cnt
            total_ratio = sum(o["ratio"] for o in q_outcomes.values())
            accuracy = round((total_ratio / unique_assessed) * 100, 1)
            latest_ts = max(o["timestamp"] for o in q_outcomes.values())

            evidence_by_topic_id[tid] = _build_topic_evidence_dict(
                topic=t,
                unique_questions_assessed=unique_assessed,
                total_answer_events=total_events,
                correct_count=correct_cnt,
                partial_count=partial_cnt,
                incorrect_count=incorrect_cnt,
                unresolved_count=unresolved_cnt,
                accuracy=accuracy,
                latest_activity_at=latest_ts,
            )

    return evidence_by_topic_id


def get_topic_evidence(user, topic: CourseTopic) -> dict:
    """Calculates evidence metrics and state for a single CourseTopic."""
    if not topic:
        raise ValueError("topic is required.")
    evidence_map = get_course_topics_evidence_map(user, topic.course)
    if topic.id in evidence_map:
        return evidence_map[topic.id]
    return _build_topic_evidence_dict(
        topic=topic,
        unique_questions_assessed=0,
        total_answer_events=0,
        correct_count=0,
        partial_count=0,
        incorrect_count=0,
        unresolved_count=0,
        accuracy=0.0,
        latest_activity_at=None,
    )


def get_course_topic_progress(user, course: Course) -> dict:
    """Calculates evidence summaries for all topics in a Course, plus overall coverage."""
    if not course:
        return {
            "has_topics": False,
            "total_topics": 0,
            "assessed_topics_count": 0,
            "coverage_percentage": 0,
            "topics": [],
            "state_counts": {},
        }

    evidence_map = get_course_topics_evidence_map(user, course)
    topics_list = list(evidence_map.values())
    total_topics = len(topics_list)

    if total_topics == 0:
        return {
            "has_topics": False,
            "total_topics": 0,
            "assessed_topics_count": 0,
            "coverage_percentage": 0,
            "topics": [],
            "state_counts": {},
        }

    assessed_count = sum(1 for t in topics_list if t["unique_questions_assessed"] > 0)
    coverage_pct = round((assessed_count / total_topics) * 100) if total_topics > 0 else 0

    state_counts = {
        "Not Assessed": sum(1 for t in topics_list if t["evidence_state"] == "Not Assessed"),
        "Early Evidence": sum(1 for t in topics_list if t["evidence_state"] == "Early Evidence"),
        "Needs Attention": sum(1 for t in topics_list if t["evidence_state"] == "Needs Attention"),
        "Developing": sum(1 for t in topics_list if t["evidence_state"] == "Developing"),
        "Showing Progress": sum(1 for t in topics_list if t["evidence_state"] == "Showing Progress"),
        "Consistently Demonstrated": sum(1 for t in topics_list if t["evidence_state"] == "Consistently Demonstrated"),
    }

    return {
        "has_topics": True,
        "total_topics": total_topics,
        "assessed_topics_count": assessed_count,
        "coverage_percentage": coverage_pct,
        "topics": topics_list,
        "state_counts": state_counts,
    }

