"""Dungeon Quest run lifecycle: start, move, fight, loot, finish.

Views stay thin - every rule lives here, and every number this module uses
comes from :mod:`dungeon.combat_config`.

Two invariants hold throughout:

* **The server decides.** Movement, encounters, grading, damage, drops and the
  exit gate are all resolved here from persisted state. The client renders what
  it is told and nothing more.
* **Answers never leave.** Nothing this module serializes for the browser
  contains ``Choice.is_correct`` or ``Question.answer_data``. Correct answers
  appear only in the response to an already-graded submission.
"""

from random import Random

from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from courses.grading import _grade_question
from courses.models import Course, Question, Quiz, QuizAttempt

from . import rooms
from .combat_config import (
    BASE_DAMAGE,
    BOARD_VERTICAL_RESERVE,
    COMBO_DAMAGE,
    COMBO_RESETS_AFTER_STRIKE,
    COMBO_RESETS_ON_NEW_ENEMY,
    COMBO_RESETS_ON_PARTIAL,
    COMBO_RESETS_ON_SKIP,
    COMBO_RESETS_ON_WRONG,
    COMBO_THRESHOLD,
    DAMAGE_PER_CORRECT_ANSWER,
    FORCED_ENCOUNTER_ON_ENEMY_TILE,
    ICON_SIZE,
    ITEM_HEALTH_POTION,
    ITEM_NOTHING,
    ITEM_SKIP_POTION,
    KEY_PIECES_PER_ENEMY,
    MAX_BOARD_SCALE,
    MIN_BOARD_SCALE,
    PARTIAL_ANSWER_DAMAGES_ENEMY,
    PARTIAL_ANSWER_DAMAGES_PLAYER,
    TILE_SIZE,
    UI_SCALE,
    XP_REASON_REVIEW_TEMPLATE,
    XP_REASON_TEMPLATE,
    calculate_review_run_xp,
    calculate_run_xp,
    enemy_count_for_questions,
    minimum_questions_required,
    resolve_combat_rules,
    review_enemy_specs,
    roll_item_drop,
)
from .models import DungeonEnemy, DungeonInventory, DungeonRun

DIRECTIONS = {
    "up": (0, -1),
    "down": (0, 1),
    "left": (-1, 0),
    "right": (1, 0),
}

OUTCOME_CORRECT = "correct"
OUTCOME_PARTIAL = "partial"
OUTCOME_INCORRECT = "incorrect"
OUTCOME_SKIPPED = "skipped"

POTION_FIELDS = {
    ITEM_SKIP_POTION: "skip_potions",
    ITEM_HEALTH_POTION: "health_potions",
}

SEALED_DOOR_MESSAGE = "The door is sealed. Defeat every enemy to assemble the key."

OBJECTIVE_RUN_FAILED = "run_failed"
OBJECTIVE_RUN_COMPLETE = "run_complete"
OBJECTIVE_DEFEAT_ENEMY = "defeat_enemy"
OBJECTIVE_EXIT_UNLOCKED = "exit_unlocked"
OBJECTIVE_EXPLORE = "explore"


class DungeonError(Exception):
    """A rule said no. The message is safe to show the player."""


class LaunchBlocked(DungeonError):
    """This quiz cannot start a run (too short, not owned, no questions)."""


# --------------------------------------------------
# 1. LAUNCH
# --------------------------------------------------
def get_owned_quiz(user, quiz_id):
    """Fetch a quiz only if this user owns the course it belongs to.

    Returns ``None`` for someone else's quiz so the view can 404 rather than
    403 - a 403 would confirm the quiz exists.
    """
    try:
        quiz_id = int(quiz_id)
    except (TypeError, ValueError):
        return None
    return (
        Quiz.objects.select_related("chapter__course")
        .filter(pk=quiz_id, chapter__course__user=user)
        .first()
    )


def _normalize_review_outcome(item):
    """Normalize an attempt item result to 'correct', 'partial', or 'incorrect'."""
    if not isinstance(item, dict):
        return "incorrect"
    state = str(item.get("result_state", "")).lower().strip()
    if state in {"correct", "partial", "incorrect"}:
        return state
    if item.get("is_correct") is True:
        return "correct"
    earned = float(item.get("earned_points", 0) or 0)
    maximum = float(item.get("maximum_points", 1) or 1)
    if earned >= maximum and maximum > 0:
        return "correct"
    elif earned > 0:
        return "partial"
    return "incorrect"


def get_unresolved_review_questions(user, quiz):
    """Return active Question objects for this quiz whose latest outcome is unresolved.

    A question is unresolved if its latest authoritative outcome is incorrect or partial,
    and has not subsequently been answered correctly in a later quiz attempt or mastered
    in a Review Run. Questions that were deleted or moved away from this quiz are excluded.
    """
    latest_outcome_by_question = {}

    # Chronologically inspect QuizAttempt history
    attempts = (
        QuizAttempt.objects.filter(user=user, quiz=quiz)
        .order_by("completed_at", "pk")
    )
    for attempt in attempts:
        items = (attempt.review_data or {}).get("review_items", [])
        for item in items:
            qid = item.get("question_id")
            if qid is not None:
                latest_outcome_by_question[qid] = (attempt.completed_at, _normalize_review_outcome(item))

    # Chronologically inspect finished Review Runs
    review_runs = (
        DungeonRun.objects.filter(
            user=user,
            quiz=quiz,
            run_type=DungeonRun.TYPE_REVIEW,
            finished_at__isnull=False,
        ).order_by("finished_at", "pk")
    )
    for rrun in review_runs:
        ts = rrun.finished_at
        mastered_set = set(rrun.review_mastered_question_ids or [])
        targeted = rrun.review_question_ids or []
        for qid in targeted:
            if qid in mastered_set:
                latest_outcome_by_question[qid] = (ts, "correct")
            else:
                latest_outcome_by_question[qid] = (ts, "incorrect")

    unresolved_ids = {
        qid for qid, (_, outcome) in latest_outcome_by_question.items()
        if outcome in {"incorrect", "partial"}
    }

    if not unresolved_ids:
        return Question.objects.none()

    return Question.objects.filter(quiz=quiz, id__in=unresolved_ids).order_by("order", "id")


