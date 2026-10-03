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
    source_counts: dict = None,
) -> dict:
    evidence_state, explanation = determine_evidence_state(
        unique_questions_assessed=unique_questions_assessed,
        accuracy=accuracy,
        unresolved_count=unresolved_count,
        correct_count=correct_count,
    )
    sc = source_counts or {"baseline": 0, "quiz": 0, "review_run": 0}
    evidence_sources = [k for k, v in sc.items() if v > 0]
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
        "source_counts": sc,
        "evidence_sources": evidence_sources,
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
    topic_source_counts = {t.id: {"baseline": 0, "quiz": 0, "review_run": 0} for t in topics}

    # 1. Inspect completed StartingKnowledgeCheck (baseline evidence)
    try:
        from courses.models import StartingKnowledgeCheck
        checks = StartingKnowledgeCheck.objects.filter(
            user=user,
            course=course,
            status=StartingKnowledgeCheck.STATUS_COMPLETED,
        ).order_by("completed_at", "pk")
        for check in checks:
            ts = check.completed_at
            items = check.graded_items or []
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
                    topic_source_counts[tid]["baseline"] += 1
                    curr = topic_question_outcomes[tid].get(qid)
                    if curr is None or (curr.get("source") == "starting_knowledge_check" and (ts and curr["timestamp"] and ts >= curr["timestamp"])):
                        topic_question_outcomes[tid][qid] = {
                            "timestamp": ts,
                            "ratio": ratio,
                            "outcome": outcome,
                            "source": "starting_knowledge_check",
                        }
    except Exception:
        pass

    quizzes = Quiz.objects.filter(chapter__course=course)

    # 2. Inspect standard QuizAttempt history (ordinary learning evidence)
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
                topic_source_counts[tid]["quiz"] += 1
                curr = topic_question_outcomes[tid].get(qid)
                # Supersedes baseline or earlier quiz attempts
                if curr is None or curr.get("source") == "starting_knowledge_check" or (ts and curr["timestamp"] and ts >= curr["timestamp"]):
                    topic_question_outcomes[tid][qid] = {
                        "timestamp": ts,
                        "ratio": ratio,
                        "outcome": outcome,
                        "source": "chapter_quiz",
                    }

    # 3. Inspect completed Dungeon Review Runs (resolution evidence; Classic Dungeon strictly excluded)
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
                    topic_source_counts[tid]["review_run"] += 1
                    curr = topic_question_outcomes[tid].get(qid)
                    # Supersedes baseline or earlier quiz/review run attempts
                    if curr is None or curr.get("source") == "starting_knowledge_check" or (ts and curr["timestamp"] and ts >= curr["timestamp"]):
                        topic_question_outcomes[tid][qid] = {
                            "timestamp": ts,
                            "ratio": ratio,
                            "outcome": outcome,
                            "source": "review_run",
                        }
    except ImportError:
        pass

    # 4. Assemble final topic evidence dicts
    for t in topics:
        tid = t.id
        q_outcomes = topic_question_outcomes[tid]
        unique_assessed = len(q_outcomes)
        total_events = topic_events_count[tid]
        sc = topic_source_counts[tid]

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
                source_counts=sc,
            )
        else:
            correct_cnt = sum(1 for o in q_outcomes.values() if o["outcome"] == "correct")
            partial_cnt = sum(1 for o in q_outcomes.values() if o["outcome"] == "partial")
            incorrect_cnt = sum(1 for o in q_outcomes.values() if o["outcome"] == "incorrect")
            unresolved_cnt = partial_cnt + incorrect_cnt
            total_ratio = sum(o["ratio"] for o in q_outcomes.values())
            accuracy = round((total_ratio / unique_assessed) * 100, 1)
            latest_ts = max((o["timestamp"] for o in q_outcomes.values() if o["timestamp"]), default=None)

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
                source_counts=sc,
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


# -----------------------------------------------------------------------------
# TOPIC-AWARE RECOMMENDATIONS ENGINE (TE-3A)
# -----------------------------------------------------------------------------

def _find_chapter_for_topic(course: Course, topic_id: int) -> Optional[Chapter]:
    """Finds the best matching Chapter for a CourseTopic.

    1. Looks for a Chapter whose Quiz has Questions linked to this topic.
    2. If multiple chapters link to this topic, returns the earliest chapter by order.
    3. Fallback: returns the earliest Chapter in the course.
    """
    ch = (
        Chapter.objects.filter(
            course=course,
            quiz__questions__topic_links__topic_id=topic_id,
        )
        .distinct()
        .order_by("order", "id")
        .first()
    )
    if ch:
        return ch
    return course.chapters.order_by("order", "id").first()


