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
from courses.models import Course, Question, Quiz

from . import rooms
from .combat_config import (
    BOARD_VERTICAL_RESERVE,
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
    XP_REASON_TEMPLATE,
    calculate_run_xp,
    enemy_count_for_questions,
    minimum_questions_required,
    resolve_combat_rules,
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


def build_launch_catalog(user):
    """Every quiz this user owns, annotated with whether it can start a run."""
    courses = (
        Course.objects.filter(user=user)
        .exclude(status="archived")
        .prefetch_related("chapters__quiz__questions")
        .order_by("title")
    )
    profile = _get_profile(user)
    catalog = []

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

            entries.append({
                "quiz": quiz,
                "chapter": chapter,
                "question_count": question_count,
                "enemy_count": enemy_count,
                "minimum_questions": minimum,
                "is_playable": question_count >= minimum and enemy_count > 0,
            })

        if entries:
            catalog.append({"course": course, "entries": entries})

    return catalog


@transaction.atomic
def start_or_resume_run(user, quiz, *, seed=None):
    """Return this user's in-progress run for the quiz, creating one if needed.

    Resuming is deliberate: an abandoned browser tab must not become a way to
    re-roll the room or restore lost HP.
    """
    existing = _find_active_run(user, quiz)
    if existing is not None:
        return existing

    question_ids = list(
        quiz.questions.order_by("order", "id").values_list("id", flat=True)
    )
    question_count = len(question_ids)

    profile = _get_profile(user)
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
        }

    if not rooms.is_walkable(grid, target_x, target_y, door_unlocked=unlocked):
        return {"moved": False, "facing": direction, "blocked_reason": None}

    run.player_x, run.player_y = target_x, target_y
    run.save(update_fields=["player_x", "player_y"])

    if tile == rooms.TILE_DOOR:
        return {
            "moved": True,
            "facing": direction,
            "x": target_x,
            "y": target_y,
            "exit": finish_run(run, cleared=True),
        }

    return {
        "moved": True,
        "facing": direction,
        "x": target_x,
        "y": target_y,
        "encounter": _roll_encounter(run, target_x, target_y, rng=rng),
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

    if outcome == OUTCOME_CORRECT:
        damage_to_enemy = DAMAGE_PER_CORRECT_ANSWER
    elif outcome == OUTCOME_PARTIAL:
        damage_to_enemy = DAMAGE_PER_CORRECT_ANSWER if PARTIAL_ANSWER_DAMAGES_ENEMY else 0
        damage_to_player = (
            rules.damage_per_wrong_answer if PARTIAL_ANSWER_DAMAGES_PLAYER else 0
        )
    elif outcome == OUTCOME_INCORRECT:
        damage_to_player = rules.damage_per_wrong_answer

    enemy.hp = max(enemy.hp - damage_to_enemy, 0)
    run.current_hp = max(run.current_hp - damage_to_player, 0)

    # An enemy falls when its HP is gone *or* when its hand is spent - surviving
    # its full set of questions counts as a clear.
    enemy_defeated = enemy.hp <= 0 or not enemy.has_questions_left
    if enemy_defeated:
        enemy.is_defeated = True

    enemy.save(update_fields=["hp", "is_defeated", "answered_question_ids"])
    run.save(update_fields=["current_hp"])

    drops = None
    if enemy_defeated:
        drops = _award_drops(run, enemy)
        run.active_enemy = None
        run.save(update_fields=["active_enemy"])

    payload = {
        "outcome": outcome,
        "damage_to_enemy": damage_to_enemy,
        "damage_to_player": damage_to_player,
        "enemy_defeated": enemy_defeated,
        "drops": drops,
        "feedback": feedback,
        "hp": {"current": run.current_hp, "max": run.max_hp},
        "enemy": serialize_enemy(enemy),
        "inventory": serialize_inventory(run),
        "door_unlocked": is_door_unlocked(run),
        "run_over": None,
        "battle": None,
    }

    if run.current_hp <= 0:
        payload["run_over"] = finish_run(run, cleared=False)
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
    xp = calculate_run_xp(
        cleared=cleared,
        enemies_defeated=enemies_defeated,
        hp_remaining=run.current_hp,
    )

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
            profile.award_xp(
                xp, reason=XP_REASON_TEMPLATE.format(quiz_title=_quiz_title(run.quiz))
            )
            profile.record_study_activity()

    return {
        "status": run.status,
        "cleared": cleared,
        "xp_awarded": run.xp_awarded or 0,
        "xp_newly_awarded": bool(claimed) and xp > 0,
        "enemies_defeated": enemies_defeated,
        "hp_remaining": run.current_hp,
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