def get_unresolved_counts_for_quizzes(user, quizzes):
    """Batch-compute unresolved mistake counts across multiple quizzes without N+1 queries."""
    if not quizzes:
        return {}

    quiz_ids = [q.id for q in quizzes]
    outcomes_by_quiz = {qid: {} for qid in quiz_ids}

    attempts = (
        QuizAttempt.objects.filter(user=user, quiz_id__in=quiz_ids)
        .order_by("completed_at", "pk")
    )
    for attempt in attempts:
        bucket = outcomes_by_quiz.get(attempt.quiz_id)
        if bucket is not None:
            items = (attempt.review_data or {}).get("review_items", [])
            for item in items:
                qid = item.get("question_id")
                if qid is not None:
                    bucket[qid] = (attempt.completed_at, _normalize_review_outcome(item))

    review_runs = (
        DungeonRun.objects.filter(
            user=user,
            quiz_id__in=quiz_ids,
            run_type=DungeonRun.TYPE_REVIEW,
            finished_at__isnull=False,
        ).order_by("finished_at", "pk")
    )
    for rrun in review_runs:
        bucket = outcomes_by_quiz.get(rrun.quiz_id)
        if bucket is not None:
            ts = rrun.finished_at
            mastered_set = set(rrun.review_mastered_question_ids or [])
            targeted = rrun.review_question_ids or []
            for qid in targeted:
                if qid in mastered_set:
                    bucket[qid] = (ts, "correct")
                else:
                    bucket[qid] = (ts, "incorrect")

    # Collect all unresolved question IDs
    all_unresolved_ids = set()
    quiz_unresolved_ids = {}
    for qid, bucket in outcomes_by_quiz.items():
        unres = {
            question_id for question_id, (_, outcome) in bucket.items()
            if outcome in {"incorrect", "partial"}
        }
        quiz_unresolved_ids[qid] = unres
        all_unresolved_ids.update(unres)

    # Filter to currently active valid questions belonging to each quiz
    valid_active_questions = set(
        Question.objects.filter(quiz_id__in=quiz_ids, id__in=all_unresolved_ids)
        .values_list("quiz_id", "id")
    )

    counts = {}
    for qid in quiz_ids:
        active_unresolved = [
            question_id for question_id in quiz_unresolved_ids.get(qid, set())
            if (qid, question_id) in valid_active_questions
        ]
        counts[qid] = len(active_unresolved)

    return counts


def estimate_run_minutes(question_count, enemy_count, rules=None):
    """Estimated duration in minutes derived from question count and encounters."""
    if question_count <= 0:
        return 0
    return max(2, round(question_count * 0.6 + enemy_count * 0.5))


def run_size_label(enemy_count):
    """Qualitative size label based on enemy count: Short, Standard, or Long."""
    if enemy_count <= 1:
        return "Short"
    elif enemy_count == 2:
        return "Standard"
    return "Long"


def get_recent_chapter_groups(user, limit=None):
    """Groups non-in-progress runs by chapter, showing the latest attempt or best cleared run."""
    from collections import OrderedDict

    runs = (
        DungeonRun.objects.filter(user=user)
        .exclude(status=DungeonRun.STATUS_IN_PROGRESS)
        .select_related("quiz__chapter__course")
        .order_by("-finished_at", "-started_at")
    )
    groups_by_chapter = OrderedDict()
    for run in runs:
        chapter = run.quiz.chapter
        if chapter.id not in groups_by_chapter:
            groups_by_chapter[chapter.id] = {
                "chapter": chapter,
                "course": chapter.course,
                "runs": [],
            }
        groups_by_chapter[chapter.id]["runs"].append(run)

    result = []
    for group_data in groups_by_chapter.values():
        all_runs = group_data["runs"]
        latest_run = all_runs[0]
        cleared_runs = [r for r in all_runs if r.status == DungeonRun.STATUS_CLEARED]
        best_cleared_run = (
            max(cleared_runs, key=lambda r: r.xp_awarded or 0) if cleared_runs else None
        )
        display_run = best_cleared_run or latest_run
        attempt_count = len(all_runs)
        earlier_count = attempt_count - 1

        result.append({
            "chapter": group_data["chapter"],
            "course": group_data["course"],
            "latest_run": latest_run,
            "best_cleared_run": best_cleared_run,
            "display_run": display_run,
            "attempt_count": attempt_count,
            "earlier_count": earlier_count,
            "has_cleared": best_cleared_run is not None,
        })

    if limit is not None:
        return result[:limit]
    return result


