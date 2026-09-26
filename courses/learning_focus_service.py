import datetime
from typing import Optional, Tuple, Dict, Any, List
from django.utils import dateparse, timezone
from django.db import transaction
from django.contrib.auth.models import User
from .models import Course, Chapter, LearningFocus, QuizAttempt


class DiagnosticPayloadError(ValueError):
    """Raised when an external diagnostic assessment payload is invalid."""
    pass


def validate_external_assessment_payload(payload: Any) -> Tuple[bool, str, Dict[str, Any]]:
    """Validates an external assessment JSON payload against the integration schema.

    Returns (is_valid, error_message, normalized_dict).
    """
    if not isinstance(payload, dict):
        return False, "Payload must be a JSON object.", {}

    ext_id = payload.get("external_assessment_id")
    if not ext_id or not isinstance(ext_id, str) or not ext_id.strip():
        return False, "Missing or invalid 'external_assessment_id'.", {}

    # Extract subject & topics from recommended_focus or subject_results
    rec_focus = payload.get("recommended_focus") or {}
    subject_results = payload.get("subject_results") or []

    subject_code = ""
    subject_name = ""
    topic_names = []
    initial_score = None

    if isinstance(rec_focus, dict) and rec_focus:
        subject_code = str(rec_focus.get("subject_code", "")).strip()
        subject_name = str(rec_focus.get("subject_name", "")).strip()
        raw_topics = rec_focus.get("topics") or []
        if isinstance(raw_topics, list):
            topic_names = [str(t).strip() for t in raw_topics if str(t).strip()]

    # If subject_name is empty, resolve from subject_results
    if not subject_name and isinstance(subject_results, list) and subject_results:
        # Match subject_code or choose highest priority / lowest score
        target_res = None
        if subject_code:
            for res in subject_results:
                if str(res.get("subject_code", "")).strip().lower() == subject_code.lower():
                    target_res = res
                    break
        if not target_res:
            target_res = subject_results[0]

        if isinstance(target_res, dict):
            if not subject_code:
                subject_code = str(target_res.get("subject_code", "")).strip()
            subject_name = str(target_res.get("subject_name", "")).strip()
            score_val = target_res.get("score")
            if isinstance(score_val, (int, float)):
                initial_score = float(score_val)

            # If no topics extracted yet, extract weak_topics
            if not topic_names:
                weak_topics = target_res.get("weak_topics") or []
                if isinstance(weak_topics, list):
                    for wt in weak_topics:
                        if isinstance(wt, dict) and "topic" in wt:
                            topic_names.append(str(wt["topic"]).strip())
                        elif isinstance(wt, str) and wt.strip():
                            topic_names.append(wt.strip())

    # Fallback: if subject_name is still blank, use subject_code
    if not subject_name:
        subject_name = subject_code

    if not subject_name:
        return False, "Could not identify subject name or code from assessment payload.", {}

    # Extract overall initial score if available
    if initial_score is None and isinstance(subject_results, list) and subject_results:
        first_score = subject_results[0].get("score")
        if isinstance(first_score, (int, float)):
            initial_score = float(first_score)

    # Parse assessed_at timestamp if provided
    assessed_at = None
    raw_date = payload.get("assessed_at")
    if raw_date and isinstance(raw_date, str):
        parsed = dateparse.parse_datetime(raw_date)
        if parsed:
            assessed_at = parsed if timezone.is_aware(parsed) else timezone.make_aware(parsed)

    normalized = {
        "external_assessment_id": ext_id.strip(),
        "student_reference": str(payload.get("student_reference", "")).strip(),
        "assessment_title": str(payload.get("assessment_title", "Diagnostic Assessment")).strip(),
        "subject_code": subject_code,
        "subject_name": subject_name,
        "topic_names": topic_names,
        "initial_score": initial_score,
        "assessed_at": assessed_at,
    }

    return True, "", normalized


