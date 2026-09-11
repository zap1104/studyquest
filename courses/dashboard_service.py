"""
courses/dashboard_service.py
Dedicated service for StudyQuest Dashboard:
- Immediate next learning action resolver based on actual progression rules
- Contextual streak guidance
- Recent active courses curation excluding featured course
"""

from django.db.models import Max
from django.urls import reverse
from django.utils import timezone

from .models import ChapterCompletion, Course, QuizAttempt, UserProfile

QUIZ_PASS_THRESHOLD = 75
GUIDED_PASS_THRESHOLD = 75
PRIOR_KNOWLEDGE_THRESHOLD = 90


def get_streak_status(profile):
    """
    Returns academically neutral, truthful streak guidance based on whether
    a qualifying activity (chapter reading or quiz) occurred today.
    """
    today = timezone.now().date()
    streak = getattr(profile, "streak_days", 0) if profile else 0
    last_study_date = getattr(profile, "last_study_date", None) if profile else None
    studied_today = (last_study_date == today)

    if studied_today:
        return {
            "status": "secured",
            "message": f"{streak}-day streak secured for today",
            "badge_label": "Secured today",
            "icon": "🔥",
            "studied_today": True,
            "streak": streak,
        }
    elif streak > 0:
        return {
            "status": "pending",
            "message": f"Complete a chapter or pass a quiz today to continue your {streak}-day streak.",
            "badge_label": "Action needed today",
            "icon": "🔥",
            "studied_today": False,
            "streak": streak,
        }
    else:
        return {
            "status": "zero",
            "message": "Complete a chapter or pass a quiz to begin a study streak.",
            "badge_label": "Start your streak",
            "icon": "⚡",
            "studied_today": False,
            "streak": 0,
        }


def _get_chapter_completion_state(user, chapter):
    """
    Helper checking reading completion and dual-route quiz passing status.
    """
    lesson_completed = ChapterCompletion.objects.filter(user=user, chapter=chapter).exists()
    quiz = getattr(chapter, "quiz", None)
    has_quiz = quiz is not None

    best_percentage = None
    if has_quiz:
        attempts = QuizAttempt.objects.filter(user=user, quiz=quiz, total_questions__gt=0)
        for attempt in attempts:
            pct = round(attempt.score / max(attempt.total_questions, 1) * 100)
            if best_percentage is None or pct > best_percentage:
                best_percentage = pct

    quiz_passed = (best_percentage is not None and best_percentage >= GUIDED_PASS_THRESHOLD)
    prior_knowledge_passed = (best_percentage is not None and best_percentage >= PRIOR_KNOWLEDGE_THRESHOLD)
    has_failed_attempt = (best_percentage is not None and best_percentage < GUIDED_PASS_THRESHOLD)
    tested_out = prior_knowledge_passed and not lesson_completed

    if has_quiz:
        completed = (lesson_completed and quiz_passed) or prior_knowledge_passed
    else:
        completed = lesson_completed

    unlock_route = "guided" if (lesson_completed and (not has_quiz or quiz_passed)) else ("prior_knowledge" if prior_knowledge_passed else None)

    return {
        "lesson_completed": lesson_completed,
        "has_quiz": has_quiz,
        "best_percentage": best_percentage,
        "quiz_passed": quiz_passed,
        "prior_knowledge_passed": prior_knowledge_passed,
        "has_failed_attempt": has_failed_attempt,
        "tested_out": tested_out,
        "completed": completed,
        "unlock_route": unlock_route,
    }


def _calculate_course_summary(user, course):
    chapters = list(course.chapters.all().order_by("order"))
    total = len(chapters)
    if total == 0:
        return {"total": 0, "completed": 0, "pct": 0, "is_complete": False}

    completed_count = sum(1 for ch in chapters if _get_chapter_completion_state(user, ch)["completed"])
    pct = int((completed_count / total) * 100)
    return {
        "total": total,
        "completed": completed_count,
        "pct": pct,
        "is_complete": (completed_count == total and total > 0),
    }