def build_launch_catalog(user):
    """Every quiz this user owns, annotated with whether it can start a run or review run."""
    courses = (
        Course.objects.filter(user=user)
        .exclude(status="archived")
        .prefetch_related("chapters__quiz__questions")
        .order_by("title")
    )
    profile = _get_profile(user)
    catalog = []

    # Gather all quizzes to batch-fetch unresolved counts with 0 N+1 growth
    all_quizzes = []
    for course in courses:
        for chapter in course.chapters.all():
            quiz = getattr(chapter, "quiz", None)
            if quiz is not None:
                all_quizzes.append(quiz)

    unresolved_counts = get_unresolved_counts_for_quizzes(user, all_quizzes)

    for course in courses:
        entries = []
        for chapter in course.chapters.all():
            quiz = getattr(chapter, "quiz", None)
            if quiz is None:
                continue

            question_count = len(quiz.questions.all())
            rules = resolve_combat_rules(
                plan=_plan_of(profile), quiz=quiz, question_count=question_count
            )
            minimum = minimum_questions_required(rules)
            enemy_count = enemy_count_for_questions(question_count, rules=rules)
            unresolved_count = unresolved_counts.get(quiz.id, 0)

            entries.append({
                "quiz": quiz,
                "chapter": chapter,
                "question_count": question_count,
                "enemy_count": enemy_count,
                "run_size": run_size_label(enemy_count),
                "estimated_minutes": estimate_run_minutes(question_count, enemy_count, rules=rules),
                "minimum_questions": minimum,
                "is_playable": question_count >= minimum and enemy_count > 0,
                "unresolved_count": unresolved_count,
                "review_run_available": unresolved_count > 0,
            })

        if entries:
            catalog.append({"course": course, "entries": entries})

    return catalog


@transaction.atomic
def start_or_resume_run(user, quiz, *, run_type=DungeonRun.TYPE_CLASSIC, seed=None):
    """Return this user's in-progress run for the quiz, creating one if needed.

    Resuming is deliberate: an abandoned browser tab must not become a way to
    re-roll the room or restore lost HP.
    """
    if run_type not in dict(DungeonRun.RUN_TYPE_CHOICES):
        raise DungeonError(f"Invalid run type '{run_type}'.")

    existing = _find_active_run(user, quiz)
    if existing is not None:
        if existing.run_type != run_type:
            existing_label = existing.get_run_type_display()
            requested_label = dict(DungeonRun.RUN_TYPE_CHOICES).get(run_type, run_type)
            raise LaunchBlocked(
                f"A {existing_label} is already in progress for this chapter. "
                f"Resume or abandon it before starting a {requested_label}."
            )
        return existing

    profile = _get_profile(user)

    if run_type == DungeonRun.TYPE_REVIEW:
        unresolved_questions = list(get_unresolved_review_questions(user, quiz))
        unresolved_count = len(unresolved_questions)
        if unresolved_count == 0:
            raise LaunchBlocked(
                f'"{_quiz_title(quiz)}" has no unresolved mistakes to review. '
                f"Play a Classic Expedition or complete a chapter quiz first."
            )

        rules = resolve_combat_rules(
            plan=_plan_of(profile), quiz=quiz, question_count=unresolved_count
        )
        specs = review_enemy_specs(unresolved_count, rules=rules)
        if not specs:
            raise LaunchBlocked("Unable to field enemies for this Review Run.")

        enemy_count = len(specs)
        seed = seed if seed is not None else Random().randrange(1, 2 ** 31 - 1)
        room_data = _build_room_for(user, seed=seed, enemy_count=enemy_count)
        question_ids = [q.id for q in unresolved_questions]

        try:
            with transaction.atomic():
                return _create_review_run(
                    user, quiz, rules, room_data, question_ids, specs, seed
                )
        except IntegrityError:
            existing = _find_active_run(user, quiz)
            if existing is None:
                raise
            return existing

    # Standard Classic Expedition
    question_ids = list(
        quiz.questions.order_by("order", "id").values_list("id", flat=True)
    )
    question_count = len(question_ids)

    rules = resolve_combat_rules(
        plan=_plan_of(profile), quiz=quiz, question_count=question_count
    )
    minimum = minimum_questions_required(rules)
    enemy_count = enemy_count_for_questions(question_count, rules=rules)

    if question_count < minimum or enemy_count < 1:
        raise LaunchBlocked(
            f'"{_quiz_title(quiz)}" has {question_count} '
            f"question{'' if question_count == 1 else 's'}. A dungeon run needs at "
            f"least {minimum} to field a single enemy - add more questions to this "
            f"quiz, then try again."
        )

    seed = seed if seed is not None else Random().randrange(1, 2 ** 31 - 1)
    room_data = _build_room_for(user, seed=seed, enemy_count=enemy_count)

    try:
        with transaction.atomic():
            return _create_run(user, quiz, rules, room_data, question_ids, enemy_count, seed)
    except IntegrityError:
        # A concurrent request created the run between our check and our insert;
        # the partial unique constraint rejected the duplicate. Resume theirs.
        existing = _find_active_run(user, quiz)
        if existing is None:
            raise
        return existing


def _find_active_run(user, quiz):
    return (
        DungeonRun.objects.filter(user=user, quiz=quiz, status=DungeonRun.STATUS_IN_PROGRESS)
        .select_related("quiz")
        .first()
    )


