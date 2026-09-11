import json
import logging
import re
import unicodedata
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.db import transaction
from django.db.models import Max
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .credits import (
    award_course_completion_bonus,
    consume_generation_credit,
    get_generation_eligibility,
)
from .models import (
    Chapter,
    ChapterCompletion,
    Choice,
    Course,
    Question,
    Quiz,
    QuizAttempt,
    UserCourseCompletion,
    UserProfile,
)
from .dashboard_service import (
    get_dashboard_next_action,
    get_recent_active_courses,
    get_streak_status,
)
from .gamification import (
    get_achievement_preview,
    get_active_course_progress,
    get_leaderboard_standings,
    get_level_progress,
    get_tier_info,
    get_tier_progress,
    get_user_achievements,
    get_weekly_momentum,
)
from .schemas import GenerationPreferences
from .services import CourseGenerationError, generate_course_journey, SourceBundleError

logger = logging.getLogger(__name__)

QUIZ_PASS_THRESHOLD = 75
LESSON_COMPLETION_XP = 15

REVIEW_ANCHOR_BY_TYPE = {
    "multiple_choice": "key-terms",
    "true_false": "overview",
    "identification": "key-terms",
    "enumeration": "enumerations",
}


# --------------------------------------------------
# 1. NORMALIZATION & PROGRESSION HELPERS
# --------------------------------------------------
def normalize_text_answer(value):
    """
    Deterministic normalization for Identification and Enumeration.
    Avoids fuzzy matching to prevent credit on incorrect technical terms.
    """
    value = unicodedata.normalize("NFKC", value or "")
    value = value.casefold()
    value = re.sub(r"[^\w\s.]", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip().strip(".")


def calculate_quiz_xp(percentage):
    if percentage >= 100:
        return 60
    if percentage >= 85:
        return 45
    if percentage >= QUIZ_PASS_THRESHOLD:
        return 35
    if percentage >= 50:
        return 20
    return 10


def get_reading_session_key(chapter):
    return f"chapter_read_to_end:{chapter.pk}"


def has_read_to_end(request, chapter):
    return bool(request.session.get(get_reading_session_key(chapter), False))


def get_best_quiz_percentage(user, chapter):
    quiz = getattr(chapter, "quiz", None)
    if quiz is None:
        return None

    attempts = QuizAttempt.objects.filter(
        user=user,
        quiz=quiz,
        total_questions__gt=0,
    ).only("score", "total_questions")

    best_percentage = None
    for attempt in attempts:
        percentage = round(attempt.score / max(attempt.total_questions, 1) * 100)
        if best_percentage is None or percentage > best_percentage:
            best_percentage = percentage

    return best_percentage


def get_chapter_completion_state(user, chapter):
    """
    A chapter is completed when its quiz score reaches 75%, with or without
    a completed lesson. Reading remains part of the guided learning path,
    but it is not a separate completion threshold.
    """
    lesson_completed = ChapterCompletion.objects.filter(user=user, chapter=chapter).exists()
    best_quiz_percentage = get_best_quiz_percentage(user, chapter)
    quiz = getattr(chapter, "quiz", None)
    has_quiz = quiz is not None

    quiz_passed = (
        best_quiz_percentage is not None
        and best_quiz_percentage >= QUIZ_PASS_THRESHOLD
    )

    tested_out = (
        best_quiz_percentage is not None
        and best_quiz_percentage >= QUIZ_PASS_THRESHOLD
        and not lesson_completed
    )

    if has_quiz:
        completed = quiz_passed
    else:
        completed = lesson_completed

    return {
        "lesson_completed": lesson_completed,
        "has_quiz": has_quiz,
        "best_quiz_percentage": best_quiz_percentage,
        "quiz_passed": quiz_passed,
        "tested_out": tested_out,
        "completed": completed,
    }


def get_previous_chapter(chapter):
    return (
        Chapter.objects.filter(course=chapter.course, order__lt=chapter.order)
        .order_by("-order")
        .first()
    )


def get_next_chapter(chapter):
    return (
        Chapter.objects.filter(course=chapter.course, order__gt=chapter.order)
        .order_by("order")
        .first()
    )


def get_chapter_status(user, chapter):
    """Returns 'completed', 'available', or 'locked'."""
    current_state = get_chapter_completion_state(user, chapter)
    if current_state["completed"]:
        return "completed"

    previous_chapter = get_previous_chapter(chapter)
    if previous_chapter is None:
        return "available"

    previous_state = get_chapter_completion_state(user, previous_chapter)
    if previous_state["completed"]:
        return "available"

    return "locked"


def calculate_course_progress(user, course):
    chapters = list(course.chapters.all())
    total_chapters = len(chapters)
    if total_chapters == 0:
        return {"total_chapters": 0, "completed_count": 0, "percentage": 0}

    completed_count = sum(
        1 for chapter in chapters if get_chapter_completion_state(user, chapter)["completed"]
    )
    percentage = int((completed_count / total_chapters) * 100)
    return {
        "total_chapters": total_chapters,
        "completed_count": completed_count,
        "percentage": percentage,
    }


# --------------------------------------------------
# 2. AUTH & DASHBOARD VIEWS
# --------------------------------------------------
def auth_portal(request):
    if request.user.is_authenticated:
        return redirect("courses:dashboard")

    active_tab = request.GET.get("tab", "login")
    login_form = AuthenticationForm()
    signup_form = UserCreationForm()

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "login":
            login_form = AuthenticationForm(request, data=request.POST)
            if login_form.is_valid():
                login(request, login_form.get_user())
                return redirect("courses:dashboard")
            active_tab = "login"
        elif action == "signup":
            signup_form = UserCreationForm(request.POST)
            if signup_form.is_valid():
                user = signup_form.save()
                login(request, user)
                return redirect("courses:dashboard")
            active_tab = "signup"

    return render(request, "courses/login.html", {
        "login_form": login_form,
        "signup_form": signup_form,
        "active_tab": active_tab,
    })


@login_required
def dashboard(request):
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    tier_info = get_tier_info(profile.current_level)
    streak_status = get_streak_status(profile)
    next_action = get_dashboard_next_action(request.user)
    featured_course_id = next_action["course"].pk if next_action.get("course") else None
    recent_courses = get_recent_active_courses(request.user, limit=3, exclude_course_id=featured_course_id)
    weekly_momentum = get_weekly_momentum(request.user)
    achievement_preview = get_achievement_preview(request.user)
    eligibility = get_generation_eligibility(profile)

    return render(request, "courses/dashboard.html", {
        "profile": profile,
        "tier_info": tier_info,
        "streak_status": streak_status,
        "next_action": next_action,
        "recent_courses": recent_courses,
        "courses": recent_courses,
        "weekly_momentum": weekly_momentum,
        "achievement_preview": achievement_preview,
        "eligibility": eligibility,
    })


@login_required
def course_list(request):
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    active_courses = Course.objects.filter(user=request.user, status="active")
    archived_courses = Course.objects.filter(user=request.user, status="archived")
    for course in active_courses:
        progress = calculate_course_progress(request.user, course)
        course.progress_pct = progress["percentage"]
    return render(request, "courses/course_list.html", {
        "profile": profile,
        "active_tab": request.GET.get("tab", "active"),
        "active_courses": active_courses,
        "archived_courses": archived_courses,
        "active_count": active_courses.count(),
        "active_limit": get_generation_eligibility(profile).active_limit,
        "eligibility": get_generation_eligibility(profile),
    })


# --------------------------------------------------
# 3. COURSE CREATION & BUILDER
# --------------------------------------------------
@login_required
def course_create(request):
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    eligibility = get_generation_eligibility(profile)

    if request.method == "GET":
        return render(request, "courses/course_form.html", {
            "eligibility": eligibility,
            "profile": profile,
        })

    if request.method == "POST":
        if not eligibility.allowed:
            return render(
                request,
                "courses/course_form.html",
                {"eligibility": eligibility, "profile": profile},
                status=403,
            )

        content_files = request.FILES.getlist("content_files")
        legacy_file = request.FILES.get("content_file")
        if not content_files and legacy_file:
            content_files = [legacy_file]

        custom_title = request.POST.get("custom_title", "").strip() or request.POST.get("title", "").strip()
        study_focus = request.POST.get("study_focus", "").strip()[:100]

        if not content_files:
            return render(request, "courses/course_form.html", {
                "eligibility": eligibility,
                "profile": profile,
                "generation_failed": True,
                "generation_error": "Please select at least one study file to generate a course.",
                "custom_title": custom_title,
                "study_focus": study_focus,
            })

        if len(content_files) > 3:
            return render(request, "courses/course_form.html", {
                "eligibility": eligibility,
                "profile": profile,
                "generation_failed": True,
                "generation_error": "You can upload a maximum of 3 files per course.",
                "custom_title": custom_title,
                "study_focus": study_focus,
            })

        preference_data = {
            "study_goal": request.POST.get("study_goal", "balanced_review"),
            "assessment_formats": request.POST.getlist("assessment_focus") or ["multiple_choice"],
        }

        try:
            preferences = GenerationPreferences.model_validate(preference_data)
        except Exception:
            return render(request, "courses/course_form.html", {
                "eligibility": eligibility,
                "profile": profile,
                "generation_failed": True,
                "generation_error": "The selected study settings were invalid.",
                "custom_title": custom_title,
                "study_focus": study_focus,
            })

        dummy_course = Course(title=custom_title or "Untitled Course", user=request.user)

        try:
            # Gemini generation is executed outside of any open database transaction
            journey_data = generate_course_journey(
                dummy_course,
                uploaded_files=content_files,
                study_goal=preferences.study_goal,
                assessment_formats=preferences.assessment_formats,
                study_focus=study_focus,
                plan_name=profile.plan,
            )
            journey_data["generation_profile"] = preferences.model_dump()

            # Transaction is isolated strictly to persistence and credit consumption
            with transaction.atomic():
                course = _build_journey(
                    request.user,
                    journey_data=journey_data,
                    custom_title=custom_title,
                )
                consume_generation_credit(request.user, course)

            return redirect("courses:course_detail", pk=course.pk)

        except SourceBundleError as err:
            logger.warning(f"[Source Bundle Validation/Extraction Error]: {err}")
            return render(request, "courses/course_form.html", {
                "eligibility": eligibility,
                "profile": profile,
                "generation_failed": True,
                "generation_error": str(err),
                "failed_filename": err.filename,
                "char_count": err.char_count,
                "max_chars": err.max_chars,
                "custom_title": custom_title,
                "study_focus": study_focus,
            })
        except CourseGenerationError as err:
            logger.error(f"[Course Generation Pipeline Interrupted]: {err}")
            return render(request, "courses/course_form.html", {
                "eligibility": eligibility,
                "profile": profile,
                "generation_failed": True,
                "generation_error": str(err),
                "custom_title": custom_title,
                "study_focus": study_focus,
            })
        except Exception as error:
            logger.error(f"[Course Generation Failed Unexpectedly]: {repr(error)}")
            return render(request, "courses/course_form.html", {
                "eligibility": eligibility,
                "profile": profile,
                "generation_failed": True,
                "generation_error": "Course generation encountered an unexpected error. Please try again.",
                "custom_title": custom_title,
                "study_focus": study_focus,
            })


@transaction.atomic
def _build_journey(course_or_user, journey_data=None, journey_override=None, custom_title=""):
    data = journey_override if journey_override is not None else journey_data

    if isinstance(course_or_user, Course):
        course = course_or_user
        if journey_override is None:
            from .services import _generate_mock_journey
            data = _generate_mock_journey(course)
        course.structured_content = data
        course.save(update_fields=["structured_content"])
    else:
        if data is None:
            raise ValueError("Journey data is required.")
        user = course_or_user
        course_meta = data["course"]
        final_title = custom_title or course_meta["title"]
        course = Course.objects.create(
            user=user,
            title=final_title,
            description=course_meta["description"],
            structured_content=data,
        )

    for chapter_data in data["chapters"]:
        chapter = Chapter.objects.create(
            course=course,
            order=chapter_data["order"],
            title=chapter_data["title"],
            review_content=chapter_data.get("overview", ""),
            source_data=chapter_data,
        )

        quiz_data = chapter_data["quiz"]
        quiz = Quiz.objects.create(chapter=chapter, title=quiz_data["title"])

        for question_data in quiz_data["questions"]:
            question_type = (
                question_data.get("type")
                or question_data.get("question_type", "multiple_choice")
            )

            answer_data = {}
            if question_type == "identification":
                answer_data = {"accepted_answers": question_data.get("accepted_answers", [])}
            elif question_type == "enumeration":
                answer_data = {
                    "expected_items": question_data.get("expected_items", []),
                    "order_matters": question_data.get("order_matters", False),
                }

            question = Question.objects.create(
                quiz=quiz,
                order=question_data["order"],
                question_type=question_type,
                text=question_data["text"],
                explanation=question_data.get("explanation", ""),
                answer_data=answer_data,
            )

            if question_type in {"multiple_choice", "true_false"}:
                for choice_data in question_data.get("choices", []):
                    Choice.objects.create(
                        question=question,
                        text=choice_data["text"],
                        is_correct=choice_data["is_correct"],
                    )

    return course


# --------------------------------------------------
# 4. COURSE DETAIL & CHAPTER REVIEW
# --------------------------------------------------
@login_required
def course_detail(request, pk):
    course = get_object_or_404(Course, pk=pk, user=request.user)
    chapters = list(course.chapters.select_related("quiz").all())

    for chapter in chapters:
        chapter.progress_status = get_chapter_status(request.user, chapter)
        completion_state = get_chapter_completion_state(request.user, chapter)
        chapter.lesson_completed = completion_state["lesson_completed"]
        chapter.quiz_passed = completion_state["quiz_passed"]
        chapter.tested_out = completion_state["tested_out"]
        chapter.best_quiz_percentage = completion_state["best_quiz_percentage"]
        chapter.previous_chapter = get_previous_chapter(chapter)

    progress = calculate_course_progress(request.user, course)

    return render(request, "courses/course_detail.html", {
        "course": course,
        "chapters": chapters,
        "course_progress_pct": progress["percentage"],
        "completed_count": progress["completed_count"],
        "total_chapters": progress["total_chapters"],
    })


@login_required
def chapter_review(request, pk):
    chapter = get_object_or_404(Chapter, pk=pk, course__user=request.user)
    course = chapter.course

    profile = getattr(request.user, "userprofile", None)
    if profile:
        profile.last_opened_course = course
        profile.last_opened_chapter = chapter
        profile.save(update_fields=["last_opened_course", "last_opened_chapter"])

    chapter_status = get_chapter_status(request.user, chapter)
    if chapter_status == "locked":
        previous_chapter = get_previous_chapter(chapter)
        previous_state = (
            get_chapter_completion_state(request.user, previous_chapter)
            if previous_chapter
            else None
        )
        return render(request, "courses/chapter_locked.html", {
            "chapter": chapter,
            "previous_chapter": previous_chapter,
            "prev_chapter": previous_chapter,
            "previous_state": previous_state,
            "course": course,
            "pass_threshold": QUIZ_PASS_THRESHOLD,
        }, status=403)

    progress = calculate_course_progress(request.user, course)
    quiz = getattr(chapter, "quiz", None)
    question_count = quiz.questions.count() if quiz else 0
    completion_state = get_chapter_completion_state(request.user, chapter)
    chapter_data = chapter.source_data or {}
    chapter_overview = (
        chapter_data.get("focus")
        or chapter_data.get("overview")
        or chapter.review_content
        or "No chapter review content is available."
    )

    # Read the latest quiz attempt to display on the activity card
    latest_attempt = (
        QuizAttempt.objects.filter(user=request.user, quiz=quiz)
        .order_by("-completed_at")
        .first()
    ) if quiz else None

    return render(request, "courses/chapter_review.html", {
        "chapter": chapter,
        "chapter_data": chapter_data,
        "chapter_overview": chapter_overview,
        "has_quiz": quiz is not None,
        "question_count": question_count,
        "latest_attempt": latest_attempt,
        "is_completed": completion_state["lesson_completed"],
        "chapter_fully_completed": completion_state["completed"],
        "quiz_passed": completion_state["quiz_passed"],
        "tested_out": completion_state["tested_out"],
        "best_quiz_percentage": completion_state["best_quiz_percentage"],
        "has_read_to_end": has_read_to_end(request, chapter),
        "total_chapters": progress["total_chapters"],
        "completed_count": progress["completed_count"],
        "course_progress_pct": progress["percentage"],
        "pass_threshold": QUIZ_PASS_THRESHOLD,
    })


@login_required
@require_POST
def record_chapter_reading(request, pk):
    """Records that the user reached the end of the lesson reader."""
    chapter = get_object_or_404(Chapter, pk=pk, course__user=request.user)
    if get_chapter_status(request.user, chapter) == "locked":
        return JsonResponse({"error": "This chapter is locked."}, status=403)

    request.session[get_reading_session_key(chapter)] = True
    request.session.modified = True
    return JsonResponse({"recorded": True, "chapter_id": chapter.pk})


@login_required
@require_POST
def complete_chapter(request, pk):
    chapter = get_object_or_404(Chapter, pk=pk, course__user=request.user)

    if get_chapter_status(request.user, chapter) == "locked":
        return JsonResponse({"error": "This chapter is locked."}, status=403)

    if not has_read_to_end(request, chapter):
        return JsonResponse({
            "error": "Finish reviewing the lesson before marking it complete."
        }, status=403)

    completion, created = ChapterCompletion.objects.get_or_create(
        user=request.user, chapter=chapter
    )

    if created:
        profile, _ = UserProfile.objects.get_or_create(user=request.user)
        profile.award_xp(LESSON_COMPLETION_XP, reason=f"Completed lesson: {chapter.title}")
        profile.record_study_activity()

        completion_state = get_chapter_completion_state(request.user, chapter)
        next_chapter = get_next_chapter(chapter)
        bonus = award_course_completion_bonus(request.user, chapter.course)

        return JsonResponse({
            "awarded": True,
            "xp_earned": LESSON_COMPLETION_XP,
            "new_total_xp": profile.total_xp,
            "chapter_completed": completion_state["completed"],
            "quiz_required": (completion_state["has_quiz"] and not completion_state["quiz_passed"]),
            "next_chapter_unlocked": (completion_state["completed"] and next_chapter is not None),
            "course_bonus_awarded": bonus["bonus_awarded"],
        })

    return JsonResponse({"awarded": False, "xp_earned": 0})


@login_required
def course_edit(request, pk):
    course = get_object_or_404(Course, pk=pk, user=request.user)
    if request.method == "POST":
        title = request.POST.get("title", "").strip()
        description = request.POST.get("description", "").strip()
        if title:
            course.title = title
            course.description = description
            course.save(update_fields=["title", "description"])
    return redirect("courses:course_detail", pk=course.pk)


@login_required
@require_POST
def course_delete(request, pk):
    course = get_object_or_404(Course, pk=pk, user=request.user)
    title = course.title
    course.delete()
    messages.success(request, f'Course "{title}" was permanently deleted.')
    return redirect(f"{redirect('courses:course_list').url}?tab=archived")

@login_required
@require_POST
def course_archive(request, pk):
    course = get_object_or_404(Course, pk=pk, user=request.user)
    course.status = "archived"
    course.save(update_fields=["status"])
    return redirect("courses:course_list")


@login_required
@require_POST
def course_restore(request, pk):
    course = get_object_or_404(Course, pk=pk, user=request.user, status="archived")
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    eligibility = get_generation_eligibility(profile)
    if eligibility.active_count >= eligibility.active_limit:
        return JsonResponse({"error": "The active-course limit has been reached."}, status=409)
    course.status = "active"
    course.save(update_fields=["status"])
    return redirect("courses:course_list")


@login_required
def chapter_rename(request, pk):
    chapter = get_object_or_404(Chapter, pk=pk, course__user=request.user)
    if request.method == "POST":
        title = request.POST.get("title", "").strip()
        if title:
            chapter.title = title
            chapter.save(update_fields=["title"])
    return redirect("courses:course_detail", pk=chapter.course.pk)


# --------------------------------------------------
# 5. MIXED-ASSESSMENT GRADING & QUIZ FLOW
# --------------------------------------------------
@login_required
def chapter_quiz(request, pk):
    chapter = get_object_or_404(Chapter, pk=pk, course__user=request.user)

    profile = getattr(request.user, "userprofile", None)
    if profile:
        profile.last_opened_course = chapter.course
        profile.last_opened_chapter = chapter
        profile.save(update_fields=["last_opened_course", "last_opened_chapter"])
    if get_chapter_status(request.user, chapter) == "locked":
        previous_chapter = get_previous_chapter(chapter)
        return render(request, "courses/chapter_locked.html", {
            "chapter": chapter,
            "previous_chapter": previous_chapter,
            "prev_chapter": previous_chapter,
            "course": chapter.course,
            "pass_threshold": QUIZ_PASS_THRESHOLD,
        }, status=403)

    quiz = getattr(chapter, "quiz", None)
    latest_attempt = (
        QuizAttempt.objects.filter(user=request.user, quiz=quiz)
        .order_by("-completed_at")
        .first()
    ) if quiz else None

    return render(request, "courses/chapter_quiz.html", {
        "chapter": chapter,
        "quiz": quiz,
        "latest_attempt": latest_attempt,
    })


def _grade_question(question, submitted_answer):
    max_points = question.max_points()
    if not isinstance(submitted_answer, dict):
        submitted_answer = {}

    # Choice-based grading
    if question.question_type in {"multiple_choice", "true_false"}:
        chosen_choice_id = submitted_answer.get("choice_id") if submitted_answer else None
        correct_choice = next((c for c in question.choices.all() if c.is_correct), None)
        try:
            chosen_choice_id = int(chosen_choice_id)
        except (TypeError, ValueError):
            chosen_choice_id = None
        is_correct = chosen_choice_id is not None and correct_choice is not None and chosen_choice_id == correct_choice.id
        return (
            1 if is_correct else 0,
            max_points,
            {
                "is_correct": is_correct,
                "correct_choice_id": correct_choice.id if correct_choice else None,
                "correct_choice_text": correct_choice.text if correct_choice else "",
            },
        )

    # Identification grading
    if question.question_type == "identification":
        submitted_text = (submitted_answer or {}).get("text", "")
        if not isinstance(submitted_text, str):
            submitted_text = ""
        accepted_answers = question.answer_data.get("accepted_answers", [])
        normalized_submitted = normalize_text_answer(submitted_text)
        is_correct = any(
            normalize_text_answer(a) == normalized_submitted for a in accepted_answers
        )
        return (
            1 if is_correct else 0,
            max_points,
            {
                "is_correct": is_correct,
                "accepted_answers": accepted_answers,
                "canonical_answer": accepted_answers[0] if accepted_answers else "",
            },
        )

    # Enumeration grading
    if question.question_type == "enumeration":
        submitted_items = (submitted_answer or {}).get("items", [])
        if not isinstance(submitted_items, list):
            submitted_items = []
        expected_items = list(question.answer_data.get("expected_items", []))
        order_matters = bool(question.answer_data.get("order_matters", False))

        if order_matters:
            matched_items = []
            for index, expected in enumerate(expected_items):
                if index >= len(submitted_items):
                    break
                sub_norm = normalize_text_answer(submitted_items[index])
                candidates = [expected["canonical"]] + expected.get("accepted_variants", [])
                if any(normalize_text_answer(c) == sub_norm for c in candidates):
                    matched_items.append(expected["canonical"])

            missing_items = [
                exp["canonical"] for exp in expected_items if exp["canonical"] not in matched_items
            ]
        else:
            remaining = list(expected_items)
            matched_items = []
            seen_submissions = set()

            for raw_item in submitted_items:
                normalized = normalize_text_answer(raw_item)
                if not normalized or normalized in seen_submissions:
                    continue
                seen_submissions.add(normalized)

                match_index = None
                for index, expected in enumerate(remaining):
                    candidates = [expected["canonical"]] + expected.get("accepted_variants", [])
                    if any(normalize_text_answer(c) == normalized for c in candidates):
                        match_index = index
                        break

                if match_index is not None:
                    matched_items.append(remaining[match_index]["canonical"])
                    remaining.pop(match_index)

            missing_items = [expected["canonical"] for expected in remaining]

        earned_points = len(matched_items)
        return (
            earned_points,
            max_points,
            {
                "is_correct": (earned_points == max_points),
                "matched_items": matched_items,
                "missing_items": missing_items,
                "order_matters": order_matters,
            },
        )

    return (0, max_points, {"is_correct": False})


@login_required
@require_POST
def check_quiz_answer(request, pk):
    chapter = get_object_or_404(Chapter, pk=pk, course__user=request.user)
    if get_chapter_status(request.user, chapter) == "locked":
        return JsonResponse({"error": "This chapter is locked."}, status=403)

    quiz = getattr(chapter, "quiz", None)
    if quiz is None:
        return JsonResponse({"error": "No quiz for this chapter."}, status=404)

    try:
        payload = json.loads(request.body)
        question_id = payload.get("question_id")
        submitted_answer = payload.get("answer", {})
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON."}, status=400)

    question = get_object_or_404(
        Question.objects.prefetch_related("choices"), pk=question_id, quiz=quiz
    )
    earned_points, maximum_points, feedback = _grade_question(question, submitted_answer)

    return JsonResponse({
        "question_id": question.id,
        "question_type": question.question_type,
        "earned_points": earned_points,
        "maximum_points": maximum_points,
        "explanation": question.explanation,
        **feedback,
    })


@login_required
@require_POST
def submit_quiz(request, pk=None, chapter_id=None):
    target_id = pk or chapter_id
    chapter = get_object_or_404(
        Chapter.objects.select_related("course", "quiz"),
        pk=target_id,
        course__user=request.user,
    )
    quiz = getattr(chapter, "quiz", None)
    if not quiz:
        return JsonResponse({"error": "Quiz not found."}, status=404)

    try:
        data = json.loads(request.body)
        user_answers = data.get("answers", {})
    except (json.JSONDecodeError, AttributeError):
        return JsonResponse({"error": "Invalid JSON payload."}, status=400)

    questions = quiz.questions.prefetch_related("choices").all().order_by("order")
    total_earned = Decimal("0.0")
    total_max = Decimal("0.0")
    review_items = []

    for q in questions:
        ans_payload = user_answers.get(str(q.id)) or user_answers.get(q.id) or {}
        grading_result = _grade_question(q, ans_payload)

        # Unpack tuple or dict defensively
        if isinstance(grading_result, tuple):
            if len(grading_result) == 3:
                earned_val, max_val, feedback = grading_result
            elif len(grading_result) == 4:
                _, earned_val, max_val, feedback = grading_result
            elif len(grading_result) == 2:
                earned_val, feedback = grading_result
                max_val = q.max_points or 1.0
            else:
                earned_val, max_val, feedback = 0, q.max_points or 1.0, {}
        elif isinstance(grading_result, dict):
            earned_val = grading_result.get("earned_points", 0)
            max_val = grading_result.get("maximum_points", q.max_points or 1.0)
            feedback = grading_result
        else:
            earned_val, max_val, feedback = 0, q.max_points or 1.0, {}

        if not isinstance(feedback, dict):
            feedback = {}

        earned_pts = Decimal(str(earned_val or 0))
        max_pts = Decimal(str(max_val or q.max_points or 1.0))
        total_earned += earned_pts
        total_max += max_pts

        if earned_pts >= max_pts and max_pts > 0:
            result_state = "correct"
        elif earned_pts > 0:
            result_state = "partial"
        else:
            result_state = "incorrect"

        review_item = {
            "order": q.order,
            "question_id": q.id,
            "question_type": q.question_type,
            "prompt": q.text,
            "question_text": q.text,
            "earned_points": float(earned_pts),
            "maximum_points": float(max_pts),
            "result_state": result_state,
            "explanation": q.explanation or feedback.get("explanation", ""),
            "submitted_answer": ans_payload,
            **feedback,
        }

        # Format 1: Multiple Choice & True/False
        if q.question_type in {"multiple_choice", "true_false"}:
            try:
                sub_c_id = int(ans_payload.get("choice_id"))
            except (TypeError, ValueError):
                sub_c_id = None

            sub_choice = next((c for c in q.choices.all() if c.id == sub_c_id), None)
            corr_choice = next((c for c in q.choices.all() if c.is_correct), None)

            review_item.update({
                "submitted_text": sub_choice.text if sub_choice else "No answer provided",
                "submitted_choice_text": sub_choice.text if sub_choice else "",
                "correct_text": corr_choice.text if corr_choice else feedback.get("correct_choice_text", ""),
                "is_correct": result_state == "correct",
            })

        # Format 2: Identification
        elif q.question_type == "identification":
            submitted_text = (ans_payload.get("text") or "").strip()
            answer_data = q.answer_data or {}
            canonical = answer_data.get("canonical_answer") or ""
            alternatives = answer_data.get("alternative_answers") or []

            review_item.update({
                "submitted_text": submitted_text or "No answer provided",
                "canonical_text": canonical,
                "accepted_variants": alternatives,
                "is_correct": result_state == "correct",
            })

        # Format 3: Enumeration
        elif q.question_type == "enumeration":
            raw_items = ans_payload.get("items") or []
            submitted_items = [str(it).strip() for it in raw_items if str(it).strip()]
            expected_items = (q.answer_data or {}).get("expected_items", [])
            canonical_items = [
                it.get("canonical", "") if isinstance(it, dict) else str(it)
                for it in expected_items
            ]

            review_item.update({
                "submitted_items": submitted_items or ["No answer provided"],
                "canonical_items": canonical_items,
                "matched_items": feedback.get("matched_items", []),
                "missing_items": feedback.get("missing_items", []),
                "order_matters": feedback.get("order_matters", False),
            })

        review_items.append(review_item)

    percentage = int(round((total_earned / total_max * 100))) if total_max > 0 else 0
    passed = percentage >= QUIZ_PASS_THRESHOLD

    previous_best_xp = (
        QuizAttempt.objects.filter(user=request.user, quiz=quiz)
        .aggregate(Max("xp_earned"))["xp_earned__max"]
        or 0
    )

    attempt_xp = calculate_quiz_xp(percentage)
    xp_delta = max(attempt_xp - previous_best_xp, 0)

    # Persist QuizAttempt with the complete review data snapshot
    QuizAttempt.objects.create(
        user=request.user,
        quiz=quiz,
        score=float(total_earned),
        total_questions=int(total_max),
        xp_earned=attempt_xp,
        review_data={
            "score": float(total_earned),
            "maximum_score": float(total_max),
            "percentage": percentage,
            "passed": passed,
            "review_items": review_items,
        },
    )

    profile = getattr(request.user, "userprofile", None) or getattr(request.user, "profile", None)
    if profile and xp_delta > 0:
        if hasattr(profile, "award_xp"):
            profile.award_xp(xp_delta, reason=f"Quiz result: {chapter.title}")
        elif hasattr(profile, "add_xp"):
            profile.add_xp(xp_delta)

    if profile and hasattr(profile, "record_study_activity"):
        profile.record_study_activity()

    return JsonResponse({
        "score": float(total_earned),
        "maximum_score": float(total_max),
        "total_questions": int(total_max),
        "percentage": percentage,
        "passed": passed,
        "xp_earned": xp_delta,
        "new_level": getattr(profile, "current_level", 1) if profile else 1,
        "new_streak": getattr(profile, "streak_days", 0) if profile else 0,
        "review_items": review_items,
        "results": review_items,
    })


# --------------------------------------------------
# 6. GAMIFICATION: PROFILE & RANK/TIER LEADERBOARD
# --------------------------------------------------
@login_required
def profile_view(request):
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    level_progress = get_level_progress(profile.total_xp, profile.current_level)
    tier_progress = get_tier_progress(profile.current_level, profile.total_xp)
    completed_courses_count = UserCourseCompletion.objects.filter(user=request.user).count()
    active_courses = get_active_course_progress(request.user, limit=6)
    achievements = get_user_achievements(request.user)

    context = {
        "profile": profile,
        "level_progress": level_progress,
        "tier_progress": tier_progress,
        "completed_courses_count": completed_courses_count,
        "active_courses": active_courses,
        "achievements": achievements,
    }
    return render(request, "courses/profile.html", context)


@login_required
def leaderboard_view(request):
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    tier_progress = get_tier_progress(profile.current_level, profile.total_xp)
    level_progress = get_level_progress(profile.total_xp, profile.current_level)
    weekly_momentum = get_weekly_momentum(request.user)
    standings_data = get_leaderboard_standings(request.user, limit=25)

    context = {
        "profile": profile,
        "tier_progress": tier_progress,
        "level_progress": level_progress,
        "weekly_momentum": weekly_momentum,
        "standings": standings_data["standings"],
        "current_user_rank": standings_data["current_user_rank"],
        "total_learners": standings_data["total_learners"],
    }
    return render(request, "courses/leaderboard.html", context)