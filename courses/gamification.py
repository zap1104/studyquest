"""
courses/gamification.py
Centralized gamification logic for StudyQuest:
- Tier rules and level thresholds
- Accurate level & tier progression math
- Real weekly momentum derived from XP transactions and chapter/quiz activity
- Logic-derived academic achievements
- Efficient course progress calculation
- Community leaderboard standings
"""

from datetime import timedelta
from django.db.models import F, Q, Sum
from django.utils import timezone
from .models import (
    ChapterCompletion,
    Course,
    QuizAttempt,
    UserCourseCompletion,
    UserProfile,
    XPTransaction,
)

TIER_RULES = [
    {
        "key": "bronze",
        "name": "Bronze",
        "icon": "🥉",
        "min_level": 1,
        "max_level": 4,
        "color": "#cd7f32",
        "badge_class": "tier-bronze",
        "title": "Foundational Scholar",
        "description": "Levels 1–4 · Getting started with core review journeys",
    },
    {
        "key": "silver",
        "name": "Silver",
        "icon": "🥈",
        "min_level": 5,
        "max_level": 9,
        "color": "#c0c0c0",
        "badge_class": "tier-silver",
        "title": "Disciplined Learner",
        "description": "Levels 5–9 · Consistent chapter progression and quizzes",
    },
    {
        "key": "gold",
        "name": "Gold",
        "icon": "🥇",
        "min_level": 10,
        "max_level": 19,
        "color": "#ffd700",
        "badge_class": "tier-gold",
        "title": "Master Questor",
        "description": "Levels 10–19 · Multi-topic mastery and solid streaks",
    },
    {
        "key": "platinum",
        "name": "Platinum",
        "icon": "💎",
        "min_level": 20,
        "max_level": None,
        "color": "#00c2a8",
        "badge_class": "tier-platinum",
        "title": "Grand Paragon",
        "description": "Level 20+ · Elite knowledge retention and course completion",
    },
]


def get_tier_info(level):
    """
    Return the tier definition for a given level, ensuring a single source of truth.
    Defaults to Bronze if level < 1.
    """
    lvl = max(1, int(level or 1))
    for rule in TIER_RULES:
        if rule["max_level"] is None:
            if lvl >= rule["min_level"]:
                return dict(rule)
        elif rule["min_level"] <= lvl <= rule["max_level"]:
            return dict(rule)
    return dict(TIER_RULES[0])


def get_all_tiers_with_status(current_level):
    """
    Returns all tiers annotated with 'status': 'completed', 'current', or 'locked'.
    """
    lvl = max(1, int(current_level or 1))
    annotated = []
    current_tier = get_tier_info(lvl)

    for rule in TIER_RULES:
        tier_data = dict(rule)
        if rule["key"] == current_tier["key"]:
            tier_data["status"] = "current"
        elif rule["max_level"] is not None and lvl > rule["max_level"]:
            tier_data["status"] = "completed"
        else:
            tier_data["status"] = "locked"
        annotated.append(tier_data)
    return annotated


def get_level_progress(total_xp, current_level):
    """
    Calculate progress towards the next level based on StudyQuest's award_xp logic:
    - Each level spans 100 XP.
    - Level 1: 0..99 XP (floor 0, next threshold 100)
    - Level 2: 100..199 XP (floor 100, next threshold 200)
    - Level N: (N-1)*100 .. N*100-1 (floor (N-1)*100, next threshold N*100)
    """
    lvl = max(1, int(current_level or 1))
    xp = max(0, int(total_xp or 0))

    current_level_floor = (lvl - 1) * 100
    next_level_threshold = lvl * 100

    xp_into_level = max(0, xp - current_level_floor)
    xp_needed_for_next = max(0, next_level_threshold - xp)
    progress_pct = max(0, min(100, int((xp_into_level / 100.0) * 100)))

    return {
        "current_level": lvl,
        "current_level_floor": current_level_floor,
        "next_level": lvl + 1,
        "next_level_threshold": next_level_threshold,
        "xp_into_level": xp_into_level,
        "xp_needed_for_next": xp_needed_for_next,
        "progress_pct": progress_pct,
    }