def _create_run(user, quiz, rules, room_data, question_ids, enemy_count, seed):
    run = DungeonRun.objects.create(
        user=user,
        quiz=quiz,
        run_type=DungeonRun.TYPE_CLASSIC,
        status=DungeonRun.STATUS_IN_PROGRESS,
        current_hp=rules.base_player_hp,
        max_hp=rules.base_player_hp,
        room_data=room_data,
        player_x=room_data["spawn"]["x"],
        player_y=room_data["spawn"]["y"],
        rules_key=rules.key,
        seed=seed,
    )
    DungeonInventory.objects.create(run=run)

    hands = _deal_questions(question_ids, enemy_count, rules, seed)
    for index, assigned in enumerate(hands):
        position = room_data["enemy_positions"][index]
        DungeonEnemy.objects.create(
            run=run,
            enemy_index=index,
            x=position["x"],
            y=position["y"],
            hp=rules.enemy_hp,
            max_hp=rules.enemy_hp,
            question_ids=assigned,
        )

    return run


def _create_review_run(user, quiz, rules, room_data, question_ids, specs, seed):
    run = DungeonRun.objects.create(
        user=user,
        quiz=quiz,
        run_type=DungeonRun.TYPE_REVIEW,
        review_question_ids=list(question_ids),
        review_mastered_question_ids=[],
        status=DungeonRun.STATUS_IN_PROGRESS,
        current_hp=rules.base_player_hp,
        max_hp=rules.base_player_hp,
        room_data=room_data,
        player_x=room_data["spawn"]["x"],
        player_y=room_data["spawn"]["y"],
        rules_key=rules.key,
        seed=seed,
    )
    DungeonInventory.objects.create(run=run)

    shuffled_qids = list(question_ids)
    Random(seed).shuffle(shuffled_qids)

    cursor = 0
    for index, spec in enumerate(specs):
        assigned = shuffled_qids[cursor:cursor + spec["question_count"]]
        cursor += spec["question_count"]
        position = room_data["enemy_positions"][index]
        DungeonEnemy.objects.create(
            run=run,
            enemy_index=index,
            x=position["x"],
            y=position["y"],
            hp=spec["max_hp"],
            max_hp=spec["max_hp"],
            question_ids=assigned,
        )

    return run


def _build_room_for(user, *, seed, enemy_count):
    """The first run a player ever takes uses the hand-authored room; every run
    after that is procedural."""
    is_first_run = not DungeonRun.objects.filter(user=user).exists()
    if is_first_run:
        return rooms.starter_room(enemy_count=enemy_count, seed=seed)
    return rooms.generate_room(seed, enemy_count)


def _deal_questions(question_ids, enemy_count, rules, seed):
    """Shuffle with the run seed, then deal a fixed hand to each enemy."""
    shuffled = list(question_ids)
    Random(seed).shuffle(shuffled)

    hands = []
    for index in range(enemy_count):
        start = index * rules.questions_per_enemy
        hands.append(shuffled[start:start + rules.questions_per_enemy])
    return hands


# --------------------------------------------------
# 2. MOVEMENT & ENCOUNTERS
# --------------------------------------------------
@transaction.atomic
def move_player(run, direction, *, rng=None):
    """Attempt one grid step. Returns what the client should render."""
    _lock(run)
    _require_active(run)

    if run.active_enemy_id:
        raise DungeonError("You can't walk away mid-battle.")

    if not isinstance(direction, str) or direction not in DIRECTIONS:
        raise DungeonError("Unknown direction.")

    grid = _grid(run)
    dx, dy = DIRECTIONS[direction]
    target_x, target_y = run.player_x + dx, run.player_y + dy
    unlocked = is_door_unlocked(run)
    tile = rooms.tile_at(grid, target_x, target_y)

    if tile == rooms.TILE_DOOR and not unlocked:
        return {
            "moved": False,
            "facing": direction,
            "blocked_reason": SEALED_DOOR_MESSAGE,
            "objective": serialize_objective(run),
        }

    if not rooms.is_walkable(grid, target_x, target_y, door_unlocked=unlocked):
        return {
            "moved": False,
            "facing": direction,
            "blocked_reason": None,
            "objective": serialize_objective(run),
        }

    run.player_x, run.player_y = target_x, target_y
    run.save(update_fields=["player_x", "player_y"])

    if tile == rooms.TILE_DOOR:
        return {
            "moved": True,
            "facing": direction,
            "x": target_x,
            "y": target_y,
            "exit": finish_run(run, cleared=True),
            "objective": serialize_objective(run),
        }

    encounter = _roll_encounter(run, target_x, target_y, rng=rng)
    return {
        "moved": True,
        "facing": direction,
        "x": target_x,
        "y": target_y,
        "encounter": encounter,
        "objective": serialize_objective(run, run.active_enemy if encounter else None),
    }


def _roll_encounter(run, x, y, *, rng=None):
    grid = _grid(run)
    if not rooms.is_encounter_tile(grid, x, y):
        return None

    living = [enemy for enemy in run.enemies.all() if not enemy.is_defeated]
    if not living:
        return None

    lair_holder = next((e for e in living if (e.x, e.y) == (x, y)), None)
    forced = FORCED_ENCOUNTER_ON_ENEMY_TILE and lair_holder is not None

    rules = rules_for(run)
    rng = rng or Random()
    if not forced and rng.random() >= rules.encounter_chance:
        return None

    enemy = lair_holder or min(
        living, key=lambda e: (abs(e.x - x) + abs(e.y - y), e.enemy_index)
    )

    if COMBO_RESETS_ON_NEW_ENEMY and run.active_enemy_id != enemy.id:
        if enemy.current_combo != 0:
            enemy.current_combo = 0
            enemy.save(update_fields=["current_combo"])

    run.active_enemy = enemy
    run.save(update_fields=["active_enemy"])
    return serialize_battle(run, enemy)


