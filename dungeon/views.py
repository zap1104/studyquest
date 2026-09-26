"""Thin HTTP layer over :mod:`dungeon.services`.

Every view here does the same three things: prove the requesting user owns the
thing being touched, hand off to a service function, and render or serialize
the result. No game rule is decided in this module.

Ownership is always enforced with a 404 rather than a 403 - telling someone
their neighbour's quiz exists but is off-limits is itself a leak.
"""

import json

from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.templatetags.static import static
from django.urls import reverse
from django.views.decorators.http import require_POST

from . import services
from .models import DungeonRun

# How many finished runs the launch screen lists.
RECENT_RUN_COUNT = 5


def _get_run_or_404(request, pk):
    return get_object_or_404(
        DungeonRun.objects.select_related("quiz__chapter__course").prefetch_related("enemies"),
        pk=pk,
        user=request.user,
    )


def _payload(request):
    """The JSON body as a dict, or None for anything else (bad JSON, a list...)."""
    try:
        payload = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _service_call(handler):
    """Run a service call, turning a rule rejection into a 400 with its message."""
    try:
        return JsonResponse({"ok": True, **(handler() or {})})
    except services.DungeonError as error:
        return JsonResponse({"ok": False, "error": str(error)}, status=400)


# --------------------------------------------------
# PAGES
# --------------------------------------------------
def _launch_context(request, launch_error=None):
    """Everything the launch screen renders, including the rules this player
    will actually play under - the template never states a number itself."""
    active_runs = (
        DungeonRun.objects.filter(
            user=request.user, status=DungeonRun.STATUS_IN_PROGRESS
        ).select_related("quiz__chapter__course", "active_enemy").prefetch_related("enemies")
    )
    recent_groups = services.get_recent_chapter_groups(request.user)
    active_focus = None
    try:
        from courses.learning_focus_service import get_active_learning_focus
        active_focus = get_active_learning_focus(request.user)
    except Exception:
        pass

    return {
        "catalog": services.build_launch_catalog(request.user, active_focus=active_focus),
        "rules": services.rules_for_user(request.user),
        "active_runs": active_runs,
        "recent_groups": recent_groups[:3],
        "all_recent_groups": recent_groups,
        "has_more_history": len(recent_groups) > 3,
        "has_prior_runs": bool(recent_groups or active_runs.exists()),
        "recent_runs": (
            DungeonRun.objects.filter(user=request.user)
            .exclude(status=DungeonRun.STATUS_IN_PROGRESS)
            .select_related("quiz__chapter__course")[:RECENT_RUN_COUNT]
        ),
        "launch_error": launch_error,
        "active_focus": active_focus,
    }


@login_required
def launch(request):
    return render(request, "dungeon/launch.html", _launch_context(request))


@login_required
@require_POST
def start_run(request):
    quiz = services.get_owned_quiz(request.user, request.POST.get("quiz_id"))
    if quiz is None:
        raise Http404("Quiz not found.")

    run_type = request.POST.get("run_type") or DungeonRun.TYPE_CLASSIC

    try:
        run = services.start_or_resume_run(request.user, quiz, run_type=run_type)
    except services.LaunchBlocked as error:
        return render(
            request, "dungeon/launch.html", _launch_context(request, str(error)), status=400
        )

    return redirect("dungeon:room", pk=run.pk)


@login_required
def room(request, pk):
    run = _get_run_or_404(request, pk)
    if not run.is_active:
        return redirect("dungeon:summary", pk=run.pk)

    display = services.display_settings()
    return render(request, "dungeon/room.html", {
        "run": run,
        "presentation": display,
        "tile_size": display["tile_size"],
        "icon_size": display["icon_size"],
        "ui_scale": display["ui_scale"],
        # One JSON payload for the client: the run state plus every endpoint it
        # may call. Rendered through json_script, so it is escaped, not inlined.
        "bootstrap": {
            "state": services.serialize_run(run),
            "urls": {
                "sprites": static("courses/dungeon/sprites.json"),
                "state": reverse("dungeon:state", args=[run.pk]),
                "move": reverse("dungeon:move", args=[run.pk]),
                "answer": reverse("dungeon:answer", args=[run.pk]),
                "use_item": reverse("dungeon:use_item", args=[run.pk]),
                "exit_room": reverse("dungeon:exit_room", args=[run.pk]),
                "summary": reverse("dungeon:summary", args=[run.pk]),
            },
        },
    })