def import_external_assessment(user: User, payload: Dict[str, Any]) -> Tuple[LearningFocus, bool, str]:
    """Validates and imports an external diagnostic assessment payload for the user.

    Returns (learning_focus, created, message).
    Idempotent: Re-importing the same external_assessment_id returns the existing record.
    """
    is_valid, error_msg, normalized = validate_external_assessment_payload(payload)
    if not is_valid:
        raise DiagnosticPayloadError(error_msg)

    ext_id = normalized["external_assessment_id"]

    # Check for existing record
    existing = LearningFocus.objects.filter(
        user=user,
        source=LearningFocus.SOURCE_EXTERNAL,
        external_assessment_id=ext_id,
    ).first()

    if existing:
        return existing, False, "Assessment result has already been imported."

    focus = LearningFocus.objects.create(
        user=user,
        source=LearningFocus.SOURCE_EXTERNAL,
        status=LearningFocus.STATUS_PENDING,
        external_assessment_id=ext_id,
        subject_code=normalized["subject_code"],
        subject_name=normalized["subject_name"],
        topic_names=normalized["topic_names"],
        initial_score=normalized["initial_score"],
        assessed_at=normalized["assessed_at"],
        source_payload=payload,
    )

    return focus, True, "Assessment imported successfully. Please review and approve your study focus."


@transaction.atomic
def create_manual_learning_focus(
    user: User,
    subject_name: str,
    topic_names: Optional[List[str]] = None,
    subject_code: str = "",
    reason: str = "",
    activate: bool = True,
) -> LearningFocus:
    """Creates a student-selected learning focus."""
    subject_name = (subject_name or "").strip()
    if not subject_name:
        raise ValueError("Subject name is required.")

    cleaned_topics = [t.strip() for t in (topic_names or []) if str(t).strip()]

    # If activating, complete any previously active focus
    if activate:
        LearningFocus.objects.filter(
            user=user,
            status=LearningFocus.STATUS_ACTIVE,
        ).update(status=LearningFocus.STATUS_COMPLETED)

    focus = LearningFocus.objects.create(
        user=user,
        source=LearningFocus.SOURCE_MANUAL,
        status=LearningFocus.STATUS_ACTIVE if activate else LearningFocus.STATUS_PENDING,
        subject_code=(subject_code or "").strip(),
        subject_name=subject_name,
        topic_names=cleaned_topics,
        source_payload={"reason": reason.strip()} if reason else {},
    )
    return focus


@transaction.atomic
def activate_learning_focus(focus: LearningFocus) -> LearningFocus:
    """Sets a focus as active, completing any existing active focus for this user."""
    LearningFocus.objects.filter(
        user=focus.user,
        status=LearningFocus.STATUS_ACTIVE,
    ).exclude(pk=focus.pk).update(status=LearningFocus.STATUS_COMPLETED)

    focus.status = LearningFocus.STATUS_ACTIVE
    focus.save(update_fields=["status", "updated_at"])
    return focus


def dismiss_learning_focus(focus: LearningFocus) -> LearningFocus:
    """Marks a pending or active focus as dismissed."""
    focus.status = LearningFocus.STATUS_DISMISSED
    focus.save(update_fields=["status", "updated_at"])
    return focus


def complete_learning_focus(focus: LearningFocus) -> LearningFocus:
    """Marks a focus as completed."""
    focus.status = LearningFocus.STATUS_COMPLETED
    focus.save(update_fields=["status", "updated_at"])
    return focus


def link_focus_to_course(focus: LearningFocus, course: Course) -> LearningFocus:
    """Associates an existing or newly generated Course with this LearningFocus."""
    if course.user_id != focus.user_id:
        raise ValueError("Cannot link focus to a course owned by another user.")

    focus.linked_course = course
    focus.save(update_fields=["linked_course", "updated_at"])
    return focus


def get_active_learning_focus(user: User) -> Optional[LearningFocus]:
    """Returns the user's currently active LearningFocus, if any."""
    if not user or not user.is_authenticated:
        return None
    return LearningFocus.objects.filter(user=user, status=LearningFocus.STATUS_ACTIVE).first()


def get_pending_learning_focus(user: User) -> Optional[LearningFocus]:
    """Returns the latest pending diagnostic focus awaiting user approval, if any."""
    if not user or not user.is_authenticated:
        return None
    return LearningFocus.objects.filter(user=user, status=LearningFocus.STATUS_PENDING).first()


def find_matching_courses(user: User, focus: LearningFocus) -> List[Course]:
    """Finds existing active courses belonging to the user that match the focus subject."""
    if not user or not user.is_authenticated:
        return []

    code = (focus.subject_code or "").strip().lower()
    name = (focus.subject_name or "").strip().lower()

    user_courses = Course.objects.filter(user=user, status="active")
    matches = []
    seen_ids = set()

    for course in user_courses:
        c_title = course.title.lower()
        # Direct match on code or name
        if (code and code in c_title) or (name and name in c_title):
            matches.append(course)
            seen_ids.add(course.id)

    return matches