# --------------------------------------------------
# 3. BATTLE
# --------------------------------------------------
def current_question(enemy):
    """The one question this enemy is asking right now, or None."""
    remaining = enemy.remaining_question_ids
    if not remaining:
        return None
    return Question.objects.prefetch_related("choices").filter(pk=remaining[0]).first()


@transaction.atomic
def answer_question(run, question_id, submitted_answer):
    """Grade one answer and apply its consequences.

    Grading runs through the shared ``courses.grading`` helper, so a dungeon
    answer is judged by exactly the rules the chapter quiz uses.
    """
    _lock(run)
    _require_active(run)
    enemy = _require_battle(run)

    question = current_question(enemy)
    if question is None:
        raise DungeonError("This enemy has no questions left.")
    if str(question.id) != str(question_id):
        raise DungeonError("That isn't the question you're being asked.")

    earned_points, maximum_points, feedback = _grade_question(question, submitted_answer)
    outcome = _classify(earned_points, maximum_points)

    return _resolve_turn(
        run,
        enemy,
        question,
        outcome=outcome,
        feedback={
            **feedback,
            "earned_points": earned_points,
            "maximum_points": maximum_points,
            "explanation": question.explanation or "",
        },
    )


def _classify(earned_points, maximum_points):
    if maximum_points > 0 and earned_points >= maximum_points:
        return OUTCOME_CORRECT
    if earned_points > 0:
        return OUTCOME_PARTIAL
    return OUTCOME_INCORRECT


def _resolve_turn(run, enemy, question, *, outcome, feedback):
    """Apply one resolved question to both combatants.

    This function decides *who* takes damage; how much always comes from the
    rules or from the module constants.
    """
    rules = rules_for(run)

    enemy.answered_question_ids = list(enemy.answered_question_ids or []) + [question.id]

    damage_to_enemy = 0
    damage_to_player = 0
    combat_event = None

    if outcome == OUTCOME_CORRECT:
        new_combo = (enemy.current_combo or 0) + 1
        if new_combo >= COMBO_THRESHOLD:
            damage_to_enemy = COMBO_DAMAGE
            combat_event = {
                "code": "power_strike",
                "label": "Power Strike",
                "damage": COMBO_DAMAGE,
            }
            if COMBO_RESETS_AFTER_STRIKE:
                enemy.current_combo = 0
            else:
                enemy.current_combo = new_combo
        else:
            damage_to_enemy = BASE_DAMAGE
            combat_event = {
                "code": "direct_hit",
                "label": "Direct Hit",
                "damage": BASE_DAMAGE,
            }
            enemy.current_combo = new_combo

        if run.is_review_run and question.id not in (run.review_mastered_question_ids or []):
            run.review_mastered_question_ids = list(run.review_mastered_question_ids or []) + [question.id]
            run.save(update_fields=["review_mastered_question_ids"])
    elif outcome == OUTCOME_PARTIAL:
        if COMBO_RESETS_ON_PARTIAL:
            enemy.current_combo = 0
        damage_to_enemy = BASE_DAMAGE if PARTIAL_ANSWER_DAMAGES_ENEMY else 0
        damage_to_player = (
            rules.damage_per_wrong_answer if PARTIAL_ANSWER_DAMAGES_PLAYER else 0
        )
    elif outcome == OUTCOME_INCORRECT:
        if COMBO_RESETS_ON_WRONG:
            enemy.current_combo = 0
        damage_to_player = rules.damage_per_wrong_answer
    elif outcome == OUTCOME_SKIPPED:
        if COMBO_RESETS_ON_SKIP:
            enemy.current_combo = 0

    enemy.hp = max(enemy.hp - damage_to_enemy, 0)
    run.current_hp = max(run.current_hp - damage_to_player, 0)

    # An enemy falls when its HP is gone *or* when its hand is spent - surviving
    # its full set of questions counts as a clear.
    # In a Review Run, the enemy only falls once all its assigned remediation questions
    # have been resolved, even if HP hit 0 early from combos.
    if run.is_review_run:
        enemy_defeated = not enemy.has_questions_left
    else:
        enemy_defeated = enemy.hp <= 0 or not enemy.has_questions_left

    if enemy_defeated:
        enemy.is_defeated = True
        enemy.current_combo = 0

    enemy.save(update_fields=["hp", "is_defeated", "answered_question_ids", "current_combo"])
    run.save(update_fields=["current_hp"])

    drops = None
    if enemy_defeated:
        drops = _award_drops(run, enemy)
        run.active_enemy = None
        run.save(update_fields=["active_enemy"])

    is_power_strike = bool(combat_event and combat_event["code"] == "power_strike")
    if is_power_strike:
        feedback["combat_message"] = "Power Strike! 2 damage!"
    elif damage_to_enemy > 0:
        feedback["combat_message"] = f"{damage_to_enemy} damage"

    stored_combo = enemy.current_combo or 0
    combo_payload = {
        "count": stored_combo,
        "threshold": COMBO_THRESHOLD,
        "remaining": max(COMBO_THRESHOLD - stored_combo, 0),
        "current": stored_combo,
        "is_power_strike": is_power_strike,
    }

    payload = {
        "outcome": outcome,
        "damage_to_enemy": damage_to_enemy,
        "damage_to_player": damage_to_player,
        "damage_dealt": damage_to_enemy,
        "combat_event": combat_event,
        "combo": combo_payload,
        "enemy_defeated": enemy_defeated,
        "drops": drops,
        "feedback": feedback,
        "hp": {"current": run.current_hp, "max": run.max_hp},
        "enemy": serialize_enemy(enemy),
        "inventory": serialize_inventory(run),
        "door_unlocked": is_door_unlocked(run),
        "run_over": None,
        "battle": None,
        "objective": serialize_objective(run, None if enemy_defeated else enemy),
    }

    if run.current_hp <= 0:
        payload["run_over"] = finish_run(run, cleared=False)
        payload["objective"] = serialize_objective(run)
        return payload

    if not enemy_defeated:
        payload["battle"] = serialize_battle(run, enemy)

    return payload


