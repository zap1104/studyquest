"""Every tunable number for Dungeon Quest lives here.

Nothing in ``dungeon/views.py``, ``dungeon/services.py``, the templates or the
JS is allowed to invent its own HP, damage, drop-rate, XP or pixel value — they
all read from this module (the templates receive the values through the view
context, the JS through the bootstrap payload).  Balancing the mode should mean
editing this file and nothing else.
"""

from dataclasses import dataclass, replace
from random import Random


# --------------------------------------------------
# 1. COMBAT RULES
# --------------------------------------------------
@dataclass(frozen=True)
class CombatRules:
    """The resolved rule-set a single run is played under.

    ``enemy_hp`` may be omitted at declaration time, in which case it defaults
    to ``questions_per_enemy`` — one point of damage per correct answer means a
    flawless battle drops the enemy exactly as its last question is answered.
    """

    key: str
    base_player_hp: int
    damage_per_wrong_answer: int
    questions_per_enemy: int
    encounter_chance: float
    health_potion_heal: int
    max_potions_held: int
    enemy_hp: int = None

    def __post_init__(self):
        if self.enemy_hp is None:
            object.__setattr__(self, "enemy_hp", self.questions_per_enemy)


DEFAULT_RULES_KEY = "free"

COMBAT_RULES = {
    "free": CombatRules(
        key="free",
        base_player_hp=5,
        damage_per_wrong_answer=1,
        questions_per_enemy=5,
        encounter_chance=0.35,
        health_potion_heal=2,
        max_potions_held=3,
    ),
    "plus": CombatRules(
        key="plus",
        base_player_hp=15,
        damage_per_wrong_answer=1,
        questions_per_enemy=5,
        encounter_chance=0.30,
        health_potion_heal=3,
        max_potions_held=5,
    ),
}


def _scale_hp_for_quiz_length(base_hp, *, quiz=None, question_count=0):
    """Designated hook for making starting HP depend on the quiz.

    Today this is deliberately a no-op: HP is purely a function of the plan, so
    the value is returned unchanged.  The planned rules (longer quizzes grant
    more HP, harder question mixes grant less, item modifiers apply on top) all
    belong *here* — ``resolve_combat_rules()`` already threads ``quiz`` and
    ``question_count`` through, so turning the scaling on is a change to this
    one function and touches zero call sites.
    """
    return base_hp


def resolve_combat_rules(*, plan, quiz=None, question_count=0):
    """The single entry point for "what are this run's numbers?".

    ``quiz`` and ``question_count`` are accepted now, and ignored now, so that
    the future length-scaling rule needs no call-site churn.
    """
    rules = COMBAT_RULES.get(plan) or COMBAT_RULES[DEFAULT_RULES_KEY]
    scaled_hp = _scale_hp_for_quiz_length(
        rules.base_player_hp, quiz=quiz, question_count=question_count
    )
    if scaled_hp == rules.base_player_hp:
        return rules
    return replace(rules, base_player_hp=scaled_hp)


# --------------------------------------------------
# 2. BATTLE RESOLUTION
# --------------------------------------------------
# Damage a correct answer deals to the enemy. Kept beside the rules rather than
# inside CombatRules because it is the same for every plan today; promote it to
# a CombatRules field the moment one plan needs to differ.
DAMAGE_PER_CORRECT_ANSWER = 1

# Enumeration questions can be partially right. A partial answer is treated as
# a glancing blow: the question is spent, but neither side takes damage.
PARTIAL_ANSWER_DAMAGES_ENEMY = False
PARTIAL_ANSWER_DAMAGES_PLAYER = False


# --------------------------------------------------
# 3. ROOM & ENCOUNTER SHAPE
# --------------------------------------------------
MIN_ENEMIES = 1
MAX_ENEMIES = 4

# Stepping onto the tile an enemy is lurking on always starts that battle,
# regardless of encounter_chance. Without this a run could stall on bad rolls.
FORCED_ENCOUNTER_ON_ENEMY_TILE = True


def enemy_count_for_questions(question_count, *, rules):
    """How many enemies a quiz of this length supports."""
    if rules.questions_per_enemy <= 0:
        return 0
    return min(question_count // rules.questions_per_enemy, MAX_ENEMIES)


def minimum_questions_required(rules):
    """Fewest questions a quiz needs before a run can be launched."""
    return rules.questions_per_enemy * MIN_ENEMIES


# --------------------------------------------------
# 4. DROP TABLE
# --------------------------------------------------
ITEM_KEY_PIECE = "key_piece"
ITEM_SKIP_POTION = "skip_potion"
ITEM_HEALTH_POTION = "health_potion"
ITEM_NOTHING = "nothing"

# Guaranteed drop, every defeated enemy, always this many.
KEY_PIECES_PER_ENEMY = 1

# Weighted table for the bonus roll. Adding a third item is a one-line change
# here — no branching logic anywhere else.
DROP_TABLE = [
    (ITEM_SKIP_POTION, 30),
    (ITEM_HEALTH_POTION, 30),
    (ITEM_NOTHING, 40),
]


def roll_item_drop(rng=None, *, drop_table=None):
    """Pick one item key from a weighted table.

    Pass a seeded ``random.Random`` to make a run's drops reproducible.
    """
    table = drop_table if drop_table is not None else DROP_TABLE
    rng = rng or Random()
    total_weight = sum(weight for _, weight in table)
    if total_weight <= 0:
        return ITEM_NOTHING

    roll = rng.random() * total_weight
    upto = 0.0
    for item_key, weight in table:
        upto += weight
        if roll < upto:
            return item_key
    return table[-1][0]


# --------------------------------------------------
# 5. XP
# --------------------------------------------------
XP_CLEAR_BASE = 40
XP_PER_ENEMY_DEFEATED = 10
XP_PER_SURVIVING_HP = 2


def calculate_run_xp(*, cleared, enemies_defeated, hp_remaining):
    """XP for a finished run. Only a cleared run pays out."""
    if not cleared:
        return 0
    return (
        XP_CLEAR_BASE
        + XP_PER_ENEMY_DEFEATED * max(enemies_defeated, 0)
        + XP_PER_SURVIVING_HP * max(hp_remaining, 0)
    )


XP_REASON_TEMPLATE = "Dungeon Quest clear: {quiz_title}"


# --------------------------------------------------
# 6. PRESENTATION SCALE
# --------------------------------------------------
# Source resolution of one tile, in pixels. The single authority: the template
# publishes it as a CSS custom property and in the JS bootstrap payload, so
# neither dungeon.css nor the JS ever spells a tile size out.
TILE_SIZE = 32

# Icons (inventory items, hearts) are drawn at half a tile.
ICON_SIZE = TILE_SIZE // 2

# Integer-only upscaling, so pixel art never lands on a fractional edge.
MIN_BOARD_SCALE = 1
MAX_BOARD_SCALE = 3

# Vertical space the page spends on everything that is not the board (top bar,
# page heading, panel padding, caption, D-pad). The renderer subtracts this from
# the viewport height before choosing a scale, so the whole room stays on screen
# instead of running off the bottom.
BOARD_VERTICAL_RESERVE = 260
