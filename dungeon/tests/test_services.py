from random import Random

from django.contrib.auth.models import User
from django.test import TestCase

from courses.models import Chapter, Choice, Course, Question, Quiz, QuizAttempt, XPTransaction

from dungeon import rooms, services
from dungeon.combat_config import (
    ITEM_HEALTH_POTION,
    ITEM_NOTHING,
    ITEM_SKIP_POTION,
    MAX_ENEMIES,
    resolve_combat_rules,
)
from dungeon.models import DungeonInventory, DungeonRun


class AlwaysEncounter(Random):
    """An RNG whose every roll triggers an encounter."""

    def random(self):
        return 0.0


class NeverEncounter(Random):
    def random(self):
        return 0.999999


def make_quiz(user, *, question_count, title="Dungeon Drill"):
    course = Course.objects.create(user=user, title=f"{title} Course")
    chapter = Chapter.objects.create(course=course, order=1, title=f"{title} Chapter")
    quiz = Quiz.objects.create(chapter=chapter, title=title)

    for index in range(question_count):
        question = Question.objects.create(
            quiz=quiz,
            order=index + 1,
            question_type="multiple_choice",
            text=f"Question {index + 1}?",
            explanation=f"Because reason {index + 1}.",
        )
        Choice.objects.create(question=question, text=f"Right {index + 1}", is_correct=True)
        Choice.objects.create(question=question, text=f"Wrong {index + 1}", is_correct=False)

    return quiz


def correct_choice_id(question_id):
    return Choice.objects.get(question_id=question_id, is_correct=True).id


def wrong_choice_id(question_id):
    return Choice.objects.filter(question_id=question_id, is_correct=False).first().id


class DungeonTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="delver", password="password123")

    def start(self, *, question_count=20, seed=42, user=None):
        user = user or self.user
        quiz = make_quiz(user, question_count=question_count)
        return services.start_or_resume_run(user, quiz, seed=seed)

    def engage(self, run, enemy=None):
        """Stand next to an enemy's lair and step onto it, forcing a battle."""
        enemy = enemy or run.enemies.filter(is_defeated=False).first()
        grid = run.room_data["grid"]
        for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            nx, ny = enemy.x + dx, enemy.y + dy
            if rooms.is_walkable(grid, nx, ny):
                run.player_x, run.player_y = nx, ny
                run.save(update_fields=["player_x", "player_y"])
                direction = {
                    (0, 1): "up", (0, -1): "down", (1, 0): "left", (-1, 0): "right",
                }[(dx, dy)]
                return services.move_player(run, direction, rng=NeverEncounter())
        self.fail("Enemy lair has no walkable neighbour.")

    def answer_correctly(self, run):
        question_id = services.current_question(run.active_enemy).id
        return services.answer_question(
            run, question_id, {"choice_id": correct_choice_id(question_id)}
        )

    def answer_wrongly(self, run):
        question_id = services.current_question(run.active_enemy).id
        return services.answer_question(
            run, question_id, {"choice_id": wrong_choice_id(question_id)}
        )

    def finish_current_battle(self, run):
        """Answer the battle already in progress perfectly until it ends."""
        result = None
        while run.active_enemy_id:
            result = self.answer_correctly(run)
        return result

    def defeat(self, run, enemy=None):
        """Fight one enemy to the ground with perfect answers."""
        self.engage(run, enemy)
        return self.finish_current_battle(run)

    def reload(self, run):
        """Re-fetch a run the way a fresh request would, dropping cached relations."""
        return DungeonRun.objects.get(pk=run.pk)

    def set_inventory(self, run, **counts):
        """Stock the bag and hand back a run that sees the change."""
        DungeonInventory.objects.filter(run=run).update(**counts)
        return self.reload(run)

    def clear_all_enemies(self, run):
        while run.enemies.filter(is_defeated=False).exists():
            self.defeat(run)

    def walk_to_door(self, run):
        door = run.room_data["door"]
        run.player_x, run.player_y = door["x"], door["y"]
        run.save(update_fields=["player_x", "player_y"])


