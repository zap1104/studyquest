from courses.models import Question
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
    def test_first_two_correct_answers_build_combo(self):
        run = self.start()
        self.engage(run)
        enemy = run.active_enemy
        self.assertEqual(enemy.hp, 5)
        self.assertEqual(enemy.current_combo, 0)

        # Q1: 1 dmg, combo count 1/3
        res1 = self.answer_correctly(run)
        self.assertEqual(res1["damage_to_enemy"], BASE_DAMAGE)
        self.assertEqual(res1["damage_dealt"], BASE_DAMAGE)
        self.assertEqual(res1["combat_event"]["code"], "direct_hit")
        self.assertEqual(res1["combo"]["count"], 1)
        self.assertEqual(res1["combo"]["remaining"], 2)
        self.assertEqual(res1["combo"]["threshold"], 3)
        self.assertFalse(res1["combo"]["is_power_strike"])

        enemy.refresh_from_db()
        self.assertEqual(enemy.hp, 4)
        self.assertEqual(enemy.current_combo, 1)

        # Q2: 1 dmg, combo count 2/3
        res2 = self.answer_correctly(run)
        self.assertEqual(res2["damage_to_enemy"], BASE_DAMAGE)
        self.assertEqual(res2["damage_dealt"], BASE_DAMAGE)
        self.assertEqual(res2["combat_event"]["code"], "direct_hit")
        self.assertEqual(res2["combo"]["count"], 2)
        self.assertEqual(res2["combo"]["remaining"], 1)
        self.assertFalse(res2["combo"]["is_power_strike"])

        enemy.refresh_from_db()
        self.assertEqual(enemy.hp, 3)
        self.assertEqual(enemy.current_combo, 2)

    def test_third_correct_answer_triggers_power_strike_and_resets_counter(self):
        run = self.start()
        self.engage(run)
        enemy = run.active_enemy

        self.answer_correctly(run)  # Q1: count 1
        self.answer_correctly(run)  # Q2: count 2

        # Q3: threshold 3 reached -> Power Strike, 2 dmg
        res3 = self.answer_correctly(run)
        self.assertEqual(res3["damage_to_enemy"], COMBO_DAMAGE)
        self.assertEqual(res3["damage_dealt"], COMBO_DAMAGE)
        self.assertEqual(res3["combat_event"]["code"], "power_strike")
        self.assertEqual(res3["combat_event"]["damage"], COMBO_DAMAGE)
        self.assertTrue(res3["combo"]["is_power_strike"])
        # Counter resets to 0 after strike
        self.assertEqual(res3["combo"]["count"], 0)
        self.assertEqual(res3["combo"]["remaining"], 3)
        self.assertEqual(res3["feedback"].get("combat_message"), "Power Strike! 2 damage!")

        enemy.refresh_from_db()
        self.assertEqual(enemy.hp, 1)  # 5 - 1 - 1 - 2 = 1 HP remaining
        self.assertEqual(enemy.current_combo, 0)

        # Next battle turn payload shows reset combo
        self.assertIsNotNone(res3["battle"])
        self.assertEqual(res3["battle"]["combo"]["count"], 0)
        self.assertEqual(res3["battle"]["combo"]["remaining"], 3)

    def test_full_sequence_defeats_five_hp_enemy_in_four_questions(self):
        run = self.start()
        self.engage(run)
        enemy = run.active_enemy
        self.assertEqual(enemy.hp, 5)

        # Q1: 1 dmg -> enemy HP 4, combo 1/3
        res1 = self.answer_correctly(run)
        self.assertEqual(res1["damage_dealt"], 1)
        self.assertEqual(res1["combo"]["count"], 1)

        # Q2: 1 dmg -> enemy HP 3, combo 2/3
        res2 = self.answer_correctly(run)
        self.assertEqual(res2["damage_dealt"], 1)
        self.assertEqual(res2["combo"]["count"], 2)

        # Q3: 2 dmg (Power Strike) -> enemy HP 1, combo resets to 0/3
        res3 = self.answer_correctly(run)
        self.assertEqual(res3["damage_dealt"], 2)
        self.assertEqual(res3["combat_event"]["code"], "power_strike")
        self.assertEqual(res3["combo"]["count"], 0)

        # Q4: 1 dmg -> enemy HP 0, enemy defeated!
        res4 = self.answer_correctly(run)
        self.assertEqual(res4["damage_dealt"], 1)
        self.assertTrue(res4["enemy_defeated"])

        enemy.refresh_from_db()
        self.assertEqual(enemy.hp, 0)
        self.assertTrue(enemy.is_defeated)
        self.assertEqual(len(enemy.answered_question_ids), 4)

    def test_wrong_answer_resets_combo_to_zero(self):
        run = self.start()
        self.engage(run)
        enemy = run.active_enemy

        # Q1: correct -> combo 1/3
        self.answer_correctly(run)
        enemy.refresh_from_db()
        self.assertEqual(enemy.current_combo, 1)

        # Q2: wrong -> combo resets to 0/3
        result = self.answer_wrongly(run)
        self.assertEqual(result["damage_to_enemy"], 0)
        self.assertEqual(result["combo"]["count"], 0)
        self.assertEqual(result["combo"]["remaining"], 3)

        enemy.refresh_from_db()
        self.assertEqual(enemy.current_combo, 0)
        self.assertEqual(enemy.hp, 4)

        # Q3: next correct answer starts streak anew at 1
        result = self.answer_correctly(run)
        self.assertEqual(result["damage_to_enemy"], BASE_DAMAGE)
        self.assertEqual(result["combo"]["count"], 1)
        self.assertFalse(result["combo"]["is_power_strike"])

    def test_partial_credit_preserves_combo_streak(self):
        run = self.start()
        self.engage(run)
        enemy = run.active_enemy

        # Q1: correct -> combo 1/3
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
        self.assertEqual(result["combo"]["count"], 1)
        self.assertEqual(result["combo"]["remaining"], 2)
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
        self.assertEqual(result["combo"]["count"], 1)

        enemy.refresh_from_db()
        self.assertEqual(enemy.current_combo, 1)

    def test_mid_encounter_refresh_preserves_combo_count(self):
        run = self.start()
        self.engage(run)

        # Q1: correct -> combo 1
        self.answer_correctly(run)
        # Q2: correct -> combo 2
        self.answer_correctly(run)

        # Simulate page refresh by fetching serialized run and battle
        reloaded_run = self.reload(run)
        serialized = services.serialize_run(reloaded_run)
        self.assertIsNotNone(serialized["battle"])
        self.assertEqual(serialized["battle"]["combo"]["count"], 2)
        self.assertEqual(serialized["battle"]["combo"]["remaining"], 1)
        self.assertEqual(serialized["battle"]["combo"]["threshold"], 3)

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