def generate_course_creation_prefill(focus: LearningFocus) -> Dict[str, Any]:
    """Generates pre-fill form data for Course Creation based on the focus."""
    emphasis_parts = []
    if focus.topic_names:
        emphasis_parts.append(f"Focus especially on: {', '.join(focus.topic_names)}.")
    if focus.initial_score is not None:
        emphasis_parts.append(f"Student scored {focus.initial_score:.0f}% on diagnostic assessment.")

    emphasis_text = " ".join(emphasis_parts)

    return {
        "title": f"{focus.subject_name} Focus Review",
        "review_emphasis": emphasis_text,
        "focus_id": focus.id,
        "subject_name": focus.subject_name,
        "topic_names": focus.topic_names,
    }


def annotate_course_chapters_with_focus(chapters: List[Chapter], focus: Optional[LearningFocus]) -> None:
    """Annotates each chapter with is_focus_target and matched_focus_topics."""
    if not focus or not focus.topic_names:
        for ch in chapters:
            ch.is_focus_target = False
            ch.matched_focus_topics = []
        return

    topics = [t.strip() for t in focus.topic_names if str(t).strip()]
    for ch in chapters:
        c_text = f"{ch.title} {ch.review_content or ''}".lower()
        matched = [t for t in topics if t.lower() in c_text]
        ch.is_focus_target = len(matched) > 0
        ch.matched_focus_topics = matched


def calculate_focus_mastery(focus: LearningFocus) -> Dict[str, Any]:
    """Evaluates learner's progress and mastery on the concepts targeted by this LearningFocus.
    
    Compares the external diagnostic baseline (e.g. 42%) against StudyQuest
    quiz performance and Review Run resolution.
    """
    if not focus or not focus.linked_course:
        return {
            "has_course": False,
            "is_mastered": False,
            "mastered_chapters": 0,
            "total_targets": 0,
            "average_score": 0.0,
            "diagnostic_baseline": focus.initial_score if focus else None,
            "gain": None,
            "chapter_details": [],
        }

    course = focus.linked_course
    chapters = list(course.chapters.select_related("quiz").all())
    topics = [t.lower() for t in (focus.topic_names or []) if str(t).strip()]

    targeted_chapters = []
    for chap in chapters:
        if topics:
            c_text = f"{chap.title} {chap.review_content or ''}".lower()
            if any(t in c_text for t in topics):
                targeted_chapters.append(chap)
        else:
            targeted_chapters.append(chap)

    if not targeted_chapters:
        targeted_chapters = chapters

    total_targets = len(targeted_chapters)
    if total_targets == 0:
        return {
            "has_course": True,
            "is_mastered": False,
            "mastered_chapters": 0,
            "total_targets": 0,
            "average_score": 0.0,
            "diagnostic_baseline": focus.initial_score,
            "gain": None,
            "chapter_details": [],
        }

    mastered_count = 0
    scores = []
    chapter_details = []

    for chap in targeted_chapters:
        best_pct = 0.0
        attempts = QuizAttempt.objects.filter(user=focus.user, quiz__chapter=chap)
        if attempts.exists():
            for att in attempts:
                pct = (att.review_data or {}).get("percentage")
                if pct is None:
                    pct = (att.score / att.total_questions * 100) if att.total_questions > 0 else 0
                if pct > best_pct:
                    best_pct = float(pct)
            scores.append(best_pct)

        # Passing threshold is 80% (guided standard)
        is_passed = best_pct >= 80.0
        if is_passed:
            mastered_count += 1

        chapter_details.append({
            "id": chap.id,
            "title": chap.title,
            "best_percentage": best_pct,
            "is_passed": is_passed,
        })

    average_score = round(sum(scores) / len(scores), 1) if scores else 0.0
    is_mastered = (mastered_count == total_targets) and total_targets > 0

    gain = None
    if focus.initial_score is not None and scores:
        gain = round(average_score - float(focus.initial_score), 1)

    return {
        "has_course": True,
        "is_mastered": is_mastered,
        "mastered_chapters": mastered_count,
        "total_targets": total_targets,
        "average_score": average_score,
        "diagnostic_baseline": focus.initial_score,
        "gain": gain,
        "chapter_details": chapter_details,
    }