class RunCreationTests(DungeonTestCase):
    def test_starting_hp_comes_from_the_plan(self):
        run = self.start()
        self.assertEqual(run.max_hp, resolve_combat_rules(plan="free").base_player_hp)
        self.assertEqual(run.current_hp, run.max_hp)

        plus_user = User.objects.create_user(username="subscriber", password="password123")
        plus_user.userprofile.plan = "plus"
        plus_user.userprofile.save(update_fields=["plan"])

        plus_run = self.start(user=plus_user)
        self.assertEqual(plus_run.max_hp, resolve_combat_rules(plan="plus").base_player_hp)
        self.assertGreater(plus_run.max_hp, run.max_hp)

    def test_short_quiz_is_blocked_with_a_clear_message(self):
        quiz = make_quiz(self.user, question_count=4)

        with self.assertRaises(services.LaunchBlocked) as blocked:
            services.start_or_resume_run(self.user, quiz)

        message = str(blocked.exception)
        self.assertIn("4 questions", message)
        self.assertIn("at least 5", message)
        self.assertFalse(DungeonRun.objects.exists())

    def test_enemy_count_is_capped_and_each_gets_a_full_hand(self):
        run = self.start(question_count=100)
        rules = services.rules_for(run)

        self.assertEqual(run.enemies.count(), MAX_ENEMIES)
        dealt = []
        for enemy in run.enemies.all():
            self.assertEqual(len(enemy.question_ids), rules.questions_per_enemy)
            self.assertEqual(enemy.hp, rules.enemy_hp)
            dealt.extend(enemy.question_ids)

        self.assertEqual(len(dealt), len(set(dealt)), "a question was dealt twice")

    def test_enemies_stand_on_grass(self):
        run = self.start()
        grid = run.room_data["grid"]
        for enemy in run.enemies.all():
            self.assertTrue(rooms.is_encounter_tile(grid, enemy.x, enemy.y))

    def test_second_launch_resumes_instead_of_rerolling(self):
        run = self.start()
        run.current_hp = 1
        run.save(update_fields=["current_hp"])

        resumed = services.start_or_resume_run(self.user, run.quiz)

        self.assertEqual(resumed.pk, run.pk)
        self.assertEqual(resumed.current_hp, 1, "resuming must not heal the player")
        self.assertEqual(DungeonRun.objects.count(), 1)

    def test_a_finished_run_does_not_block_a_new_one(self):
        run = self.start()
        services.abandon_run(run)

        fresh = services.start_or_resume_run(self.user, run.quiz)

        self.assertNotEqual(fresh.pk, run.pk)
        self.assertEqual(fresh.current_hp, fresh.max_hp)


class MovementTests(DungeonTestCase):
    def test_walls_block_movement(self):
        run = self.start()
        grid = run.room_data["grid"]
        # Stand beside a wall and walk into it.
        run.player_x, run.player_y = 1, 1
        run.save(update_fields=["player_x", "player_y"])
        self.assertEqual(rooms.tile_at(grid, 0, 1), rooms.TILE_WALL)

        result = services.move_player(run, "left", rng=NeverEncounter())

        self.assertFalse(result["moved"])
        run.refresh_from_db()
        self.assertEqual((run.player_x, run.player_y), (1, 1))

    def test_walking_is_persisted(self):
        run = self.start()
        start = (run.player_x, run.player_y)
        services.move_player(run, "up", rng=NeverEncounter())

        reloaded = DungeonRun.objects.get(pk=run.pk)
        self.assertNotEqual((reloaded.player_x, reloaded.player_y), start)

    def test_grass_can_roll_no_encounter(self):
        run = self.start()
        result = self.engage(run)  # engage() forces via the lair rule
        self.assertIsNotNone(result["encounter"])

        run.refresh_from_db()
        run.active_enemy = None
        run.save(update_fields=["active_enemy"])

        # Standing on grass that is nobody's lair with a cold RNG: no battle.
        grid = run.room_data["grid"]
        lairs = {(e.x, e.y) for e in run.enemies.all()}
        plain_grass = next(
            (x, y)
            for (x, y) in rooms.find_tiles(grid, rooms.TILE_GRASS)
            if (x, y) not in lairs and rooms.is_walkable(grid, x, y - 1)
        )
        run.player_x, run.player_y = plain_grass[0], plain_grass[1] - 1
        run.save(update_fields=["player_x", "player_y"])

        result = services.move_player(run, "down", rng=NeverEncounter())
        self.assertIsNone(result["encounter"])

    def test_cannot_walk_away_mid_battle(self):
        run = self.start()
        self.engage(run)

        with self.assertRaises(services.DungeonError):
            services.move_player(run, "up", rng=NeverEncounter())

    def test_sealed_door_blocks_movement_and_explains_why(self):
        run = self.start()
        door = run.room_data["door"]
        # Stand on the walkable tile below the door and try to step through.
        run.player_x, run.player_y = door["x"], door["y"] + 1
        run.save(update_fields=["player_x", "player_y"])

        result = services.move_player(run, "up", rng=NeverEncounter())

        self.assertFalse(result["moved"])
        self.assertIn("sealed", result["blocked_reason"])
        run.refresh_from_db()
        self.assertEqual(run.status, DungeonRun.STATUS_IN_PROGRESS)


