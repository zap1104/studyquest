"""Service functions for TE-3B Starting Knowledge Check.

Implements:
1. Question selection (5-10 validated questions stratified across course topics).
2. Starting knowledge check creation & single-offer tracking.
3. Scoring & topic evidence calculation without affecting course grades or progression.
4. Study Focus suggestions and user-approved adoption.
"""

from typing import Optional, List, Dict, Any
from django.utils import timezone
from django.contrib.auth.models import User
from courses.models import Course, CourseTopic, Question, StartingKnowledgeCheck, LearningFocus
from courses.grading import _grade_question
from courses.learning_focus_service import create_manual_learning_focus, link_focus_to_course


def select_knowledge_check_questions(
    course: Course,
    min_questions: int = 5,
    max_questions: int = 10,
) -> List[Question]:
    """Selects 5-10 existing topic-tagged questions from the course, stratified across topics.

    Ensures balanced topic coverage using uploaded materials only.
    """
    if not course:
        return []

    topics = list(course.topics.all().order_by("order", "id"))
    if not topics:
        return []

    # Map each topic to its available questions
    questions_by_topic: Dict[int, List[Question]] = {}
    for topic in topics:
        qs = list(
            Question.objects.filter(
                quiz__chapter__course=course,
                topic_links__topic=topic,
            )
            .distinct()
            .select_related("quiz", "quiz__chapter")
            .prefetch_related("choices", "topic_links__topic")
            .order_by("quiz__chapter__order", "id")
        )
        if qs:
            questions_by_topic[topic.id] = qs

    if not questions_by_topic:
        return []

    selected: List[Question] = []
    selected_ids = set()

    # Stratified round-robin selection across topics
    active_topics = [t.id for t in topics if t.id in questions_by_topic]
    changed = True
    while changed and len(selected) < max_questions:
        changed = False
        for tid in active_topics:
            topic_qs = questions_by_topic[tid]
            for q in topic_qs:
                if q.id not in selected_ids:
                    selected.append(q)
                    selected_ids.add(q.id)
                    changed = True
                    break
            if len(selected) >= max_questions:
                break

    # If still below min_questions, attempt to fill with any remaining topic-tagged questions
    if len(selected) < min_questions:
        remaining_qs = (
            Question.objects.filter(
                quiz__chapter__course=course,
                topic_links__isnull=False,
            )
            .exclude(id__in=selected_ids)
            .distinct()
            .select_related("quiz", "quiz__chapter")
            .prefetch_related("choices", "topic_links__topic")
            .order_by("quiz__chapter__order", "id")
        )
        for q in remaining_qs:
            selected.append(q)
            selected_ids.add(q.id)
            if len(selected) >= max_questions:
                break

    return selected


def get_or_create_knowledge_check(
    user: User,
    course: Course,
) -> Optional[StartingKnowledgeCheck]:
    """Retrieves an existing StartingKnowledgeCheck for the course or initializes one if eligible.

    Requires at least 3 topic-tagged questions in the course to offer a check.
    Returns None if course has insufficient topic-tagged questions.
    """
    if not user or not user.is_authenticated or not course:
        return None

    existing = StartingKnowledgeCheck.objects.filter(course=course).first()
    if existing:
        return existing

    # Check eligibility: must have at least 3 topic-tagged questions
    eligible_count = Question.objects.filter(
        quiz__chapter__course=course,
        topic_links__isnull=False,
    ).distinct().count()

    if eligible_count < 3:
        return None

    selected_questions = select_knowledge_check_questions(course)
    if len(selected_questions) < 3:
        return None

    check = StartingKnowledgeCheck.objects.create(
        user=user,
        course=course,
        status=StartingKnowledgeCheck.STATUS_OFFERED,
        question_ids=[q.id for q in selected_questions],
        total_questions=len(selected_questions),
    )
    return check


