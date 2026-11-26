from dataclasses import dataclass
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .models import (
    Course,
    CourseCreditTransaction,
    QuizAttempt,
    UserCourseCompletion,
    UserProfile,
)
from .plan_policies import get_plan_limits

COURSE_GENERATION_COOLDOWN = timedelta(days=7)
FREE_ACTIVE_COURSE_LIMIT = 5
MAX_BONUS_COURSE_CREDITS = 1


@dataclass
class GenerationEligibility:
    allowed: bool
    source: str | None
    reason: str
    available_at: object | None
    active_count: int
    active_limit: int
    weekly_used: int = 0
    weekly_limit: int = 1
    bonus_limit: int = 1


def get_active_course_count(user, plan=None):
    statuses = ("active", "processing")
    return Course.objects.filter(user=user, status__in=statuses).count()


def has_available_course_slot(user, plan="free"):
    return get_active_course_count(user) < get_plan_limits(plan)["active_courses"]


def get_generation_eligibility(profile):
    limits = get_plan_limits(profile.plan)
    active_count = get_active_course_count(profile.user)
    if active_count >= limits["active_courses"]:
        weekly_count = _weekly_generation_count(profile.user)
        return GenerationEligibility(False, None, "active_course_limit", None, active_count, limits["active_courses"], weekly_count, limits["weekly_generations"], limits["bonus_credit_cap"])

    weekly_spend_count = _weekly_generation_count(profile.user)
    if weekly_spend_count == 0 and profile.last_course_generated_at:
        if profile.last_course_generated_at + COURSE_GENERATION_COOLDOWN > timezone.now():
            weekly_spend_count = limits["weekly_generations"]
    if weekly_spend_count < limits["weekly_generations"]:
        return GenerationEligibility(True, "weekly", "first_generation" if weekly_spend_count == 0 else "weekly_credit_available", None, active_count, limits["active_courses"], weekly_spend_count, limits["weekly_generations"], limits["bonus_credit_cap"])
    if profile.bonus_course_credits > 0:
        return GenerationEligibility(True, "bonus", "bonus_credit_available", None, active_count, limits["active_courses"], weekly_spend_count, limits["weekly_generations"], limits["bonus_credit_cap"])

    weekly_spends = CourseCreditTransaction.objects.filter(
        user=profile.user,
        transaction_type="weekly_spend",
    ).order_by("-created_at", "-pk").values_list("created_at", flat=True)
    cutoff = next(iter(weekly_spends[limits["weekly_generations"] - 1:limits["weekly_generations"]]), None)
    last_used = cutoff or profile.last_course_generated_at
    available_at = last_used + COURSE_GENERATION_COOLDOWN if last_used else None
    return GenerationEligibility(False, None, "weekly_cooldown", available_at, active_count, limits["active_courses"], weekly_spend_count, limits["weekly_generations"], limits["bonus_credit_cap"])


def _weekly_generation_count(user):
    window_start = timezone.now() - COURSE_GENERATION_COOLDOWN
    return CourseCreditTransaction.objects.filter(
        user=user,
        transaction_type="weekly_spend",
        created_at__gte=window_start,
    ).count()


@transaction.atomic
def consume_generation_credit(user, course):
    profile = UserProfile.objects.select_for_update().get(user=user)
    eligibility = get_generation_eligibility(profile)
    if not eligibility.allowed:
        raise ValueError(eligibility.reason)

    if eligibility.source == "weekly":
        now = timezone.now()
        profile.last_course_generated_at = now
        profile.save(update_fields=["last_course_generated_at"])
        CourseCreditTransaction.objects.create(
            user=user, transaction_type="weekly_spend", amount=-1,
            related_course=course,
            reference_key=f"weekly-generation:{user.pk}:{course.pk}",
        )
        return "weekly"

    profile.bonus_course_credits -= 1
    profile.save(update_fields=["bonus_course_credits"])
    CourseCreditTransaction.objects.create(
        user=user, transaction_type="bonus_spend", amount=-1,
        related_course=course,
        reference_key=f"bonus-generation:{user.pk}:{course.pk}",
    )
    return "bonus"


def has_completed_course(user, course):
    from .views import get_chapter_completion_state

    chapters = list(course.chapters.all())
    return bool(chapters) and all(
        get_chapter_completion_state(user, chapter)["completed"] for chapter in chapters
    )


@transaction.atomic
def award_course_completion_bonus(user, course):
    if not has_completed_course(user, course):
        return {"course_completed": False, "bonus_awarded": False}

    completion, _ = UserCourseCompletion.objects.select_for_update().get_or_create(user=user, course=course)
    if completion.bonus_credit_awarded:
        return {"course_completed": True, "bonus_awarded": False, "already_rewarded": True}

    profile = UserProfile.objects.select_for_update().get(user=user)
    cap = get_plan_limits(profile.plan)["bonus_credit_cap"]
    if profile.bonus_course_credits >= cap:
        return {"course_completed": True, "bonus_awarded": False, "bonus_balance_full": True}

    profile.bonus_course_credits += 1
    profile.save(update_fields=["bonus_course_credits"])
    completion.bonus_credit_awarded = True
    completion.save(update_fields=["bonus_credit_awarded"])
    CourseCreditTransaction.objects.create(
        user=user, transaction_type="bonus_award", amount=1,
        related_course=course,
        reference_key=f"completion-bonus:{user.pk}:{course.pk}",
    )
    return {"course_completed": True, "bonus_awarded": True, "bonus_credits": profile.bonus_course_credits}


def calculate_delta_quiz_xp(user, quiz, percentage):
    from .views import calculate_quiz_xp

    best = QuizAttempt.objects.filter(user=user, quiz=quiz).order_by("-xp_earned").values_list("xp_earned", flat=True).first() or 0
    return max(calculate_quiz_xp(percentage) - best, 0)