class BattleTests(DungeonTestCase):
    def test_correct_answer_damages_the_enemy_only(self):
        run = self.start()
        self.engage(run)
        enemy_hp_before = run.active_enemy.hp

        result = self.answer_correctly(run)

        self.assertEqual(result["outcome"], services.OUTCOME_CORRECT)
        self.assertEqual(result["enemy"]["hp"], enemy_hp_before - 1)
        self.assertEqual(result["hp"]["current"], run.max_hp)

    def test_wrong_answer_damages_the_player_only(self):
        run = self.start()
        self.engage(run)
        rules = services.rules_for(run)
        enemy_hp_before = run.active_enemy.hp

        result = self.answer_wrongly(run)

        self.assertEqual(result["outcome"], services.OUTCOME_INCORRECT)
        self.assertEqual(result["enemy"]["hp"], enemy_hp_before)
        self.assertEqual(
            result["hp"]["current"], run.max_hp - rules.damage_per_wrong_answer
        )

    def test_answering_out_of_turn_is_rejected(self):
        run = self.start()
        self.engage(run)
        other_question = (
            run.quiz.questions.exclude(pk=services.current_question(run.active_enemy).pk)
            .first()
        )

        with self.assertRaises(services.DungeonError):
            services.answer_question(run, other_question.id, {"choice_id": 1})

    def test_enemy_dies_when_hp_runs_out(self):
        run = self.start()
        result = self.defeat(run)

        self.assertTrue(result["enemy_defeated"])
        self.assertEqual(result["enemy"]["hp"], 0)
        self.assertIsNone(run.active_enemy_id)

    def test_surviving_the_full_question_set_counts_as_a_clear(self):
        """Wrong answers still exhaust the hand; a spent hand kills the enemy."""
        run = self.start()
        run.current_hp = 99
        run.max_hp = 99
        run.save(update_fields=["current_hp", "max_hp"])
        self.engage(run)
        enemy = run.active_enemy
        rules = services.rules_for(run)

        result = None
        for _ in range(rules.questions_per_enemy):
            result = self.answer_wrongly(run)

        self.assertTrue(result["enemy_defeated"])
        self.assertGreater(result["enemy"]["hp"], 0, "died from a spent hand, not damage")
        enemy.refresh_from_db()
        self.assertTrue(enemy.is_defeated)

    def test_player_death_ends_the_run_with_no_xp(self):
        run = self.start()
        self.engage(run)

        result = None
        while run.current_hp > 0:
            result = self.answer_wrongly(run)
            run.refresh_from_db()

        self.assertIsNotNone(result["run_over"])
        self.assertEqual(result["run_over"]["status"], DungeonRun.STATUS_FAILED)
        self.assertIsNone(result["battle"])

        run.refresh_from_db()
        self.assertEqual(run.status, DungeonRun.STATUS_FAILED)
        self.assertIsNotNone(run.finished_at)
        self.assertEqual(run.xp_awarded, 0)
        self.assertEqual(self.user.userprofile.total_xp, 0)
        self.assertFalse(XPTransaction.objects.filter(user=self.user).exists())

    def test_a_dead_run_rejects_further_play(self):
        run = self.start()
        run.current_hp = 0
        run.status = DungeonRun.STATUS_FAILED
        run.save(update_fields=["current_hp", "status"])

        with self.assertRaises(services.DungeonError):
            services.move_player(run, "up", rng=AlwaysEncounter())

    def test_refreshing_does_not_heal_or_revive(self):
        run = self.start()
        self.engage(run)
        self.answer_wrongly(run)
        self.finish_current_battle(run)

        reloaded = self.reload(run)
        state = services.serialize_run(reloaded)

        self.assertLess(state["hp"]["current"], state["hp"]["max"])
        self.assertEqual(sum(1 for e in state["enemies"] if e["is_defeated"]), 1)