def _format_topic_recommendation_reason(topic: dict, priority_level: int) -> str:
    """Formats clear, observable evidence counts without overly technical terms."""
    unresolved = topic.get("unresolved_count", 0)
    incorrect = topic.get("incorrect_count", 0)
    partial = topic.get("partial_count", 0)
    correct = topic.get("correct_count", 0)
    total = topic.get("unique_questions_assessed", 0)
    acc = int(round(topic.get("accuracy", 0.0)))
    q_unit = "question" if unresolved == 1 else "questions"
    need_verb = "needs" if unresolved == 1 else "need"

    if priority_level == 1:  # Needs Attention
        if unresolved > 0 and incorrect > 0 and partial > 0:
            m_unit = "mistake" if incorrect == 1 else "mistakes"
            rem_verb = "remains" if incorrect == 1 else "remain"
            return f"{unresolved} questions need attention. {incorrect} {m_unit} {rem_verb} unresolved."
        elif unresolved > 0 and incorrect > 0:
            m_unit = "mistake" if unresolved == 1 else "mistakes"
            rem_verb = "remains" if unresolved == 1 else "remain"
            return f"{unresolved} {q_unit} {need_verb} attention. {unresolved} {m_unit} {rem_verb} unresolved."
        elif unresolved > 0 and partial > 0:
            return f"{unresolved} {q_unit} {need_verb} attention to reach full credit."
        else:
            return f"{correct} of {total} questions answered correctly ({acc}%). Review recommended."

    elif priority_level == 2:  # Developing
        if unresolved > 0:
            m_unit = "mistake" if unresolved == 1 else "mistakes"
            rem_verb = "remains" if unresolved == 1 else "remain"
            return f"{unresolved} {q_unit} {need_verb} attention. {unresolved} {m_unit} {rem_verb} unresolved ({acc}% accuracy)."
        else:
            return f"{correct} of {total} questions answered correctly ({acc}%). Practice recommended to strengthen this topic."

    elif priority_level == 3:  # Showing Progress with unresolved mistakes
        m_unit = "mistake" if unresolved == 1 else "mistakes"
        rem_verb = "remains" if unresolved == 1 else "remain"
        return f"{correct} of {total} questions answered correctly ({acc}%), but {unresolved} {m_unit} {rem_verb} to review."

    elif priority_level == 4:  # Assessment Needed (Not Assessed or Early Evidence)
        if total == 0:
            return "No questions assessed yet. Take a quiz or review to establish your starting level."
        else:
            return f"{correct} of {total} questions answered correctly. More practice needed to establish a progress level."

    return "All course topics are consistently demonstrated! Test your long-term recall in Dungeon Quest."