def get_dashboard_next_action(user):
    """
    Deterministic resolution of the primary "Next in Your Course" card.
    Priority:
    1. Last opened incomplete course
    2. Most recently updated incomplete course
    3. If all courses complete, all_courses_completed state
    4. If no courses exist, no_courses state
    """
    profile = (
        UserProfile.objects.filter(user=user)
        .select_related("last_opened_course", "last_opened_chapter")
        .first()
    )
    active_courses = list(
        Course.objects.filter(user=user, status="active")
        .prefetch_related("chapters", "chapters__quiz", "chapters__quiz__attempts")
        .order_by("-updated_at")
    )

    if not active_courses:
        create_url = reverse("courses:course_create")
        return {
            "state": "no_courses",
            "course": None,
            "chapter": None,
            "eyebrow": "Welcome to StudyQuest",
            "title": "Start Your First Study Journey",
            "subtitle": "Transform your syllabus, slides, notes, or reviewers into structured chapters and quizzes.",
            "status_text": "No active courses yet. Create one in seconds with StudyQuest AI.",
            "button_label": "⚡ Create Your First Course",
            "button_url": create_url,
            "primary_button_label": "⚡ Create Your First Course",
            "primary_button_url": create_url,
            "secondary_button_label": None,
            "secondary_button_url": None,
            "secondary_button_disabled": False,
            "lock_reason": None,
            "progress_pct": 0,
            "completed_chapters": 0,
            "total_chapters": 0,
        }

    # Identify candidate course
    candidate = None
    if profile and profile.last_opened_course and profile.last_opened_course.status == "active":
        cand_summary = _calculate_course_summary(user, profile.last_opened_course)
        if not cand_summary["is_complete"]:
            candidate = profile.last_opened_course

    if not candidate:
        # Find first incomplete active course
        for c in active_courses:
            summary = _calculate_course_summary(user, c)
            if not summary["is_complete"]:
                candidate = c
                break

    # If all active courses are complete:
    if not candidate:
        first_course = active_courses[0]
        first_summary = _calculate_course_summary(user, first_course)
        create_url = reverse("courses:course_create")
        review_url = reverse("courses:course_detail", args=[first_course.pk])
        return {
            "state": "all_courses_completed",
            "course": first_course,
            "chapter": None,
            "eyebrow": "All Courses Complete",
            "title": first_course.title,
            "subtitle": "All chapters and knowledge checks completed across your courses.",
            "status_text": "You have mastered all active review journeys! Create a new course or review previous material.",
            "button_label": "⚡ Create New Course",
            "button_url": create_url,
            "primary_button_label": "⚡ Create New Course",
            "primary_button_url": create_url,
            "secondary_button_label": "🎓 Review Course →",
            "secondary_button_url": review_url,
            "secondary_button_disabled": False,
            "lock_reason": None,
            "review_url": review_url,
            "progress_pct": 100,
            "completed_chapters": first_summary["completed"],
            "total_chapters": first_summary["total"],
        }

    # Evaluate chapters in the candidate course
    chapters = list(candidate.chapters.all().order_by("order"))
    course_summary = _calculate_course_summary(user, candidate)

    for ch in chapters:
        ch_state = _get_chapter_completion_state(user, ch)
        if not ch_state["completed"]:
            review_url = reverse("courses:chapter_review", args=[ch.pk])
            quiz_url = reverse("courses:chapter_quiz", args=[ch.pk]) if ch_state["has_quiz"] else None

            # Case A: Lesson unread, but quiz passed at 75%-89% (reading required to advance)
            if not ch_state["lesson_completed"] and ch_state["quiz_passed"]:
                next_order = ch.order + 1
                return {
                    "state": "reading_required_for_pass",
                    "course": candidate,
                    "chapter": ch,
                    "lesson_completed": ch_state["lesson_completed"],
                    "review_url": review_url,
                    "eyebrow": "Complete Reading to Advance",
                    "title": candidate.title,
                    "subtitle": f"Chapter {ch.order}: {ch.title}",
                    "status_text": f"Scored {ch_state['best_percentage']}% on quiz. Mark the chapter reading as complete to unlock Chapter {next_order} (or score ≥ 90% on the quiz to bypass).",
                    "button_label": f"📖 Complete Chapter {ch.order} Reading (+15 XP) →",
                    "button_url": review_url,
                    "primary_button_label": f"📖 Complete Chapter {ch.order} Reading (+15 XP) →",
                    "primary_button_url": review_url,
                    "secondary_button_label": f"⚡ Retake Quiz (Aim for ≥90%)",
                    "secondary_button_url": quiz_url,
                    "secondary_button_disabled": False,
                    "lock_reason": None,
                    "progress_pct": course_summary["pct"],
                    "completed_chapters": course_summary["completed"],
                    "total_chapters": course_summary["total"],
                }

            # Case B: Lesson unread / reading incomplete
            if not ch_state["lesson_completed"]:
                if ch_state["has_failed_attempt"]:
                    return {
                        "state": "quiz_not_passed",
                        "course": candidate,
                        "chapter": ch,
                        "lesson_completed": ch_state["lesson_completed"],
                        "review_url": review_url,
                        "eyebrow": "Knowledge Check Retake",
                        "title": candidate.title,
                        "subtitle": f"Chapter {ch.order}: {ch.title}",
                        "status_text": f"Previous quiz score was {ch_state['best_percentage']}%. Complete the chapter reading before retaking, or score ≥ 90% to bypass.",
                        "button_label": f"📖 Continue Reading Chapter {ch.order} →",
                        "button_url": review_url,
                        "primary_button_label": f"📖 Continue Reading Chapter {ch.order} →",
                        "primary_button_url": review_url,
                        "secondary_button_label": f"📝 Retake Chapter {ch.order} Quiz",
                        "secondary_button_url": quiz_url,
                        "secondary_button_disabled": False,
                        "lock_reason": None,
                        "progress_pct": course_summary["pct"],
                        "completed_chapters": course_summary["completed"],
                        "total_chapters": course_summary["total"],
                    }
                else:
                    return {
                        "state": "reading_not_started",
                        "course": candidate,
                        "chapter": ch,
                        "lesson_completed": ch_state["lesson_completed"],
                        "review_url": review_url,
                        "eyebrow": "Next in Your Course",
                        "title": candidate.title,
                        "subtitle": f"Chapter {ch.order}: {ch.title}",
                        "status_text": "Lesson review in progress. Complete chapter reading and score ≥ 75% on quiz, or score ≥ 90% to test out directly.",
                        "button_label": f"📖 Continue Reading Chapter {ch.order} →",
                        "button_url": review_url,
                        "primary_button_label": f"📖 Continue Reading Chapter {ch.order} →",
                        "primary_button_url": review_url,
                        "secondary_button_label": f"⚡ Try Quiz First (≥90% to bypass)" if ch_state["has_quiz"] else None,
                        "secondary_button_url": quiz_url,
                        "secondary_button_disabled": False if ch_state["has_quiz"] else True,
                        "lock_reason": None,
                        "progress_pct": course_summary["pct"],
                        "completed_chapters": course_summary["completed"],
                        "total_chapters": course_summary["total"],
                    }

            # Case C: Lesson completed, quiz not passed
            if ch_state["has_quiz"] and not ch_state["quiz_passed"]:
                if ch_state["has_failed_attempt"]:
                    return {
                        "state": "quiz_not_passed",
                        "course": candidate,
                        "chapter": ch,
                        "lesson_completed": ch_state["lesson_completed"],
                        "review_url": review_url,
                        "eyebrow": "Knowledge Check Retake",
                        "title": candidate.title,
                        "subtitle": f"Chapter {ch.order}: {ch.title}",
                        "status_text": f"Previous quiz score was {ch_state['best_percentage']}%. Review key concepts and retake to achieve passing score (≥ 75%).",
                        "button_label": f"📝 Review & Retake Chapter {ch.order} Quiz →",
                        "button_url": quiz_url,
                        "primary_button_label": f"📝 Review & Retake Chapter {ch.order} Quiz →",
                        "primary_button_url": quiz_url,
                        "secondary_button_label": f"📖 Review Chapter {ch.order}",
                        "secondary_button_url": review_url,
                        "secondary_button_disabled": False,
                        "lock_reason": None,
                        "progress_pct": course_summary["pct"],
                        "completed_chapters": course_summary["completed"],
                        "total_chapters": course_summary["total"],
                    }
                else:
                    return {
                        "state": "quiz_ready",
                        "course": candidate,
                        "chapter": ch,
                        "lesson_completed": ch_state["lesson_completed"],
                        "review_url": review_url,
                        "eyebrow": "Knowledge Check Ready",
                        "title": candidate.title,
                        "subtitle": f"Chapter {ch.order}: {ch.title}",
                        "status_text": "Reading completed. The chapter quiz is ready (score ≥ 75% to unlock next chapter).",
                        "button_label": f"⚡ Take Chapter {ch.order} Quiz →",
                        "button_url": quiz_url,
                        "primary_button_label": f"⚡ Take Chapter {ch.order} Quiz →",
                        "primary_button_url": quiz_url,
                        "secondary_button_label": f"📖 Review Chapter {ch.order}",
                        "secondary_button_url": review_url,
                        "secondary_button_disabled": False,
                        "lock_reason": None,
                        "progress_pct": course_summary["pct"],
                        "completed_chapters": course_summary["completed"],
                        "total_chapters": course_summary["total"],
                    }

    # Fallback if candidate happens to be complete
    detail_url = reverse("courses:course_detail", args=[candidate.pk])
    create_url = reverse("courses:course_create")
    return {
        "state": "course_completed",
        "course": candidate,
        "chapter": None,
        "eyebrow": "Course Completed",
        "title": candidate.title,
        "subtitle": f"{course_summary['total']} of {course_summary['total']} chapters completed",
        "status_text": "Course completed! You have cleared all chapters and quizzes.",
        "button_label": "🎓 Review Course →",
        "button_url": detail_url,
        "primary_button_label": "🎓 Review Course →",
        "primary_button_url": detail_url,
        "secondary_button_label": "⚡ Create New Course",
        "secondary_button_url": create_url,
        "secondary_button_disabled": False,
        "lock_reason": None,
        "progress_pct": 100,
        "completed_chapters": course_summary["completed"],
        "total_chapters": course_summary["total"],
    }


def get_recent_active_courses(user, limit=3, exclude_course_id=None):
    """
    Returns up to `limit` active courses, explicitly excluding `exclude_course_id`
    to prevent repeating the course featured in the primary Continue Learning card.
    """
    query = Course.objects.filter(user=user, status="active")
    if exclude_course_id:
        query = query.exclude(pk=exclude_course_id)

    courses = list(
        query.prefetch_related("chapters", "chapters__quiz", "chapters__quiz__attempts")
        .order_by("-updated_at")[:limit]
    )

    for c in courses:
        summary = _calculate_course_summary(user, c)
        c.progress_pct = summary["pct"]
        c.completed_chapter_count = summary["completed"]
        c.total_chapter_count = summary["total"]
        c.is_complete = summary["is_complete"]

    return courses