class DropAndInventoryTests(DungeonTestCase):
    def test_every_defeated_enemy_drops_exactly_one_key_piece(self):
        run = self.start()
        for expected in range(1, run.enemies.count() + 1):
            self.defeat(run)
            self.assertEqual(DungeonInventory.objects.get(run=run).key_pieces, expected)

    def test_bonus_drops_only_ever_come_from_the_table(self):
        run = self.start(seed=777)
        enemy_count = run.enemies.count()
        self.clear_all_enemies(run)

        bag = services.serialize_inventory(run)
        self.assertEqual(bag["key_pieces"], enemy_count)
        # At most one bonus item per enemy, and only the two table items exist.
        self.assertLessEqual(bag["skip_potions"] + bag["health_potions"], enemy_count)

    def test_a_drop_reports_what_it_rolled(self):
        run = self.start()
        result = self.defeat(run)

        drops = result["drops"]
        self.assertEqual(drops["key_pieces"], 1)
        self.assertIn(drops["rolled"], {ITEM_SKIP_POTION, ITEM_HEALTH_POTION, ITEM_NOTHING})

    def test_potions_never_exceed_the_configured_cap(self):
        run = self.start()
        rules = services.rules_for(run)
        run = self.set_inventory(
            run,
            health_potions=rules.max_potions_held,
            skip_potions=rules.max_potions_held,
        )

        self.clear_all_enemies(run)

        inventory = DungeonInventory.objects.get(run=run)
        self.assertEqual(inventory.health_potions, rules.max_potions_held)
        self.assertEqual(inventory.skip_potions, rules.max_potions_held)

    def test_health_potion_heals_up_to_the_cap(self):
        run = self.start()
        rules = services.rules_for(run)
        run.current_hp = 1
        run.save(update_fields=["current_hp"])
        run = self.set_inventory(run, health_potions=2)

        result = services.use_item(run, ITEM_HEALTH_POTION)

        self.assertEqual(
            result["hp"]["current"], min(1 + rules.health_potion_heal, run.max_hp)
        )
        self.assertEqual(DungeonInventory.objects.get(run=run).health_potions, 1)

    def test_health_potion_at_full_health_is_refused_and_not_consumed(self):
        run = self.start()
        run = self.set_inventory(run, health_potions=1)

        with self.assertRaises(services.DungeonError):
            services.use_item(run, ITEM_HEALTH_POTION)

        self.assertEqual(DungeonInventory.objects.get(run=run).health_potions, 1)

    def test_using_an_item_you_do_not_have_is_refused(self):
        run = self.start()
        self.engage(run)

        with self.assertRaises(services.DungeonError):
            services.use_item(run, ITEM_SKIP_POTION)

    def test_skip_potion_spends_the_question_without_damage(self):
        run = self.start()
        run = self.set_inventory(run, skip_potions=1)
        self.engage(run)

        enemy_hp_before = run.active_enemy.hp
        skipped_question = services.current_question(run.active_enemy)

        result = services.use_item(run, ITEM_SKIP_POTION)

        self.assertEqual(result["outcome"], services.OUTCOME_SKIPPED)
        self.assertEqual(result["damage_to_player"], 0)
        self.assertEqual(result["damage_to_enemy"], 0)
        self.assertEqual(result["enemy"]["hp"], enemy_hp_before)
        self.assertEqual(result["hp"]["current"], run.max_hp)

        run.active_enemy.refresh_from_db()
        self.assertIn(skipped_question.id, run.active_enemy.answered_question_ids)
        self.assertNotEqual(
            services.current_question(run.active_enemy).id, skipped_question.id
        )


