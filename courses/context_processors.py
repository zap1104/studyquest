"""
courses/context_processors.py
Supplies topbar_stats to all templates extending base.html.
Provides accurate tier, level progression, 7-day streak calendar,
weekly XP momentum, and recent XP transactions.
"""

from datetime import timedelta
from django.db.models import Sum
from django.utils import timezone
from .models import XPTransaction
from .gamification import get_tier_info, get_level_progress
from .dashboard_service import get_streak_status


def topbar_stats(request):
    """
    Context processor returning structured stats for the persistent topbar.
    Returns None if user is unauthenticated or has no profile.
    """
    if not request.user.is_authenticated:
        return {"topbar_stats": None}

    profile = getattr(request.user, "userprofile", None)
    if not profile:
        return {"topbar_stats": None}

    now = timezone.now()
    today = now.date()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)

    # 1. Level & Tier math (zero DB queries)
    tier_info = get_tier_info(profile.current_level)
    level_progress = get_level_progress(profile.total_xp, profile.current_level)

    # 2. Streak status & 7-day rolling Mon-Sun week calendar
    streak_status = get_streak_status(profile)
    streak_days = profile.streak_days or 0
    best_streak = profile.best_streak_days or streak_days
    studied_today = (profile.last_study_date == today)

    # Contiguous streak starting date
    if studied_today and streak_days > 0:
        streak_start = today - timedelta(days=streak_days - 1)
    elif not studied_today and streak_days > 0:
        streak_start = (today - timedelta(days=1)) - timedelta(days=streak_days - 1)
    else:
        streak_start = None

    # Fetch active dates for this week from XP transactions (single fast query)
    active_dates = set(
        XPTransaction.objects.filter(
            user=request.user,
            created_at__date__gte=monday,
            created_at__date__lte=sunday,
        ).values_list("created_at__date", flat=True)
    )

    streak_week = []
    day_labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    for idx in range(7):
        day_date = monday + timedelta(days=idx)
        is_today = (day_date == today)
        if day_date > today:
            status = "upcoming"
            icon = ""
        elif is_today:
            status = "today_done" if studied_today else "today_pending"
            icon = "🔥"
        else:
            # Past day
            if (streak_start and streak_start <= day_date) or (day_date in active_dates):
                status = "done"
                icon = "✓"
            else:
                status = "inactive"
                icon = ""

        streak_week.append({
            "label": day_labels[idx],
            "day_number": day_date.day,
            "date": day_date,
            "is_today": is_today,
            "status": status,
            "icon": icon,
        })

    # 3. Weekly XP & Recent XP Awards (1 fast query)
    start_of_week_dt = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    weekly_xp = (
        XPTransaction.objects.filter(user=request.user, created_at__gte=start_of_week_dt)
        .aggregate(total=Sum("amount"))["total"]
        or 0
    )

    recent_xp = list(
        XPTransaction.objects.filter(user=request.user)
        .order_by("-created_at")[:3]
    )

    return {
        "topbar_stats": {
            "tier": tier_info,
            "level_progress": level_progress,
            "streak_status": streak_status,
            "streak_days": streak_days,
            "best_streak_days": best_streak,
            "streak_week": streak_week,
            "weekly_xp": max(0, weekly_xp),
            "recent_xp": recent_xp,
            "plan": profile.plan,
        }
    }

