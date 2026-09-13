from courses.models import Choice, Question
from dungeon import services
from dungeon.combat_config import (
    BASE_DAMAGE,
    COMBO_DAMAGE,
    COMBO_THRESHOLD,
)
from dungeon.models import DungeonEnemy
from dungeon.tests.test_services import (
    DungeonTestCase,
    correct_choice_id,
    wrong_choice_id,
)


class ComboMechanicsTests(DungeonTestCase):
    def test_first_correct_answer_advances_combo_and_deals_base_damage(self):
        run = self.start()
        self.engage(run)
        enemy = run.active_enemy
        self.assertEqual(enemy.hp, 5)
        self.assertEqual(enemy.current_combo, 0)

        result = self.answer_correctly(run)

        self.assertEqual(result["damage_to_enemy"], BASE_DAMAGE)
        self.assertEqual(result["damage_dealt"], BASE_DAMAGE)
        self.assertEqual(
            result["combo"],
            {"current": 1, "threshold": COMBO_THRESHOLD, "is_power_strike": False},
        )
        self.assertEqual(result["feedback"].get("combat_message"), "1 damage")

        enemy.refresh_from_db()
        self.assertEqual(enemy.hp, 4)
        self.assertEqual(enemy.current_combo, 1)

    def test_second_correct_answer_triggers_power_strike_and_resets_counter(self):
        run = self.start()
        self.engage(run)
        enemy = run.active_enemy

        # Q1: combo 1, 1 dmg
        self.answer_correctly(run)

        # Q2: combo threshold reached -> Power Strike, 2 dmg
        result = self.answer_correctly(run)

        self.assertEqual(result["damage_to_enemy"], COMBO_DAMAGE)
        self.assertEqual(result["damage_dealt"], COMBO_DAMAGE)
        self.assertTrue(result["combo"]["is_power_strike"])
        self.assertEqual(result["combo"]["current"], COMBO_THRESHOLD)
        self.assertEqual(result["feedback"].get("combat_message"), "Power Strike! 2 damage!")

        enemy.refresh_from_db()
        self.assertEqual(enemy.hp, 2)
        # Counter resets to 0 in database so next strike requires 2 more answers
        self.assertEqual(enemy.current_combo, 0)

        # In the next battle turn payload (for Q3), battle combo reflects reset state
        self.assertIsNotNone(result["battle"])
        self.assertEqual(
            result["battle"]["combo"],
            {"current": 0, "threshold": COMBO_THRESHOLD, "is_power_strike": False},
        )

    def test_full_sequence_defeats_five_hp_enemy_in_four_questions(self):
        run = self.start()
        self.engage(run)
        enemy = run.active_enemy
        self.assertEqual(enemy.hp, 5)

        # Q1: 1 dmg -> enemy HP 4, db combo 1
        res1 = self.answer_correctly(run)
        self.assertEqual(res1["damage_dealt"], 1)
        self.assertFalse(res1["combo"]["is_power_strike"])

        # Q2: 2 dmg (Power Strike) -> enemy HP 2, db combo resets to 0
        res2 = self.answer_correctly(run)
        self.assertEqual(res2["damage_dealt"], 2)
        self.assertTrue(res2["combo"]["is_power_strike"])

        # Q3: 1 dmg -> enemy HP 1, db combo 1
        res3 = self.answer_correctly(run)
        self.assertEqual(res3["damage_dealt"], 1)
        self.assertFalse(res3["combo"]["is_power_strike"])

        # Q4: 2 dmg (Power Strike) -> enemy HP 0, defeated!
        res4 = self.answer_correctly(run)
        self.assertEqual(res4["damage_dealt"], 2)
        self.assertTrue(res4["combo"]["is_power_strike"])
        self.assertTrue(res4["enemy_defeated"])

        enemy.refresh_from_db()
        self.assertEqual(enemy.hp, 0)
        self.assertTrue(enemy.is_defeated)
        self.assertEqual(len(enemy.answered_question_ids), 4)

    def test_wrong_answer_resets_combo_to_zero(self):
        run = self.start()
        self.engage(run)
        enemy = run.active_enemy

        # Q1: correct -> combo 1
        self.answer_correctly(run)
        enemy.refresh_from_db()
        self.assertEqual(enemy.current_combo, 1)

        # Q2: wrong -> combo resets to 0, player takes damage, enemy takes 0
        result = self.answer_wrongly(run)
        self.assertEqual(result["damage_to_enemy"], 0)
        self.assertEqual(
            result["combo"],
            {"current": 0, "threshold": COMBO_THRESHOLD, "is_power_strike": False},
        )

        enemy.refresh_from_db()
        self.assertEqual(enemy.current_combo, 0)
        self.assertEqual(enemy.hp, 4)

        # Q3: next correct answer starts streak anew at 1
        result = self.answer_correctly(run)
        self.assertEqual(result["damage_to_enemy"], BASE_DAMAGE)
        self.assertEqual(result["combo"]["current"], 1)
        self.assertFalse(result["combo"]["is_power_strike"])

    def test_partial_credit_preserves_combo_streak(self):
        run = self.start()
        self.engage(run)
        enemy = run.active_enemy

        # Q1: correct -> combo 1
        self.answer_correctly(run)
        enemy.refresh_from_db()
        self.assertEqual(enemy.current_combo, 1)

        # Set up an enumeration question with partial answer
        qid = services.current_question(enemy).id
        q = Question.objects.get(pk=qid)
        q.question_type = "enumeration"
        q.answer_data = {
            "expected_items": [
                {"canonical": "Alpha", "accepted_variants": []},
                {"canonical": "Beta", "accepted_variants": []},
            ]
        }
        q.save()

        # Submit only one item -> partial credit
        result = services.answer_question(run, qid, {"items": ["Alpha"]})
        self.assertEqual(result["outcome"], services.OUTCOME_PARTIAL)
        self.assertEqual(result["combo"]["current"], 1)
        self.assertFalse(result["combo"]["is_power_strike"])

        enemy.refresh_from_db()
        self.assertEqual(enemy.current_combo, 1)

    def test_skip_potion_preserves_combo_streak(self):
        run = self.start()
        self.set_inventory(run, skip_potions=2)
        self.engage(run)
        enemy = run.active_enemy

        # Q1: correct -> combo 1
        self.answer_correctly(run)
        enemy.refresh_from_db()
        self.assertEqual(enemy.current_combo, 1)

        # Use skip potion
        result = services.use_item(run, "skip_potion")
        self.assertEqual(result["item"], "skip_potion")
        self.assertEqual(result["combo"]["current"], 1)

        enemy.refresh_from_db()
        self.assertEqual(enemy.current_combo, 1)

    def test_mid_encounter_refresh_preserves_combo_count(self):
        run = self.start()
        self.engage(run)

        # Q1: correct -> combo 1
        self.answer_correctly(run)

        # Simulate page refresh by fetching serialized run and battle
        reloaded_run = self.reload(run)
        serialized = services.serialize_run(reloaded_run)
        self.assertIsNotNone(serialized["battle"])
        self.assertEqual(
            serialized["battle"]["combo"],
            {"current": 1, "threshold": COMBO_THRESHOLD, "is_power_strike": False},
        )

    def test_duplicate_answer_submission_cannot_advance_combo_twice(self):
        run = self.start()
        self.engage(run)
        enemy = run.active_enemy

        qid = services.current_question(enemy).id
        choice_id = correct_choice_id(qid)

        # First submission succeeds
        services.answer_question(run, qid, {"choice_id": choice_id})
        enemy.refresh_from_db()
        self.assertEqual(enemy.current_combo, 1)
        self.assertEqual(enemy.hp, 4)

        # Replayed / duplicate submission with same question_id fails
        with self.assertRaises(services.DungeonError):
            services.answer_question(run, qid, {"choice_id": choice_id})

        # State has not been corrupted or double-incremented
        enemy.refresh_from_db()
        self.assertEqual(enemy.current_combo, 1)
        self.assertEqual(enemy.hp, 4)