class ExitGateTests(DungeonTestCase):
    def test_door_stays_locked_until_every_enemy_falls(self):
        run = self.start()
        self.assertFalse(services.is_door_unlocked(run))

        enemies = list(run.enemies.all())
        for enemy in enemies[:-1]:
            self.defeat(run, enemy)
            self.assertFalse(
                services.is_door_unlocked(run),
                "door opened before the last key piece dropped",
            )

        self.defeat(run, enemies[-1])
        self.assertTrue(services.is_door_unlocked(run))

    def test_exiting_a_locked_room_is_refused(self):
        run = self.start()
        self.walk_to_door(run)

        with self.assertRaises(services.DungeonError):
            services.attempt_exit(run)

        run.refresh_from_db()
        self.assertEqual(run.status, DungeonRun.STATUS_IN_PROGRESS)

    def test_exit_requires_standing_at_the_door(self):
        run = self.start()
        self.clear_all_enemies(run)

        with self.assertRaises(services.DungeonError):
            services.attempt_exit(run)

    def test_unlocked_door_becomes_walkable(self):
        run = self.start()
        self.clear_all_enemies(run)
        grid = run.room_data["grid"]
        door = run.room_data["door"]

        self.assertFalse(rooms.is_walkable(grid, door["x"], door["y"], door_unlocked=False))
        self.assertTrue(rooms.is_walkable(grid, door["x"], door["y"], door_unlocked=True))

    def test_walking_through_the_open_door_clears_the_run(self):
        run = self.start()
        self.clear_all_enemies(run)
        door = run.room_data["door"]
        run.player_x, run.player_y = door["x"], door["y"] + 1
        run.save(update_fields=["player_x", "player_y"])

        result = services.move_player(run, "up", rng=NeverEncounter())

        self.assertTrue(result["moved"])
        self.assertTrue(result["exit"]["cleared"])
        run.refresh_from_db()
        self.assertEqual(run.status, DungeonRun.STATUS_CLEARED)


class XpAwardTests(DungeonTestCase):
    def test_clearing_awards_xp_through_the_ledger(self):
        run = self.start()
        self.clear_all_enemies(run)
        self.walk_to_door(run)

        result = services.attempt_exit(run)
        profile = self.user.userprofile
        profile.refresh_from_db()

        self.assertGreater(result["xp_awarded"], 0)
        self.assertEqual(profile.total_xp, result["xp_awarded"])

        ledger = XPTransaction.objects.filter(user=self.user)
        self.assertEqual(ledger.count(), 1)
        self.assertEqual(ledger.first().amount, result["xp_awarded"])
        self.assertEqual(
            profile.total_xp,
            sum(ledger.values_list("amount", flat=True)),
            "cached total drifted from the ledger",
        )

    def test_xp_is_awarded_once_even_if_finish_is_called_again(self):
        run = self.start()
        self.clear_all_enemies(run)
        self.walk_to_door(run)

        first = services.attempt_exit(run)
        self.assertTrue(first["xp_newly_awarded"])

        run.refresh_from_db()
        second = services.finish_run(run, cleared=True)

        self.assertFalse(second["xp_newly_awarded"])
        self.assertEqual(second["xp_awarded"], first["xp_awarded"])
        self.assertEqual(XPTransaction.objects.filter(user=self.user).count(), 1)

        profile = self.user.userprofile
        profile.refresh_from_db()
        self.assertEqual(profile.total_xp, first["xp_awarded"])

    def test_clearing_records_study_activity_for_the_streak(self):
        run = self.start()
        self.clear_all_enemies(run)
        self.walk_to_door(run)
        services.attempt_exit(run)

        profile = self.user.userprofile
        profile.refresh_from_db()
        self.assertEqual(profile.streak_days, 1)
        self.assertIsNotNone(profile.last_study_date)

    def test_a_dungeon_run_never_writes_a_quiz_attempt(self):
        run = self.start()
        self.clear_all_enemies(run)
        self.walk_to_door(run)
        services.attempt_exit(run)

        self.assertFalse(QuizAttempt.objects.exists())


