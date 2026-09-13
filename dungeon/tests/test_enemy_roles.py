from courses.models import Question
from dungeon import services
from dungeon.combat_config import (
    BASE_DAMAGE,
    COMBO_DAMAGE,
    GUARDIAN_ARMOR_REQUIRED_STREAK,
)
from dungeon.models import DungeonEnemy
from dungeon.tests.test_services import (
    DungeonTestCase,
    correct_choice_id,
    wrong_choice_id,
)


class EnemyRoleAndArmorTests(DungeonTestCase):
    def test_enemy_role_assignment_in_run(self):
        run = self.start()
        enemies = list(run.enemies.order_by("enemy_index"))
        self.assertGreaterEqual(len(enemies), 2)

        # Grunts
        for enemy in enemies[:-1]:
            self.assertEqual(enemy.role, DungeonEnemy.ROLE_GRUNT)
            self.assertTrue(enemy.name.startswith("Grunt "))
            self.assertFalse(enemy.armor_active)

        # Guardian is the final enemy
        guardian = enemies[-1]
        self.assertEqual(guardian.role, DungeonEnemy.ROLE_GUARDIAN)
        self.assertEqual(guardian.name, "Guardian")
        self.assertTrue(guardian.armor_active)

    def test_guardian_armor_deflects_first_hit_and_breaks_on_second(self):
        run = self.start()
        guardian = run.enemies.filter(role=DungeonEnemy.ROLE_GUARDIAN).first()
        self.assertIsNotNone(guardian)

        self.engage(run, enemy=guardian)
        initial_hp = guardian.hp

        # 1st correct answer: Deflected by Guardian Armor
        res1 = self.answer_correctly(run)
        self.assertEqual(res1["damage_to_enemy"], 0)
        self.assertEqual(res1["damage_dealt"], 0)
        self.assertEqual(res1["combat_event"]["code"], "armor_deflect")
        self.assertIn("Armor Deflected", res1["combat_event"]["label"])
        self.assertEqual(res1["combo"]["count"], 1)

        guardian.refresh_from_db()
        self.assertEqual(guardian.hp, initial_hp)
        self.assertTrue(guardian.armor_active)
        self.assertEqual(guardian.current_combo, 1)

        # 2nd correct answer: Armor broken! Dealt 1 damage
        res2 = self.answer_correctly(run)
        self.assertEqual(res2["damage_to_enemy"], BASE_DAMAGE)
        self.assertEqual(res2["damage_dealt"], BASE_DAMAGE)
        self.assertEqual(res2["combat_event"]["code"], "armor_break")
        self.assertIn("Cracked", res2["combat_event"]["label"])
        self.assertEqual(res2["combo"]["count"], 2)

        guardian.refresh_from_db()
        self.assertEqual(guardian.hp, initial_hp - BASE_DAMAGE)
        self.assertFalse(guardian.armor_active)
        self.assertEqual(guardian.current_combo, 2)

        # 3rd correct answer: Power Strike against unarmored guardian
        res3 = self.answer_correctly(run)
        self.assertEqual(res3["damage_to_enemy"], COMBO_DAMAGE)
        self.assertEqual(res3["combat_event"]["code"], "power_strike")

        guardian.refresh_from_db()
        self.assertEqual(guardian.hp, initial_hp - BASE_DAMAGE - COMBO_DAMAGE)

    def test_wrong_answer_resets_streak_without_breaking_armor(self):
        run = self.start()
        guardian = run.enemies.filter(role=DungeonEnemy.ROLE_GUARDIAN).first()
        self.engage(run, enemy=guardian)

        # Q1: Correct -> deflected, streak 1
        res1 = self.answer_correctly(run)
        self.assertEqual(res1["combat_event"]["code"], "armor_deflect")
        guardian.refresh_from_db()
        self.assertEqual(guardian.current_combo, 1)
        self.assertTrue(guardian.armor_active)

        # Q2: Wrong -> player takes damage, combo resets to 0, armor stays active
        enemy = guardian
        qid = services.current_question(enemy).id
        wid = wrong_choice_id(qid)
        res_wrong = services.answer_question(run, qid, {"choice_id": wid})

        self.assertEqual(res_wrong["outcome"], "incorrect")
        self.assertGreater(res_wrong["damage_to_player"], 0)
        self.assertEqual(res_wrong["damage_to_enemy"], 0)

        guardian.refresh_from_db()
        self.assertEqual(guardian.current_combo, 0)
        self.assertTrue(guardian.armor_active)

        # Q3: Next correct answer deflects again because streak is only 1
        res3 = self.answer_correctly(run)
        self.assertEqual(res3["combat_event"]["code"], "armor_deflect")
        guardian.refresh_from_db()
        self.assertEqual(guardian.current_combo, 1)
        self.assertTrue(guardian.armor_active)

    def test_power_strike_shatters_armor_immediately(self):
        run = self.start()
        guardian = run.enemies.filter(role=DungeonEnemy.ROLE_GUARDIAN).first()
        self.engage(run, enemy=guardian)
        guardian.current_combo = 2
        guardian.save(update_fields=["current_combo"])

        initial_hp = guardian.hp

        # Next correct answer hits threshold 3 -> Power Strike breaks armor & deals COMBO_DAMAGE
        res = self.answer_correctly(run)
        self.assertEqual(res["damage_to_enemy"], COMBO_DAMAGE)
        self.assertEqual(res["combat_event"]["code"], "power_strike")
        self.assertIn("Armor Shattered", res["combat_event"]["label"])

        guardian.refresh_from_db()
        self.assertEqual(guardian.hp, initial_hp - COMBO_DAMAGE)
        self.assertFalse(guardian.armor_active)

    def test_serialization_enemy_and_battle(self):
        run = self.start()
        grunt = run.enemies.filter(role=DungeonEnemy.ROLE_GRUNT).first()
        guardian = run.enemies.filter(role=DungeonEnemy.ROLE_GUARDIAN).first()

        s_grunt = services.serialize_enemy(grunt)
        self.assertEqual(s_grunt["role"], DungeonEnemy.ROLE_GRUNT)
        self.assertEqual(s_grunt["name"], grunt.name)
        self.assertFalse(s_grunt["armor_active"])

        s_guardian = services.serialize_enemy(guardian)
        self.assertEqual(s_guardian["role"], DungeonEnemy.ROLE_GUARDIAN)
        self.assertEqual(s_guardian["name"], "Guardian")
        self.assertTrue(s_guardian["armor_active"])

        b_grunt = services.serialize_battle(run, grunt)
        self.assertEqual(b_grunt["portrait_key"], "enemy_grunt_portrait")

        b_guardian = services.serialize_battle(run, guardian)
        self.assertEqual(b_guardian["portrait_key"], "enemy_guardian_portrait")