def get_topic_recommendation(user, course: Course, focus=None) -> Optional[dict]:
    """Generates an evidence-backed topic recommendation for a Course (TE-3A).

    Prioritizes topics strictly in order:
    1. Needs Attention
    2. Developing
    3. Showing Progress with unresolved mistakes
    4. Not Assessed / Early Evidence (more evidence needed)
    5. Consistently Demonstrated topics are NEVER recommended for review.
       When all topics are mastered, recommends Dungeon Quest for long-term retention.

    Routes learners only to existing activities:
    - Chapter Review
    - Chapter Quiz
    - Review Run
    - Dungeon Quest

    Returns None if the course has no topics.
    """
    if not course:
        return None

    evidence_map = get_course_topics_evidence_map(user, course)
    if not evidence_map:
        return None

    topics = list(evidence_map.values())
    if not topics:
        return None

    # Identify user's active focus target topics for tie-breaking
    focus_topic_keys = set()
    focus_topic_names = set()
    if focus and getattr(focus, "topic_names", None):
        from django.utils.text import slugify
        for tn in focus.topic_names:
            clean = str(tn).strip().lower()
            if clean:
                focus_topic_names.add(clean)
                focus_topic_keys.add(slugify(clean))

    def _is_focus(t: dict) -> bool:
        return (t["topic_key"] in focus_topic_keys) or (t["topic_name"].lower() in focus_topic_names)

    # 1. Bucket topics by priority
    needs_attention = [t for t in topics if t["evidence_state"] == "Needs Attention"]
    developing = [t for t in topics if t["evidence_state"] == "Developing"]
    showing_progress_unresolved = [
        t for t in topics if t["evidence_state"] == "Showing Progress" and t.get("unresolved_count", 0) > 0
    ]
    assessment_needed = [t for t in topics if t["evidence_state"] in ("Not Assessed", "Early Evidence")]

    selected_topic = None
    priority_level = None

    if needs_attention:
        priority_level = 1
        # Sort: focus target first, highest unresolved count, lowest accuracy, lowest topic_id
        needs_attention.sort(
            key=lambda t: (
                -1 if _is_focus(t) else 0,
                -t.get("unresolved_count", 0),
                t.get("accuracy", 0.0),
                t.get("topic_id", 0),
            )
        )
        selected_topic = needs_attention[0]

    elif developing:
        priority_level = 2
        # Sort: focus target first, highest unresolved count, lowest accuracy, lowest topic_id
        developing.sort(
            key=lambda t: (
                -1 if _is_focus(t) else 0,
                -t.get("unresolved_count", 0),
                t.get("accuracy", 0.0),
                t.get("topic_id", 0),
            )
        )
        selected_topic = developing[0]

    elif showing_progress_unresolved:
        priority_level = 3
        # Sort: focus target first, highest unresolved count, lowest accuracy, lowest topic_id
        showing_progress_unresolved.sort(
            key=lambda t: (
                -1 if _is_focus(t) else 0,
                -t.get("unresolved_count", 0),
                t.get("accuracy", 0.0),
                t.get("topic_id", 0),
            )
        )
        selected_topic = showing_progress_unresolved[0]

    elif assessment_needed:
        priority_level = 4
        # Sort: focus target first, Early Evidence first (needs fewer questions to resolve), lowest topic_id
        assessment_needed.sort(
            key=lambda t: (
                -1 if _is_focus(t) else 0,
                0 if t["evidence_state"] == "Early Evidence" else 1,
                t.get("topic_id", 0),
            )
        )
        selected_topic = assessment_needed[0]

    else:
        # Priority 5: All topics are Consistently Demonstrated or cleanly Showing Progress!
        # Consistently Demonstrated topics are NOT recommended for review.
        return {
            "has_recommendation": True,
            "is_topic_aware": True,
            "topic_id": None,
            "topic_key": "",
            "topic_name": "",
            "evidence_state": "Consistently Demonstrated",
            "priority_level": 5,
            "badge_label": "MASTERY DEMONSTRATED",
            "title": "Dungeon Expedition",
            "reason": "All course topics are consistently demonstrated! Test your long-term recall in Dungeon Quest.",
            "button_label": "Enter Dungeon Quest →",
            "url": "/dungeon/",
            "secondary_button_label": None,
            "secondary_url": None,
            "action_type": "dungeon_quest",
            "chapter_id": None,
            "chapter_title": "",
            "unresolved_count": 0,
            "accuracy": 100.0,
        }

    # Resolve target chapter and actions for selected_topic
    chapter = _find_chapter_for_topic(course, selected_topic["topic_id"])
    reason_text = _format_topic_recommendation_reason(selected_topic, priority_level)
    unres_cnt = selected_topic.get("unresolved_count", 0)

    if priority_level == 1:
        badge_label = "RECOMMENDED FOR REVIEW"
        action_type = "topic_review"
    elif priority_level == 2:
        badge_label = "RECOMMENDED FOR REVIEW" if unres_cnt > 0 else "PRACTICE RECOMMENDED"
        action_type = "topic_practice"
    elif priority_level == 3:
        badge_label = "RECOMMENDED FOR REVIEW"
        action_type = "topic_review"
    else:  # priority_level == 4
        badge_label = "ASSESSMENT RECOMMENDED"
        action_type = "topic_assessment"

    if chapter:
        review_url = f"/chapters/{chapter.id}/review/"
        quiz_url = f"/chapters/{chapter.id}/quiz/"
        ch_title = chapter.title
        ch_id = chapter.id
    else:
        review_url = f"/courses/{course.id}/"
        quiz_url = f"/courses/{course.id}/"
        ch_title = ""
        ch_id = None

    if priority_level in (1, 2, 3):
        button_label = "Open Chapter Review →"
        url = review_url
        if unres_cnt > 0:
            secondary_button_label = "Start Review Run →"
            secondary_url = "/dungeon/"
        else:
            secondary_button_label = "Take Chapter Quiz →"
            secondary_url = quiz_url
    else:  # priority_level == 4 (Assessment needed)
        button_label = "Open Chapter Review →"
        url = review_url
        secondary_button_label = "Take Chapter Quiz →"
        secondary_url = quiz_url

    return {
        "has_recommendation": True,
        "is_topic_aware": True,
        "topic_id": selected_topic["topic_id"],
        "topic_key": selected_topic["topic_key"],
        "topic_name": selected_topic["topic_name"],
        "evidence_state": selected_topic["evidence_state"],
        "priority_level": priority_level,
        "badge_label": badge_label,
        "title": selected_topic["topic_name"],
        "reason": reason_text,
        "button_label": button_label,
        "url": url,
        "secondary_button_label": secondary_button_label,
        "secondary_url": secondary_url,
        "action_type": action_type,
        "chapter_id": ch_id,
        "chapter_title": ch_title,
        "unresolved_count": unres_cnt,
        "accuracy": selected_topic.get("accuracy", 0.0),
    }