def grade_knowledge_check(
    check: StartingKnowledgeCheck,
    submitted_answers: Dict[str, Any],
) -> Dict[str, Any]:
    """Grades the Starting Knowledge Check answers and computes topic evidence.

    Does NOT create QuizAttempt records and does NOT affect course progression.
    Updates the StartingKnowledgeCheck with results and marks it completed.
    """
    course = check.course
    questions = list(
        Question.objects.filter(id__in=check.question_ids)
        .prefetch_related("choices", "topic_links__topic")
    )
    # Preserve question order matching check.question_ids
    q_map = {q.id: q for q in questions}
    ordered_questions = [q_map[qid] for qid in check.question_ids if qid in q_map]

    total_earned = 0.0
    total_max = 0.0
    graded_items = []

    # Per-topic counters: topic_id -> {assessed, correct, partial, incorrect, total_ratio}
    topic_stats: Dict[int, Dict[str, Any]] = {}

    for q in ordered_questions:
        q_ans = submitted_answers.get(str(q.id)) or submitted_answers.get(q.id) or {}
        if not isinstance(q_ans, dict):
            # If submitted as raw choice_id or text
            if q.question_type in ("multiple_choice", "true_false"):
                q_ans = {"choice_id": q_ans}
            else:
                q_ans = {"text": str(q_ans)}

        earned, max_pts, feedback = _grade_question(q, q_ans)
        max_pts = max(float(max_pts), 1.0)
        ratio = min(max(float(earned) / max_pts, 0.0), 1.0)
        total_earned += earned
        total_max += max_pts

        is_correct = ratio >= 1.0
        is_partial = 0.0 < ratio < 1.0

        graded_items.append({
            "question_id": q.id,
            "question_text": q.text,
            "earned_points": earned,
            "maximum_points": max_pts,
            "is_correct": is_correct,
            "is_partial": is_partial,
            "feedback": feedback,
        })

        for link in q.topic_links.all():
            tid = link.topic_id
            if tid not in topic_stats:
                topic_stats[tid] = {
                    "topic_id": tid,
                    "topic_name": link.topic.name,
                    "topic_key": link.topic.key,
                    "unique_assessed": 0,
                    "correct_count": 0,
                    "partial_count": 0,
                    "incorrect_count": 0,
                    "total_ratio": 0.0,
                }
            topic_stats[tid]["unique_assessed"] += 1
            if is_correct:
                topic_stats[tid]["correct_count"] += 1
            elif is_partial:
                topic_stats[tid]["partial_count"] += 1
            else:
                topic_stats[tid]["incorrect_count"] += 1
            topic_stats[tid]["total_ratio"] += ratio

    overall_score = round((total_earned / total_max * 100), 1) if total_max > 0 else 0.0

    # Calculate final topic evidence breakdown & suggestions
    topic_evidence_data = {}
    suggested_topic_keys = []

    for tid, stats in topic_stats.items():
        assessed = stats["unique_assessed"]
        acc = round((stats["total_ratio"] / assessed * 100), 1) if assessed > 0 else 0.0
        unresolved = stats["partial_count"] + stats["incorrect_count"]
        needs_attention = acc < 80.0 or unresolved > 0

        topic_evidence_data[str(tid)] = {
            "topic_id": tid,
            "topic_name": stats["topic_name"],
            "topic_key": stats["topic_key"],
            "unique_assessed": assessed,
            "correct_count": stats["correct_count"],
            "partial_count": stats["partial_count"],
            "incorrect_count": stats["incorrect_count"],
            "unresolved_count": unresolved,
            "accuracy": acc,
            "needs_attention": needs_attention,
            "status_label": "Review Recommended" if needs_attention else "Demonstrated",
        }

        if needs_attention:
            suggested_topic_keys.append(stats["topic_key"])

    # If learner excelled on all tested topics, suggest all tested topics for reinforcement
    if not suggested_topic_keys and topic_stats:
        suggested_topic_keys = [s["topic_key"] for s in topic_stats.values()]

    # Persist on check record
    check.answers = submitted_answers
    check.score = overall_score
    check.total_questions = len(ordered_questions)
    check.topic_evidence = topic_evidence_data
    check.suggested_topic_keys = suggested_topic_keys
    check.status = StartingKnowledgeCheck.STATUS_COMPLETED
    check.completed_at = timezone.now()
    check.save()

    return {
        "score": overall_score,
        "total_questions": len(ordered_questions),
        "graded_items": graded_items,
        "topic_evidence": topic_evidence_data,
        "suggested_topic_keys": suggested_topic_keys,
    }


def adopt_knowledge_check_focus(
    user: User,
    check: StartingKnowledgeCheck,
    selected_topic_keys: List[str],
) -> Optional[LearningFocus]:
    """Adopts user-approved topic suggestions into an active Study Focus.

    Strictly requires user approval before creating or updating the Study Focus.
    """
    if not user or not user.is_authenticated or not check or not check.course:
        return None

    course = check.course
    selected_topics = list(CourseTopic.objects.filter(course=course, key__in=selected_topic_keys))
    topic_names = [t.name for t in selected_topics]

    # Look for existing active focus for this course
    existing_focus = LearningFocus.objects.filter(
        user=user,
        linked_course=course,
        status=LearningFocus.STATUS_ACTIVE,
    ).first()

    if existing_focus:
        existing_focus.topic_names = topic_names
        existing_focus.initial_score = check.score
        existing_focus.save(update_fields=["topic_names", "initial_score"])
        focus = existing_focus
    else:
        focus = create_manual_learning_focus(
            user=user,
            subject_name=course.title,
            topic_names=topic_names,
            reason="Starting Knowledge Check Diagnostic",
            activate=True,
        )
        focus.initial_score = check.score
        focus.save(update_fields=["initial_score"])
        link_focus_to_course(focus, course)

    check.focus_adopted = True
    check.save(update_fields=["focus_adopted"])
    return focus