def get_tier_progress(current_level, total_xp):
    """
    Calculate progress across tiers:
    - Finds current tier and next tier.
    - Computes levels and XP remaining until the next tier threshold.
    """
    lvl = max(1, int(current_level or 1))
    xp = max(0, int(total_xp or 0))

    current_tier = get_tier_info(lvl)
    tiers = TIER_RULES
    current_idx = next((i for i, t in enumerate(tiers) if t["key"] == current_tier["key"]), 0)

    if current_idx + 1 < len(tiers):
        next_tier = tiers[current_idx + 1]
        levels_to_next = max(0, next_tier["min_level"] - lvl)
        target_xp = (next_tier["min_level"] - 1) * 100
        xp_to_next_tier = max(0, target_xp - xp)

        tier_start_xp = (current_tier["min_level"] - 1) * 100
        tier_span = target_xp - tier_start_xp
        if tier_span > 0:
            tier_progress_pct = max(0, min(100, int(((xp - tier_start_xp) / tier_span) * 100)))
        else:
            tier_progress_pct = 100
    else:
        next_tier = None
        levels_to_next = 0
        xp_to_next_tier = 0
        tier_progress_pct = 100

    return {
        "current_tier": current_tier,
        "next_tier": next_tier,
        "levels_to_next": levels_to_next,
        "xp_to_next_tier": xp_to_next_tier,
        "tier_progress_pct": tier_progress_pct,
        "all_tiers": get_all_tiers_with_status(lvl),
    }


def get_weekly_momentum(user):
    """
    Aggregates real user activity for the current week starting Monday 00:00:00.
    Uses existing XPTransaction, QuizAttempt, and ChapterCompletion tables.
    """
    now = timezone.now()
    start_of_week = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    weekly_xp = (
        XPTransaction.objects.filter(user=user, created_at__gte=start_of_week).aggregate(
            total=Sum("amount")
        )["total"]
        or 0
    )

    quizzes_passed = (
        QuizAttempt.objects.filter(user=user, completed_at__gte=start_of_week)
        .filter(
            Q(score__gte=F("total_questions") * 0.75, total_questions__gt=0)
            | Q(review_data__passed=True)
        )
        .count()
    )

    chapters_read = ChapterCompletion.objects.filter(
        user=user, completed_at__gte=start_of_week
    ).count()

    return {
        "weekly_xp": max(0, weekly_xp),
        "quizzes_passed": quizzes_passed,
        "chapters_read": chapters_read,
        "week_start": start_of_week,
    }


def get_user_achievements(user):
    """
    Evaluates 6 core academic achievements derived strictly from existing verified data:
    1. First Course Created
    2. First Chapter Mastered
    3. Quiz Cleared
    4. Flawless Knowledge Check (100% quiz)
    5. Seven-Day Streak
    6. Course Graduate (Completed course)
    """
    profile = UserProfile.objects.filter(user=user).first()
    streak_days = getattr(profile, "streak_days", 0) if profile else 0
    best_streak = getattr(profile, "best_streak_days", 0) if profile else 0

    has_course = Course.objects.filter(user=user).exists()
    has_chapter = ChapterCompletion.objects.filter(user=user).exists()
    has_quiz_passed = (
        QuizAttempt.objects.filter(user=user)
        .filter(
            Q(score__gte=F("total_questions") * 0.75, total_questions__gt=0)
            | Q(review_data__passed=True)
        )
        .exists()
    )
    has_perfect_quiz = (
        QuizAttempt.objects.filter(user=user)
        .filter(
            Q(score__gte=F("total_questions"), total_questions__gt=0)
            | Q(review_data__percentage=100)
        )
        .exists()
    )
    has_7_streak = (streak_days >= 7) or (best_streak >= 7)
    has_course_completed = UserCourseCompletion.objects.filter(user=user).exists()

    achievements = [
        {
            "id": "first_course",
            "name": "First Course Created",
            "description": "Synthesized your first review journey with StudyQuest AI.",
            "icon": "📜",
            "unlocked": has_course,
        },
        {
            "id": "first_chapter",
            "name": "First Chapter Mastered",
            "description": "Marked your first chapter review reading complete.",
            "icon": "📖",
            "unlocked": has_chapter,
        },
        {
            "id": "quiz_cleared",
            "name": "Quiz Cleared",
            "description": "Achieved a passing score (≥ 75%) on any chapter knowledge check.",
            "icon": "⚡",
            "unlocked": has_quiz_passed,
        },
        {
            "id": "flawless_quiz",
            "name": "Flawless Knowledge Check",
            "description": "Scored 100% on a chapter quiz with zero errors.",
            "icon": "💯",
            "unlocked": has_perfect_quiz,
        },
        {
            "id": "seven_day_streak",
            "name": "Seven-Day Streak",
            "description": "Maintained study consistency for at least 7 consecutive days.",
            "icon": "🔥",
            "unlocked": has_7_streak,
        },
        {
            "id": "course_graduate",
            "name": "Course Graduate",
            "description": "Completed all chapters and assessments in a full course.",
            "icon": "🎓",
            "unlocked": has_course_completed,
        },
    ]

    unlocked_count = sum(1 for a in achievements if a["unlocked"])
    return {
        "list": achievements,
        "unlocked_count": unlocked_count,
        "total_count": len(achievements),
    }