@login_required
def summary(request, pk):
    run = _get_run_or_404(request, pk)
    if run.is_active:
        return redirect("dungeon:room", pk=run.pk)

    enemies = list(run.enemies.all())
    targeted = run.targeted_count
    mastered = run.mastered_count
    verdict_code = services.get_run_verdict_code(run)

    unresolved_questions = services.get_unresolved_review_questions(request.user, run.quiz)
    unresolved_count = len(unresolved_questions)

    active_run = DungeonRun.objects.filter(
        user=request.user,
        quiz=run.quiz,
        status=DungeonRun.STATUS_IN_PROGRESS,
    ).exclude(pk=run.pk).select_related("quiz__chapter").first()

    other_active_run = active_run
    if other_active_run is None:
        other_active_run = DungeonRun.objects.filter(
            user=request.user,
            status=DungeonRun.STATUS_IN_PROGRESS,
        ).exclude(pk=run.pk).select_related("quiz__chapter").first()

    return render(request, "dungeon/summary.html", {
        "run": run,
        "verdict_code": verdict_code,
        "is_review_run": run.is_review_run,
        "is_mastered": run.is_mastered,
        "targeted_count": targeted,
        "mastered_count": mastered,
        "still_to_review_count": max(targeted - mastered, 0),
        "unresolved_count": unresolved_count,
        "active_run": active_run,
        "other_active_run": other_active_run,
        "active_run_url": reverse("dungeon:room", args=[other_active_run.pk]) if other_active_run else None,
        "review_action_blocked": active_run is not None,
        "enemies_defeated": sum(1 for enemy in enemies if enemy.is_defeated),
        "enemy_count": len(enemies),
        "questions_answered": sum(len(e.answered_question_ids or []) for e in enemies),
        "inventory": services.serialize_inventory(run),
        "relaunch_url": reverse("dungeon:launch"),
        "chapter_review_url": reverse("courses:chapter_review", args=[run.quiz.chapter.pk]),
        "course_url": reverse("courses:course_detail", args=[run.quiz.chapter.course.pk]),
    })


# --------------------------------------------------
# RUN API
# --------------------------------------------------
@login_required
def state(request, pk):
    run = _get_run_or_404(request, pk)
    return JsonResponse({"ok": True, "state": services.serialize_run(run)})


@login_required
@require_POST
def move(request, pk):
    run = _get_run_or_404(request, pk)
    payload = _payload(request)
    if payload is None:
        return JsonResponse({"ok": False, "error": "Invalid JSON."}, status=400)

    return _service_call(
        lambda: {"result": services.move_player(run, payload.get("direction"))}
    )


@login_required
@require_POST
def answer(request, pk):
    run = _get_run_or_404(request, pk)
    payload = _payload(request)
    if payload is None:
        return JsonResponse({"ok": False, "error": "Invalid JSON."}, status=400)

    return _service_call(lambda: {
        "result": services.answer_question(
            run, payload.get("question_id"), payload.get("answer", {})
        )
    })


@login_required
@require_POST
def use_item(request, pk):
    run = _get_run_or_404(request, pk)
    payload = _payload(request)
    if payload is None:
        return JsonResponse({"ok": False, "error": "Invalid JSON."}, status=400)

    return _service_call(
        lambda: {"result": services.use_item(run, payload.get("item"))}
    )


@login_required
@require_POST
def exit_room(request, pk):
    run = _get_run_or_404(request, pk)
    return _service_call(lambda: {
        "result": services.attempt_exit(run),
        "summary_url": reverse("dungeon:summary", args=[run.pk]),
    })


@login_required
@require_POST
def abandon(request, pk):
    run = _get_run_or_404(request, pk)
    services.abandon_run(run)
    return redirect("dungeon:summary", pk=run.pk)