class SerializationSafetyTests(DungeonTestCase):
    def collect_strings(self, node, found=None):
        found = found if found is not None else []
        if isinstance(node, dict):
            for key, value in node.items():
                found.append(str(key))
                self.collect_strings(value, found)
        elif isinstance(node, (list, tuple)):
            for value in node:
                self.collect_strings(value, found)
        else:
            found.append(str(node))
        return found

    def test_run_state_never_carries_correct_answers(self):
        run = self.start()
        self.engage(run)
        state = services.serialize_run(run)
        blob = " ".join(self.collect_strings(state))

        self.assertNotIn("is_correct", blob)
        self.assertNotIn("answer_data", blob)
        self.assertNotIn("accepted_answers", blob)
        self.assertNotIn("correct_choice_id", blob)

        question = state["battle"]["question"]
        self.assertEqual(set(question) & {"choices"}, {"choices"})
        for choice in question["choices"]:
            self.assertEqual(set(choice), {"id", "text"})

        # The explanation can hint at the answer, so it is withheld until grading.
        self.assertNotIn("explanation", question)

    def test_identification_answers_are_not_serialized(self):
        course = Course.objects.create(user=self.user, title="Protocols")
        chapter = Chapter.objects.create(course=course, order=1, title="Tagging")
        quiz = Quiz.objects.create(chapter=chapter, title="Tagging Quiz")
        for index in range(5):
            Question.objects.create(
                quiz=quiz,
                order=index + 1,
                question_type="identification",
                text=f"Name the protocol {index}.",
                answer_data={"accepted_answers": ["IEEE 802.1Q", "dot1q"]},
            )

        run = services.start_or_resume_run(self.user, quiz, seed=5)
        self.engage(run)
        blob = " ".join(self.collect_strings(services.serialize_run(run)))

        self.assertNotIn("802.1Q", blob)
        self.assertNotIn("dot1q", blob)

    def test_enumeration_exposes_only_the_number_of_blanks(self):
        course = Course.objects.create(user=self.user, title="Layers")
        chapter = Chapter.objects.create(course=course, order=1, title="OSI")
        quiz = Quiz.objects.create(chapter=chapter, title="OSI Quiz")
        for index in range(5):
            Question.objects.create(
                quiz=quiz,
                order=index + 1,
                question_type="enumeration",
                text=f"List the parts {index}.",
                answer_data={
                    "expected_items": [
                        {"canonical": "Physical"},
                        {"canonical": "Data Link"},
                    ],
                    "order_matters": True,
                },
            )

        run = services.start_or_resume_run(self.user, quiz, seed=5)
        self.engage(run)
        question = services.serialize_run(run)["battle"]["question"]

        self.assertEqual(question["expected_item_count"], 2)
        self.assertTrue(question["order_matters"])
        blob = " ".join(self.collect_strings(question))
        self.assertNotIn("Physical", blob)
        self.assertNotIn("Data Link", blob)

    def test_feedback_after_grading_does_reveal_the_answer(self):
        """The flip side: once an answer is spent, the player is owed the truth."""
        run = self.start()
        self.engage(run)
        result = self.answer_wrongly(run)

        self.assertIn("correct_choice_text", result["feedback"])
        self.assertTrue(result["feedback"]["correct_choice_text"])
        self.assertTrue(result["feedback"]["explanation"])