def get_achievement_preview(user):
    """
    Returns an actionable milestone preview card for the Dashboard:
    - Identifies the nearest achievable milestone or most recently earned milestone.
    - Provides a concrete instruction string with remaining count.
    - Calculates progress percentage and remaining text.
    """
    profile = UserProfile.objects.filter(user=user).first()
    streak_days = getattr(profile, "streak_days", 0) if profile else 0
    best_streak = getattr(profile, "best_streak_days", 0) if profile else 0
    effective_streak = max(streak_days, best_streak)

    active_courses = list(
        Course.objects.filter(user=user, status="active")
        .prefetch_related("chapters", "chapters__completions")
        .order_by("-updated_at")
    )
    has_course = len(active_courses) > 0 or Course.objects.filter(user=user).exists()
    has_chapter_completed = ChapterCompletion.objects.filter(user=user).exists()
    has_quiz_passed = (
        QuizAttempt.objects.filter(user=user)
        .filter(
            Q(score__gte=F("total_questions") * 0.75, total_questions__gt=0)
            | Q(review_data__passed=True)
        )
        .exists()
    )
    has_course_completed = UserCourseCompletion.objects.filter(user=user).exists()
    has_perfect_quiz = (
        QuizAttempt.objects.filter(user=user)
        .filter(
            Q(score__gte=F("total_questions"), total_questions__gt=0)
            | Q(review_data__percentage=100)
        )
        .exists()
    )
    has_7_streak = effective_streak >= 7

    # 1. First Course
    if not has_course:
        return {
            "key": "first_course",
            "name": "First Course Created",
            "icon": "📜",
            "instruction": "Synthesize your first review journey with StudyQuest AI.",
            "current": 0,
            "target": 1,
            "progress_pct": 0,
            "remaining_text": "1 course remaining",
            "is_earned": False,
            "is_all_unlocked": False,
        }

    # 2. First Chapter Mastered
    if not has_chapter_completed:
        return {
            "key": "first_chapter",
            "name": "First Chapter Mastered",
            "icon": "📖",
            "instruction": "Complete your first chapter review reading.",
            "current": 0,
            "target": 1,
            "progress_pct": 0,
            "remaining_text": "1 chapter remaining",
            "is_earned": False,
            "is_all_unlocked": False,
        }

    # 3. First Quiz Passed
    if not has_quiz_passed:
        return {
            "key": "quiz_cleared",
            "name": "Quiz Cleared",
            "icon": "⚡",
            "instruction": "Score ≥ 75% on any chapter quiz to clear your first test.",
            "current": 0,
            "target": 1,
            "progress_pct": 0,
            "remaining_text": "1 quiz remaining",
            "is_earned": False,
            "is_all_unlocked": False,
        }

    # 4. Course Graduate (in-progress course)
    if not has_course_completed and active_courses:
        best_course = None
        best_total = 0
        best_completed = 0
        for c in active_courses:
            chs = list(c.chapters.all())
            tot = len(chs)
            comp = sum(1 for ch in chs if any(cp.user_id == user.id for cp in ch.completions.all()))
            if tot > 0:
                if best_course is None or (comp / tot) > (best_completed / max(best_total, 1)):
                    best_course = c
                    best_total = tot
                    best_completed = comp

        if best_course and best_total > 0:
            remaining = max(1, best_total - best_completed)
            pct = int((best_completed / best_total) * 100)
            return {
                "key": "course_graduate",
                "name": "Course Graduate",
                "icon": "🎓",
                "instruction": f"Complete {remaining} more chapter{'s' if remaining > 1 else ''} in {best_course.title}.",
                "current": best_completed,
                "target": best_total,
                "progress_pct": pct,
                "remaining_text": f"{remaining} chapter{'s' if remaining > 1 else ''} remaining",
                "is_earned": False,
                "is_all_unlocked": False,
            }

    # 5. Seven-Day Streak
    if not has_7_streak:
        remaining = max(1, 7 - effective_streak)
        pct = int((min(effective_streak, 7) / 7.0) * 100)
        return {
            "key": "seven_day_streak",
            "name": "Seven-Day Streak",
            "icon": "🔥",
            "instruction": f"Study for {remaining} more consecutive day{'s' if remaining > 1 else ''} to build consistency.",
            "current": effective_streak,
            "target": 7,
            "progress_pct": pct,
            "remaining_text": f"{remaining} day{'s' if remaining > 1 else ''} remaining",
            "is_earned": False,
            "is_all_unlocked": False,
        }

    # 6. Flawless Quiz
    if not has_perfect_quiz:
        return {
            "key": "flawless_quiz",
            "name": "Flawless Knowledge Check",
            "icon": "💯",
            "instruction": "Achieve a 100% perfect score on any chapter quiz.",
            "current": 0,
            "target": 1,
            "progress_pct": 0,
            "remaining_text": "1 perfect quiz remaining",
            "is_earned": False,
            "is_all_unlocked": False,
        }

    # All Core Achievements Unlocked
    return {
        "key": "all_unlocked",
        "name": "Master Scholar",
        "icon": "🏆",
        "instruction": "All core achievements unlocked! You have mastered the curriculum.",
        "current": 6,
        "target": 6,
        "progress_pct": 100,
        "remaining_text": "All Mastered",
        "is_earned": True,
        "is_all_unlocked": True,
    }


