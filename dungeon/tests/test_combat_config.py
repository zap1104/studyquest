from random import Random

from django.test import SimpleTestCase

from dungeon import combat_config
from dungeon.combat_config import (
    COMBAT_RULES,
    DROP_TABLE,
    ITEM_HEALTH_POTION,
    ITEM_NOTHING,
    ITEM_SKIP_POTION,
    MAX_ENEMIES,
    _scale_hp_for_quiz_length,
    calculate_run_xp,
    enemy_count_for_questions,
    minimum_questions_required,
    resolve_combat_rules,
    roll_item_drop,
)


class CombatRuleResolutionTests(SimpleTestCase):
    def test_free_plan_starts_with_five_hp(self):
        self.assertEqual(resolve_combat_rules(plan="free").base_player_hp, 5)

    def test_plus_plan_starts_with_fifteen_hp(self):
        self.assertEqual(resolve_combat_rules(plan="plus").base_player_hp, 15)

    def test_unknown_plan_falls_back_to_free_rules(self):
        self.assertEqual(
            resolve_combat_rules(plan="enterprise-unicorn"),
            resolve_combat_rules(plan="free"),
        )
        self.assertEqual(resolve_combat_rules(plan=None).key, "free")

    def test_enemy_hp_defaults_to_questions_per_enemy(self):
        for rules in COMBAT_RULES.values():
            self.assertEqual(rules.enemy_hp, rules.questions_per_enemy)

    def test_rules_are_immutable(self):
        rules = resolve_combat_rules(plan="free")
        with self.assertRaises(Exception):
            rules.base_player_hp = 99


class QuizLengthScalingHookTests(SimpleTestCase):
    """The hook exists and is wired up, but must not change HP yet."""

    def test_stub_returns_hp_unchanged(self):
        for question_count in (0, 5, 40, 500):
            self.assertEqual(
                _scale_hp_for_quiz_length(7, quiz=None, question_count=question_count),
                7,
            )

    def test_quiz_and_question_count_do_not_move_hp_yet(self):
        baseline = resolve_combat_rules(plan="plus").base_player_hp
        for question_count in (0, 5, 50, 1000):
            self.assertEqual(
                resolve_combat_rules(
                    plan="plus", quiz=object(), question_count=question_count
                ).base_player_hp,
                baseline,
            )


class RoomShapeConfigTests(SimpleTestCase):
    def test_enemy_count_is_questions_divided_by_pack_size(self):
        rules = resolve_combat_rules(plan="free")
        self.assertEqual(enemy_count_for_questions(4, rules=rules), 0)
        self.assertEqual(enemy_count_for_questions(5, rules=rules), 1)
        self.assertEqual(enemy_count_for_questions(12, rules=rules), 2)

    def test_enemy_count_is_capped(self):
        rules = resolve_combat_rules(plan="free")
        self.assertEqual(enemy_count_for_questions(9999, rules=rules), MAX_ENEMIES)

    def test_minimum_questions_comes_from_the_rules(self):
        rules = resolve_combat_rules(plan="free")
        self.assertEqual(minimum_questions_required(rules), rules.questions_per_enemy)


class DropTableTests(SimpleTestCase):
    def test_drop_roll_is_reproducible_for_a_seed(self):
        first = [roll_item_drop(Random(1234)) for _ in range(5)]
        second = [roll_item_drop(Random(1234)) for _ in range(5)]
        self.assertEqual(first, second)

    def test_every_roll_returns_a_known_table_entry(self):
        known = {item_key for item_key, _ in DROP_TABLE}
        rng = Random(7)
        for _ in range(200):
            self.assertIn(roll_item_drop(rng), known)

    def test_weighting_is_respected_over_many_seeded_rolls(self):
        rng = Random(2024)
        rolls = [roll_item_drop(rng) for _ in range(6000)]

        skip_share = rolls.count(ITEM_SKIP_POTION) / len(rolls)
        health_share = rolls.count(ITEM_HEALTH_POTION) / len(rolls)
        nothing_share = rolls.count(ITEM_NOTHING) / len(rolls)

        self.assertAlmostEqual(skip_share, 0.30, delta=0.03)
        self.assertAlmostEqual(health_share, 0.30, delta=0.03)
        self.assertAlmostEqual(nothing_share, 0.40, delta=0.03)

    def test_zero_weight_table_degrades_to_nothing(self):
        self.assertEqual(
            roll_item_drop(Random(1), drop_table=[(ITEM_SKIP_POTION, 0)]),
            ITEM_NOTHING,
        )

    def test_single_item_table_always_drops_that_item(self):
        rng = Random(3)
        table = [(ITEM_HEALTH_POTION, 10)]
        for _ in range(25):
            self.assertEqual(roll_item_drop(rng, drop_table=table), ITEM_HEALTH_POTION)


class RunXpTests(SimpleTestCase):
    def test_failed_run_earns_nothing(self):
        self.assertEqual(
            calculate_run_xp(cleared=False, enemies_defeated=3, hp_remaining=4), 0
        )

    def test_cleared_run_uses_the_configured_formula(self):
        expected = (
            combat_config.XP_CLEAR_BASE
            + combat_config.XP_PER_ENEMY_DEFEATED * 2
            + combat_config.XP_PER_SURVIVING_HP * 3
        )
        self.assertEqual(
            calculate_run_xp(cleared=True, enemies_defeated=2, hp_remaining=3), expected
        )

    def test_negative_inputs_cannot_reduce_the_base_award(self):
        self.assertEqual(
            calculate_run_xp(cleared=True, enemies_defeated=-5, hp_remaining=-5),
            combat_config.XP_CLEAR_BASE,
        )


class PresentationScaleTests(SimpleTestCase):
    def test_icon_size_is_derived_from_tile_size(self):
        self.assertEqual(combat_config.ICON_SIZE, combat_config.TILE_SIZE // 2)

    def test_board_scale_bounds_are_integers(self):
        self.assertIsInstance(combat_config.MIN_BOARD_SCALE, int)
        self.assertIsInstance(combat_config.MAX_BOARD_SCALE, int)
        self.assertLessEqual(combat_config.MIN_BOARD_SCALE, combat_config.MAX_BOARD_SCALE)