def _award_drops(run, enemy):
    """Key piece always, plus one weighted bonus roll.

    The roll is seeded off the run so a given run's loot is reproducible.
    """
    rules = rules_for(run)
    bag = DungeonInventory.objects.filter(run=run)

    # F() increments happen in the database, so they can never be computed
    # from a count this request read before another request wrote.
    bag.update(key_pieces=F("key_pieces") + KEY_PIECES_PER_ENEMY)

    rng = Random(f"{run.seed}:{run.pk}:{enemy.enemy_index}")
    rolled = roll_item_drop(rng)

    granted = None
    wasted_at_cap = False
    field = POTION_FIELDS.get(rolled)
    if field:
        # The cap is part of the same UPDATE, so it holds under concurrency too.
        added = bag.filter(**{f"{field}__lt": rules.max_potions_held}).update(
            **{field: F(field) + 1}
        )
        if added:
            granted = rolled
        else:
            wasted_at_cap = True

    run.inventory.refresh_from_db()

    return {
        "key_pieces": KEY_PIECES_PER_ENEMY,
        "rolled": rolled,
        "item": granted or ITEM_NOTHING,
        "item_wasted_at_cap": wasted_at_cap,
    }


@transaction.atomic
def use_item(run, item_key):
    """Spend a potion. Both effects are resolved and persisted server-side."""
    _lock(run)
    _require_active(run)
    rules = rules_for(run)
    inventory = _inventory(run)

    if item_key == ITEM_HEALTH_POTION:
        if inventory.health_potions <= 0:
            raise DungeonError("You have no health potions.")
        if run.current_hp >= run.max_hp:
            raise DungeonError("You're already at full health.")

        healed_to = min(run.current_hp + rules.health_potion_heal, run.max_hp)
        healed_by = healed_to - run.current_hp
        run.current_hp = healed_to
        inventory.health_potions -= 1
        run.save(update_fields=["current_hp"])
        inventory.save(update_fields=["health_potions"])

        return {
            "item": ITEM_HEALTH_POTION,
            "healed_by": healed_by,
            "hp": {"current": run.current_hp, "max": run.max_hp},
            "inventory": serialize_inventory(run),
            "battle": (
                serialize_battle(run, run.active_enemy) if run.active_enemy_id else None
            ),
            "objective": serialize_objective(run),
        }

    if item_key == ITEM_SKIP_POTION:
        if inventory.skip_potions <= 0:
            raise DungeonError("You have no skip potions.")

        enemy = _require_battle(run)
        question = current_question(enemy)
        if question is None:
            raise DungeonError("There's nothing to skip.")

        inventory.skip_potions -= 1
        inventory.save(update_fields=["skip_potions"])

        # A skip spends the question without either side taking damage.
        result = _resolve_turn(
            run,
            enemy,
            question,
            outcome=OUTCOME_SKIPPED,
            feedback={"skipped": True, "explanation": question.explanation or ""},
        )
        result["item"] = ITEM_SKIP_POTION
        return result

    raise DungeonError("That item can't be used.")


# --------------------------------------------------
# 4. EXIT & RUN COMPLETION
# --------------------------------------------------
def required_key_pieces(run):
    return run.enemies.count() * KEY_PIECES_PER_ENEMY


def is_door_unlocked(run):
    """The door opens only once every enemy is down *and* every piece is held.

    Checking the enemies directly, not just the key counter, means no bug or
    race that inflates the counter can ever open the door early.
    """
    required = required_key_pieces(run)
    if required <= 0 or run.enemies.filter(is_defeated=False).exists():
        return False
    return _inventory(run).key_pieces >= required


@transaction.atomic
def attempt_exit(run):
    _lock(run)
    _require_active(run)

    if not is_door_unlocked(run):
        raise DungeonError(SEALED_DOOR_MESSAGE)

    door = (run.room_data or {}).get("door", {})
    if (run.player_x, run.player_y) != (door.get("x"), door.get("y")):
        raise DungeonError("Walk to the door first.")

    return finish_run(run, cleared=True)