def get_active_course_progress(user, limit=5):
    """
    Returns active courses with chapter counts and completion percentage,
    optimized to avoid N+1 queries.
    """
    courses = list(
        Course.objects.filter(user=user, status="active")
        .prefetch_related("chapters", "chapters__completions")
        .order_by("-updated_at")[:limit]
    )

    progress_list = []
    for c in courses:
        chapters = list(c.chapters.all())
        total = len(chapters)
        if total == 0:
            completed = 0
            pct = 0
        else:
            completed = sum(
                1 for ch in chapters if any(comp.user_id == user.id for comp in ch.completions.all())
            )
            pct = int((completed / total) * 100)

        progress_list.append({
            "course": c,
            "total_chapters": total,
            "completed_chapters": completed,
            "progress_pct": pct,
        })

    return progress_list


def get_leaderboard_standings(current_user, limit=25):
    """
    Returns authentic rankings of registered learners sorted by total_xp.
    Includes rank, user details, tier, level, and is_current_user flag.
    Also returns the current user's exact rank.
    """
    profiles = list(
        UserProfile.objects.select_related("user")
        .order_by("-total_xp", "-current_level", "id")[:limit]
    )

    standings = []
    current_user_found = False
    current_user_rank = None

    for idx, prof in enumerate(profiles, start=1):
        is_me = (prof.user_id == current_user.id)
        if is_me:
            current_user_found = True
            current_user_rank = idx

        standings.append({
            "rank": idx,
            "user": prof.user,
            "username": prof.user.username,
            "avatar_initial": prof.user.username[:1].upper() if prof.user.username else "?",
            "total_xp": prof.total_xp,
            "level": prof.current_level,
            "tier": get_tier_info(prof.current_level),
            "is_current_user": is_me,
        })

    if not current_user_found:
        my_profile, _ = UserProfile.objects.get_or_create(user=current_user)
        higher_count = UserProfile.objects.filter(
            Q(total_xp__gt=my_profile.total_xp)
            | Q(total_xp=my_profile.total_xp, current_level__gt=my_profile.current_level)
            | Q(total_xp=my_profile.total_xp, current_level=my_profile.current_level, id__lt=my_profile.id)
        ).count()
        current_user_rank = higher_count + 1

    return {
        "standings": standings,
        "current_user_rank": current_user_rank,
        "total_learners": UserProfile.objects.count(),
    }
