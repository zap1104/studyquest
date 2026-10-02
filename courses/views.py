import json
import logging
import re
import unicodedata
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.core.cache import cache
from django.db import transaction
from django.db.models import Max
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .grading import _grade_question, normalize_text_answer
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
    LearningFocus,
    Question,
    Quiz,
    QuizAttempt,
    UserCourseCompletion,
    UserProfile,
)
from .learning_focus_service import (
    validate_external_assessment_payload,
    import_external_assessment,
    create_manual_learning_focus,
    activate_learning_focus,
    dismiss_learning_focus,
    complete_learning_focus,
    link_focus_to_course,
    get_active_learning_focus,
    get_pending_learning_focus,
    get_completed_learning_focus,
    find_matching_courses,
    generate_course_creation_prefill,
    annotate_course_chapters_with_focus,
    calculate_focus_mastery,
    get_focus_remediation_recommendations,
    get_academic_catalogue,
    DiagnosticPayloadError,
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
GUIDED_PASS_THRESHOLD = 75
PRIOR_KNOWLEDGE_THRESHOLD = 90
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
    A chapter can be completed via two distinct routes:
    1. Guided Learning Route: Lesson reading is marked complete AND the quiz
       is passed (score >= 75%). If no quiz exists, reading alone completes it.
    2. Prior Knowledge Route: The learner tests out by scoring >= 90% on the quiz,
       even without completing the chapter reading.
    """
    lesson_completed = ChapterCompletion.objects.filter(user=user, chapter=chapter).exists()
    best_quiz_percentage = get_best_quiz_percentage(user, chapter)
    quiz = getattr(chapter, "quiz", None)
    has_quiz = quiz is not None

    quiz_passed = (
        best_quiz_percentage is not None
        and best_quiz_percentage >= GUIDED_PASS_THRESHOLD
    )

    prior_knowledge_passed = (
        best_quiz_percentage is not None
        and best_quiz_percentage >= PRIOR_KNOWLEDGE_THRESHOLD
    )

    tested_out = prior_knowledge_passed and not lesson_completed

    if has_quiz:
        completed = (lesson_completed and quiz_passed) or prior_knowledge_passed
    else:
        completed = lesson_completed

    if completed:
        if lesson_completed and (not has_quiz or quiz_passed):
            unlock_route = "guided"
        else:
            unlock_route = "prior_knowledge"
    else:
        unlock_route = None

    return {
        "lesson_completed": lesson_completed,
        "has_quiz": has_quiz,
        "best_quiz_percentage": best_quiz_percentage,
        "quiz_passed": quiz_passed,
        "prior_knowledge_passed": prior_knowledge_passed,
        "tested_out": tested_out,
        "completed": completed,
        "unlock_route": unlock_route,
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

def _client_ip(request):
    """Authoritative client IP for rate-limiting.

    Defaults strictly to REMOTE_ADDR. Forwarded headers (e.g. HTTP_X_FORWARDED_FOR)
    are untrusted and ignored unless TRUST_PROXY_HEADERS is explicitly True AND
    REMOTE_ADDR matches a configured trusted proxy in TRUSTED_PROXY_IPS.
    """
    remote_addr = request.META.get("REMOTE_ADDR", "").strip() or "unknown"

    trust_proxy = getattr(settings, "TRUST_PROXY_HEADERS", False)
    trusted_proxies = getattr(settings, "TRUSTED_PROXY_IPS", None) or []
    if isinstance(trusted_proxies, str):
        trusted_proxies = [ip.strip() for ip in trusted_proxies.split(",") if ip.strip()]

    if trust_proxy and remote_addr in trusted_proxies:
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded:
            client = forwarded.split(",")[0].strip()
            if client:
                return client

    return remote_addr


def _throttle_key(scope, request):
    return f"sq-throttle:{scope}:{_client_ip(request)}"


def _is_throttled(scope, request):
    """True once the caller has exceeded the allowed attempt count."""
    limit = getattr(settings, "LOGIN_THROTTLE_MAX_ATTEMPTS", 10)
    attempts = cache.get(_throttle_key(scope, request), 0)
    return attempts >= limit


def _register_attempt(scope, request):
    """Count one failed attempt, with a rolling expiry window."""
    window = getattr(settings, "LOGIN_THROTTLE_WINDOW_SECONDS", 300)
    key = _throttle_key(scope, request)
    try:
        attempts = cache.incr(key)
    except ValueError:
        cache.set(key, 1, window)
        attempts = 1
    return attempts


def _clear_attempts(scope, request):
    cache.delete(_throttle_key(scope, request))


def auth_portal(request):
    if request.user.is_authenticated:
        return redirect("courses:dashboard")

    active_tab = request.GET.get("tab", "login")
    login_form = AuthenticationForm()
    signup_form = UserCreationForm()

    if request.method == "POST":
        action = request.POST.get("action")
        scope = "login" if action == "login" else "signup"

        # Unauthenticated brute-force protection. The plan-based course
        # generation quota is a separate concern and is untouched by this.
        if _is_throttled(scope, request):
            messages.error(
                request,
                "Too many attempts from this device. "
                "Please wait a few minutes and try again.",
            )
            return render(request, "courses/login.html", {
                "login_form": login_form,
                "signup_form": signup_form,
                "active_tab": scope,
            }, status=429)

        if action == "login":
            login_form = AuthenticationForm(request, data=request.POST)
            if login_form.is_valid():
                _clear_attempts(scope, request)
                login(request, login_form.get_user())
                return redirect("courses:dashboard")
            _register_attempt(scope, request)
            active_tab = "login"
        elif action == "signup":
            signup_form = UserCreationForm(request.POST)
            if signup_form.is_valid():
                _clear_attempts(scope, request)
                user = signup_form.save()
                login(request, user)
                return redirect("courses:dashboard")
            _register_attempt(scope, request)
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

    owned_course_count = Course.objects.filter(user=request.user).count()
    has_courses = owned_course_count > 0
    active_focus = get_active_learning_focus(request.user)
    pending_focus = get_pending_learning_focus(request.user)
    completed_focus = get_completed_learning_focus(request.user) if not active_focus and not pending_focus else None

    # Read the catalogue once; reuse it for the embedded JSON and the template.
    academic_catalogue = get_academic_catalogue()

    return render(request, "courses/dashboard.html", {
        "profile": profile,
        "tier_info": tier_info,
        "streak_status": streak_status,
        "next_action": next_action,
        "recent_courses": recent_courses,
        "courses": recent_courses,
        "owned_course_count": owned_course_count,
        "has_courses": has_courses,
        "weekly_momentum": weekly_momentum,
        "achievement_preview": achievement_preview,
        "eligibility": eligibility,
        "active_focus": active_focus,
        "pending_focus": pending_focus,
        "completed_focus": completed_focus,
        "open_focus": request.GET.get("open_focus") == "1",
        "academic_catalogue": academic_catalogue,
        "academic_catalogue_json": json.dumps(academic_catalogue),
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

    focus_id = request.GET.get("focus_id") or request.POST.get("focus_id")
    learning_focus = None
    initial_title = ""
    initial_focus = ""

    if focus_id:
        learning_focus = LearningFocus.objects.filter(pk=focus_id, user=request.user).first()
        if learning_focus:
            prefill = generate_course_creation_prefill(learning_focus)
            initial_title = prefill.get("title", "")
            initial_focus = prefill.get("review_emphasis", "")[:100]

    if request.method == "GET":
        return render(request, "courses/course_form.html", {
            "eligibility": eligibility,
            "profile": profile,
            "custom_title": initial_title,
            "study_focus": initial_focus,
            "focus_id": focus_id,
            "learning_focus": learning_focus,
        })

    if request.method == "POST":
        if not eligibility.allowed:
            return render(
                request,
                "courses/course_form.html",
                {"eligibility": eligibility, "profile": profile, "focus_id": focus_id, "learning_focus": learning_focus},
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
                "focus_id": focus_id,
                "learning_focus": learning_focus,
            })

        if len(content_files) > 3:
            return render(request, "courses/course_form.html", {
                "eligibility": eligibility,
                "profile": profile,
                "generation_failed": True,
                "generation_error": "You can upload a maximum of 3 files per course.",
                "custom_title": custom_title,
                "study_focus": study_focus,
                "focus_id": focus_id,
                "learning_focus": learning_focus,
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
                "focus_id": focus_id,
                "learning_focus": learning_focus,
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
                if focus_id:
                    target_focus = LearningFocus.objects.filter(pk=focus_id, user=request.user).first()
                    if target_focus:
                        link_focus_to_course(target_focus, course)
                        activate_learning_focus(target_focus)

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
                "focus_id": focus_id,
                "learning_focus": learning_focus,
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
                "focus_id": focus_id,
                "learning_focus": learning_focus,
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
                "focus_id": focus_id,
                "learning_focus": learning_focus,
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
        chapter.prior_knowledge_passed = completion_state["prior_knowledge_passed"]
        chapter.tested_out = completion_state["tested_out"]
        chapter.unlock_route = completion_state["unlock_route"]
        chapter.completed = completion_state["completed"]
        chapter.best_quiz_percentage = completion_state["best_quiz_percentage"]
        chapter.previous_chapter = get_previous_chapter(chapter)

    progress = calculate_course_progress(request.user, course)
    linked_focus = course.learning_focuses.filter(
        status__in=[LearningFocus.STATUS_ACTIVE, LearningFocus.STATUS_PENDING, LearningFocus.STATUS_COMPLETED]
    ).first()
    active_focus = get_active_learning_focus(request.user)
    current_focus = linked_focus or (active_focus if active_focus and active_focus.linked_course_id == course.id else None)

    annotate_course_chapters_with_focus(chapters, current_focus)
    focus_mastery = calculate_focus_mastery(current_focus) if current_focus else None
    recommendation = get_focus_remediation_recommendations(request.user, current_focus) if current_focus else None

    first_focus_chapter = next((c for c in chapters if getattr(c, "is_focus_target", False)), chapters[0] if chapters else None)
    focus_target_quizzes = [c.quiz for c in chapters if getattr(c, "is_focus_target", False) and getattr(c, "quiz", None)]
    focus_question_count = sum(q.questions.count() for q in focus_target_quizzes) or (len(focus_target_quizzes) * 5) or 10

    return render(request, "courses/course_detail.html", {
        "course": course,
        "chapters": chapters,
        "course_progress_pct": progress["percentage"],
        "completed_count": progress["completed_count"],
        "total_chapters": progress["total_chapters"],
        "guided_pass_threshold": GUIDED_PASS_THRESHOLD,
        "prior_knowledge_threshold": PRIOR_KNOWLEDGE_THRESHOLD,
        "pass_threshold": GUIDED_PASS_THRESHOLD,
        "linked_focus": current_focus,
        "focus_mastery": focus_mastery,
        "recommendation": recommendation,
        "first_focus_chapter": first_focus_chapter,
        "focus_question_count": focus_question_count,
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
            "guided_threshold": GUIDED_PASS_THRESHOLD,
            "prior_knowledge_threshold": PRIOR_KNOWLEDGE_THRESHOLD,
            "pass_threshold": GUIDED_PASS_THRESHOLD,
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

    active_focus = get_active_learning_focus(request.user)
    focus_topics = []
    if active_focus and (active_focus.linked_course_id == course.id or active_focus.subject_name.lower() in course.title.lower()):
        c_text = f"{chapter.title} {chapter.review_content or ''}".lower()
        focus_topics = [t for t in (active_focus.topic_names or []) if t.lower() in c_text]

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
        "prior_knowledge_passed": completion_state["prior_knowledge_passed"],
        "tested_out": completion_state["tested_out"],
        "unlock_route": completion_state["unlock_route"],
        "best_quiz_percentage": completion_state["best_quiz_percentage"],
        "has_read_to_end": has_read_to_end(request, chapter),
        "total_chapters": progress["total_chapters"],
        "completed_count": progress["completed_count"],
        "course_progress_pct": progress["percentage"],
        "guided_threshold": GUIDED_PASS_THRESHOLD,
        "prior_knowledge_threshold": PRIOR_KNOWLEDGE_THRESHOLD,
        "pass_threshold": GUIDED_PASS_THRESHOLD,
        "active_focus": active_focus,
        "focus_topics": focus_topics,
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
            "course": chapter.course,
            "guided_threshold": GUIDED_PASS_THRESHOLD,
            "prior_knowledge_threshold": PRIOR_KNOWLEDGE_THRESHOLD,
            "pass_threshold": GUIDED_PASS_THRESHOLD,
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
    status_label = (
        "correct"
        if earned_points >= maximum_points and maximum_points > 0
        else ("partial" if earned_points > 0 else "incorrect")
    )

    return JsonResponse({
        "question_id": question.id,
        "question_type": question.question_type,
        "earned_points": earned_points,
        "points_awarded": earned_points,
        "maximum_points": maximum_points,
        "status": status_label,
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

    post_completion_state = get_chapter_completion_state(request.user, chapter)

    # Check focus mastery if linked
    active_focus = get_active_learning_focus(request.user)
    focus_update = None
    if active_focus and (active_focus.linked_course_id == chapter.course_id or active_focus.subject_name.lower() in chapter.course.title.lower()):
        mastery = calculate_focus_mastery(active_focus)
        focus_update = {
            "focus_id": active_focus.id,
            "subject_name": active_focus.subject_name,
            "is_mastered": mastery["is_mastered"],
            "mastered_chapters": mastery["mastered_chapters"],
            "total_targets": mastery["total_targets"],
            "average_score": mastery["average_score"],
            "diagnostic_baseline": active_focus.initial_score,
            "gain": mastery["gain"],
        }
        if mastery["is_mastered"]:
            complete_learning_focus(active_focus)

    return JsonResponse({
        "score": float(total_earned),
        "maximum_score": float(total_max),
        "total_questions": int(total_max),
        "percentage": percentage,
        "passed": passed,
        "guided_passed": percentage >= GUIDED_PASS_THRESHOLD,
        "prior_knowledge_passed": percentage >= PRIOR_KNOWLEDGE_THRESHOLD,
        "chapter_completed": post_completion_state["completed"],
        "unlock_route": post_completion_state["unlock_route"],
        "xp_earned": xp_delta,
        "new_level": getattr(profile, "current_level", 1) if profile else 1,
        "new_streak": getattr(profile, "streak_days", 0) if profile else 0,
        "review_items": review_items,
        "results": review_items,
        "focus_update": focus_update,
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


# --------------------------------------------------
# 7. DIAGNOSTIC FOCUS & EXTERNAL ASSESSMENT INTEGRATION
# --------------------------------------------------
@login_required
def focus_checkin(request):
    """Handles manual focus creation or JSON upload/paste from an external diagnostic system."""
    if request.method == "POST":
        assessment_file = request.FILES.get("assessment_file")
        raw_json = request.POST.get("assessment_json", "").strip()

        # 1. JSON Import Pathway
        if assessment_file or raw_json:
            try:
                if assessment_file:
                    payload = json.loads(assessment_file.read().decode("utf-8"))
                else:
                    payload = json.loads(raw_json)

                focus, created, msg = import_external_assessment(request.user, payload)
                messages.success(request, msg)
                return redirect("courses:focus_recommendation", pk=focus.pk)
            except json.JSONDecodeError:
                messages.error(request, "Invalid JSON file or text. Please check the format.")
                return redirect("courses:dashboard")
            except DiagnosticPayloadError as e:
                messages.error(request, f"Assessment format error: {e}")
                return redirect("courses:dashboard")
            except Exception as e:
                logger.error(f"Error importing diagnostic assessment: {e}")
                messages.error(request, "Could not import assessment result.")
                return redirect("courses:dashboard")

        # 2. Manual Selection Pathway
        subject_name = request.POST.get("subject_name", "").strip()
        if subject_name:
            raw_topics = request.POST.get("topic_names", "")
            topic_names = [t.strip() for t in raw_topics.split(",") if t.strip()]
            subject_code = request.POST.get("subject_code", "").strip()
            reason = request.POST.get("reason", "").strip()

            focus = create_manual_learning_focus(
                user=request.user,
                subject_name=subject_name,
                topic_names=topic_names,
                subject_code=subject_code,
                reason=reason,
                activate=True,
            )
            messages.success(request, f"Study focus set to {focus.subject_name}!")
            return redirect("courses:focus_recommendation", pk=focus.pk)

        messages.warning(request, "Please enter a subject name or upload an assessment file.")
        return redirect("courses:dashboard")

    return redirect("/?open_focus=1")


@login_required
def focus_recommendation(request, pk):
    """Presents the approved/recommended study focus, showing matching courses and creation options."""
    focus = get_object_or_404(LearningFocus, pk=pk, user=request.user)
    matching_courses = find_matching_courses(request.user, focus)

    return render(request, "courses/focus_recommendation.html", {
        "focus": focus,
        "matching_courses": matching_courses,
        "prefill": generate_course_creation_prefill(focus),
    })


@login_required
@require_POST
def focus_activate(request, pk):
    """Approves and activates a pending or inactive LearningFocus."""
    focus = get_object_or_404(LearningFocus, pk=pk, user=request.user)
    activate_learning_focus(focus)
    messages.success(request, f"Activated study focus for {focus.subject_name}!")
    return redirect("courses:focus_recommendation", pk=focus.pk)


@login_required
@require_POST
def focus_dismiss(request, pk):
    """Dismisses a LearningFocus so it stops appearing on the dashboard."""
    focus = get_object_or_404(LearningFocus, pk=pk, user=request.user)
    dismiss_learning_focus(focus)
    messages.info(request, "Study focus dismissed.")
    return redirect("courses:dashboard")


@login_required
@require_POST
def focus_link_course(request, pk, course_id):
    """Links a LearningFocus to an existing course."""
    focus = get_object_or_404(LearningFocus, pk=pk, user=request.user)
    course = get_object_or_404(Course, pk=course_id, user=request.user)
    link_focus_to_course(focus, course)
    activate_learning_focus(focus)
    messages.success(request, f"Connected focus '{focus.subject_name}' to course '{course.title}'!")
    return redirect("courses:course_detail", pk=course.pk)


@login_required
def focus_summary(request, pk):
    """Displays the diagnostic-to-mastery institutional summary report."""
    focus = get_object_or_404(LearningFocus, pk=pk, user=request.user)
    mastery = calculate_focus_mastery(focus)
    recommendation = get_focus_remediation_recommendations(request.user, focus)
    return render(request, "courses/focus_summary.html", {
        "focus": focus,
        "mastery": mastery,
        "recommendation": recommendation,
    })


@login_required
@require_POST
def focus_complete(request, pk):
    """Manually marks a focus as completed."""
    focus = get_object_or_404(LearningFocus, pk=pk, user=request.user)
    complete_learning_focus(focus)
    messages.success(request, f"Marked study focus '{focus.subject_name}' as completed!")
    return redirect("courses:focus_summary", pk=focus.pk)


@login_required
@require_POST
def focus_reopen(request, pk):
    """Reopens a completed focus."""
    focus = get_object_or_404(LearningFocus, pk=pk, user=request.user)
    activate_learning_focus(focus)
    messages.success(request, f"Reopened study focus '{focus.subject_name}'!")
    return redirect("courses:focus_summary", pk=focus.pk)


@login_required
def focus_catalogue_api(request):
    """Returns the curated Philippine higher education academic terminology catalogue."""
    return JsonResponse(get_academic_catalogue())