def finish_run(run, *, cleared):
    """End the run and, for a clear, pay XP exactly once.

    The payout is guarded by a conditional UPDATE on ``xp_awarded``: two
    simultaneous POSTs race on the database and only one of them wins, so a
    double submit cannot farm the award. XP goes through
    ``UserProfile.award_xp`` so the XPTransaction ledger stays in step with the
    cached total, and no QuizAttempt is written - a dungeon run is a different
    activity and would pollute quiz score history.
    """
    status = DungeonRun.STATUS_CLEARED if cleared else DungeonRun.STATUS_FAILED
    enemies_defeated = run.enemies.filter(is_defeated=True).count()

    if run.is_review_run:
        targeted = run.targeted_count
        mastered_count = run.mastered_count
        is_mastered = cleared and targeted > 0 and mastered_count == targeted
        xp = calculate_review_run_xp(
            mastered=is_mastered,
            questions_mastered=mastered_count,
        )
        reason = XP_REASON_REVIEW_TEMPLATE.format(quiz_title=_quiz_title(run.quiz))
    else:
        xp = calculate_run_xp(
            cleared=cleared,
            enemies_defeated=enemies_defeated,
            hp_remaining=run.current_hp,
        )
        reason = XP_REASON_TEMPLATE.format(quiz_title=_quiz_title(run.quiz))

    claimed = DungeonRun.objects.filter(pk=run.pk, xp_awarded__isnull=True).update(
        status=status,
        finished_at=timezone.now(),
        active_enemy=None,
        xp_awarded=xp,
    )

    run.refresh_from_db()

    if claimed and xp > 0:
        profile = _get_profile(run.user)
        if profile is not None:
            profile.award_xp(xp, reason=reason)
            profile.record_study_activity()

    return {
        "status": run.status,
        "cleared": cleared,
        "run_type": run.run_type,
        "is_review_run": run.is_review_run,
        "is_mastered": run.is_mastered,
        "targeted_count": run.targeted_count,
        "mastered_count": run.mastered_count,
        "xp_awarded": run.xp_awarded or 0,
        "xp_newly_awarded": bool(claimed) and xp > 0,
        "enemies_defeated": enemies_defeated,
        "hp_remaining": run.current_hp,
        "objective": serialize_objective(run),
    }


@transaction.atomic
def abandon_run(run):
    _lock(run)
    if not run.is_active:
        return run
    run.status = DungeonRun.STATUS_ABANDONED
    run.finished_at = timezone.now()
    run.active_enemy = None
    run.save(update_fields=["status", "finished_at", "active_enemy"])
    return run


def get_run_verdict_code(run):
    """Authoritative verdict classification for a completed or terminated DungeonRun."""
    if run.status == DungeonRun.STATUS_ABANDONED:
        return "abandoned"

    if run.status == DungeonRun.STATUS_FAILED:
        return "failed"

    if run.run_type == DungeonRun.TYPE_REVIEW:
        targeted = set(run.review_question_ids or [])
        mastered = set(run.review_mastered_question_ids or [])
        return "review_mastered" if (targeted and targeted <= mastered) else "review_incomplete"

    if run.status == DungeonRun.STATUS_CLEARED:
        return "cleared"

    return "incomplete"


# --------------------------------------------------
# 5. SERIALIZATION (client-safe by construction)
# --------------------------------------------------
def serialize_question(run, question):
    """The client-safe shape of a question.

    Deliberately absent: ``Choice.is_correct``, ``Question.answer_data`` and the
    explanation. Choices are shuffled per run so not even their ordering can
    hint at the answer.
    """
    payload = {
        "id": question.id,
        "type": question.question_type,
        "prompt": question.text,
    }

    if question.question_type in {"multiple_choice", "true_false"}:
        choices = [{"id": c.id, "text": c.text} for c in question.choices.all()]
        Random(f"{run.seed}:{question.id}").shuffle(choices)
        payload["choices"] = choices

    if question.question_type == "enumeration":
        answer_data = question.answer_data or {}
        # How many blanks to render - a count, never the items themselves.
        payload["expected_item_count"] = len(answer_data.get("expected_items", []))
        payload["order_matters"] = bool(answer_data.get("order_matters", False))

    return payload


def serialize_objective(run, battle_enemy=None):
    """The authoritative current objective for this run.

    Decided entirely on the server; the client renders what it receives and
    never computes gameplay conditions or win/loss states independently.
    """
    if run.status in (DungeonRun.STATUS_FAILED, DungeonRun.STATUS_ABANDONED) or not run.is_alive:
        return {
            "code": OBJECTIVE_RUN_FAILED,
            "label": "You have fallen",
            "detail": "The dungeon claimed this run.",
        }

    if run.status == DungeonRun.STATUS_CLEARED:
        return {
            "code": OBJECTIVE_RUN_COMPLETE,
            "label": "Review mastered" if run.is_mastered else "Dungeon cleared",
            "detail": "You escaped through the exit door.",
        }

    if run.is_review_run:
        enemy = battle_enemy or (run.active_enemy if run.active_enemy_id else None)
        if enemy and not enemy.is_defeated:
            remaining_q = len(enemy.remaining_question_ids)
            q_word = "question" if remaining_q == 1 else "questions"
            return {
                "code": OBJECTIVE_DEFEAT_ENEMY,
                "label": f"Review Enemy {enemy.enemy_index + 1}",
                "detail": f"{remaining_q} remediation {q_word} remaining ({enemy.hp} HP).",
            }

        if is_door_unlocked(run):
            return {
                "code": OBJECTIVE_EXIT_UNLOCKED,
                "label": "Review Complete — Exit Unlocked",
                "detail": "Step onto the door to record your remediation.",
            }

        living = run.enemies.filter(is_defeated=False).count()
        plural = "enemy" if living == 1 else "enemies"
        return {
            "code": OBJECTIVE_EXPLORE,
            "label": "Review Run: Explore",
            "detail": f"Search the tall grass ({living} review {plural} remaining · {run.mastered_count}/{run.targeted_count} mastered).",
        }

    enemy = battle_enemy or (run.active_enemy if run.active_enemy_id else None)
    if enemy and not enemy.is_defeated:
        return {
            "code": OBJECTIVE_DEFEAT_ENEMY,
            "label": f"Defeat Enemy {enemy.enemy_index + 1}",
            "detail": f"{enemy.hp} of {enemy.max_hp} HP remaining",
        }

    if is_door_unlocked(run):
        return {
            "code": OBJECTIVE_EXIT_UNLOCKED,
            "label": "Exit is open",
            "detail": "Step onto the door to escape.",
        }

    living = run.enemies.filter(is_defeated=False).count()
    plural = "enemy" if living == 1 else "enemies"
    return {
        "code": OBJECTIVE_EXPLORE,
        "label": "Explore the room",
        "detail": f"Search the tall grass ({living} {plural} remaining)",
    }


def serialize_enemy(enemy):
    return {
        "index": enemy.enemy_index,
        "x": enemy.x,
        "y": enemy.y,
        "hp": enemy.hp,
        "max_hp": enemy.max_hp,
        "is_defeated": enemy.is_defeated,
        "questions_total": len(enemy.question_ids or []),
        "questions_answered": len(enemy.answered_question_ids or []),
        "combo": enemy.current_combo,
    }


def serialize_battle(run, enemy):
    if enemy is None:
        return None
    question = current_question(enemy)
    return {
        "enemy": serialize_enemy(enemy),
        "question": serialize_question(run, question) if question else None,
        "question_number": len(enemy.answered_question_ids or []) + 1,
        "questions_total": len(enemy.question_ids or []),
        "objective": serialize_objective(run, enemy),
        "combo": {
            "count": enemy.current_combo or 0,
            "threshold": COMBO_THRESHOLD,
            "remaining": max(COMBO_THRESHOLD - (enemy.current_combo or 0), 0),
            "current": enemy.current_combo or 0,
            "is_power_strike": False,
        },
    }


def serialize_inventory(run):
    inventory = _inventory(run)
    rules = rules_for(run)
    required = required_key_pieces(run)
    return {
        "key_pieces": inventory.key_pieces,
        "key_pieces_required": required,
        "has_final_key": required > 0 and inventory.key_pieces >= required,
        "skip_potions": inventory.skip_potions,
        "health_potions": inventory.health_potions,
        "max_potions_held": rules.max_potions_held,
    }


def serialize_run(run):
    """The whole client state: everything the board needs, nothing it shouldn't."""
    room_data = run.room_data or {}
    active = run.active_enemy if run.active_enemy_id else None

    return {
        "run_id": run.pk,
        "status": run.status,
        "run_type": run.run_type,
        "is_review_run": run.is_review_run,
        "review_targeted_count": run.targeted_count,
        "review_mastered_count": run.mastered_count,
        "is_mastered": run.is_mastered,
        "is_active": run.is_active,
        "hp": {"current": run.current_hp, "max": run.max_hp},
        "player": {"x": run.player_x, "y": run.player_y},
        "room": {
            "grid": room_data.get("grid", []),
            "width": room_data.get("width", 0),
            "height": room_data.get("height", 0),
            "door": room_data.get("door", {}),
            "spawn": room_data.get("spawn", {}),
            "door_unlocked": is_door_unlocked(run),
        },
        "enemies": [serialize_enemy(enemy) for enemy in run.enemies.all()],
        "inventory": serialize_inventory(run),
        "battle": serialize_battle(run, active),
        "objective": serialize_objective(run, active),
        "display": display_settings(),
        "quiz": {
            "title": _quiz_title(run.quiz),
            "chapter": run.quiz.chapter.title,
            "course": run.quiz.chapter.course.title,
        },
    }


def display_settings():
    """Pixel scale, published from config so no CSS or JS spells it out."""
    return {
        "tile_size": TILE_SIZE,
        "icon_size": ICON_SIZE,
        "min_scale": MIN_BOARD_SCALE,
        "max_scale": MAX_BOARD_SCALE,
        "vertical_reserve": BOARD_VERTICAL_RESERVE,
        "ui_scale": UI_SCALE,
    }


# --------------------------------------------------
# 6. INTERNALS
# --------------------------------------------------
def rules_for(run):
    return resolve_combat_rules(plan=run.rules_key, quiz=run.quiz, question_count=0)


def rules_for_user(user):
    """The rules this user would play under right now, for the launch screen."""
    return resolve_combat_rules(plan=_plan_of(_get_profile(user)))


def _grid(run):
    return (run.room_data or {}).get("grid", [])


def _inventory(run):
    inventory = getattr(run, "inventory", None)
    if inventory is None:
        inventory = DungeonInventory.objects.create(run=run)
        run.inventory = inventory
    return inventory


def _lock(run):
    """Take this run's row lock for the rest of the enclosing transaction, then
    reload it.

    Two requests for one run (a double-click, a replayed POST) can both load it
    before either writes. Locking and reloading makes the second one wait and
    then act on what the first committed, instead of on its stale copy. On
    databases without row locks (SQLite) the lock is a no-op, but the reload
    still discards stale state and SQLite serialises the writes.
    """
    DungeonRun.objects.select_for_update().filter(pk=run.pk).values_list("pk").first()
    run.refresh_from_db()


def _require_active(run):
    if not run.is_active:
        raise DungeonError("This run is already over.")


def _require_battle(run):
    if not run.active_enemy_id:
        raise DungeonError("You're not in a battle.")
    return run.active_enemy


def _get_profile(user):
    return getattr(user, "userprofile", None) or getattr(user, "profile", None)


def _plan_of(profile):
    return getattr(profile, "plan", None)


def _quiz_title(quiz):
    return quiz.title or quiz.chapter.title
