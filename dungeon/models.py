"""Persistent state for a Dungeon Quest run.

These live in their own app rather than ``courses/models.py`` so the mode can
evolve without touching the shared model module.  Everything a client could
lie about — HP, inventory, which enemies are dead, which questions an enemy
still holds, where the player is standing — is stored here and is the only
thing the server trusts.  A refresh reloads this state; it never heals the
player or revives an enemy.
"""

from django.contrib.auth.models import User
from django.db import models

from courses.models import Quiz


class DungeonRun(models.Model):
    STATUS_IN_PROGRESS = "in_progress"
    STATUS_CLEARED = "cleared"
    STATUS_FAILED = "failed"
    STATUS_ABANDONED = "abandoned"

    STATUS_CHOICES = [
        (STATUS_IN_PROGRESS, "In Progress"),
        (STATUS_CLEARED, "Cleared"),
        (STATUS_FAILED, "Failed"),
        (STATUS_ABANDONED, "Abandoned"),
    ]

    TYPE_CLASSIC = "classic"
    TYPE_REVIEW = "review"

    RUN_TYPE_CHOICES = [
        (TYPE_CLASSIC, "Classic Expedition"),
        (TYPE_REVIEW, "Review Run"),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="dungeon_runs")
    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE, related_name="dungeon_runs")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_IN_PROGRESS)
    run_type = models.CharField(
        max_length=20,
        choices=RUN_TYPE_CHOICES,
        default=TYPE_CLASSIC,
    )

    # Remediation targets and authoritative outcomes for Review Runs.
    review_question_ids = models.JSONField(default=list, blank=True)
    review_mastered_question_ids = models.JSONField(default=list, blank=True)

    current_hp = models.IntegerField(default=0)
    max_hp = models.IntegerField(default=0)

    room_data = models.JSONField(default=dict, blank=True)
    player_x = models.IntegerField(default=0)
    player_y = models.IntegerField(default=0)

    rules_key = models.CharField(max_length=20, default="free")
    seed = models.BigIntegerField(default=0)

    active_enemy = models.ForeignKey(
        "DungeonEnemy",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="The battle in progress, so a page refresh resumes it instead of escaping it.",
    )

    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    # None = never paid out. An integer (0 included) = already paid out, so a
    # double POST cannot farm the award.
    xp_awarded = models.IntegerField(null=True, blank=True, default=None)

    class Meta:
        ordering = ["-started_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "quiz"],
                condition=models.Q(status="in_progress"),
                name="unique_active_dungeon_run_per_quiz",
            )
        ]

    def __str__(self):
        return f"{self.user.username} — {self.quiz} ({self.status})"

    @property
    def is_active(self):
        return self.status == self.STATUS_IN_PROGRESS

    @property
    def has_awarded_xp(self):
        return self.xp_awarded is not None

    @property
    def is_alive(self):
        return self.current_hp > 0

    @property
    def is_review_run(self):
        return self.run_type == self.TYPE_REVIEW

    @property
    def targeted_count(self):
        return len(self.review_question_ids or [])

    @property
    def mastered_count(self):
        return len(self.review_mastered_question_ids or [])

    @property
    def is_mastered(self):
        return self.is_review_run and self.targeted_count > 0 and self.mastered_count == self.targeted_count

    @property
    def verdict_code(self):
        if self.status == self.STATUS_ABANDONED:
            return "abandoned"
        if self.status == self.STATUS_FAILED:
            return "failed"
        if self.run_type == self.TYPE_REVIEW:
            targeted = set(self.review_question_ids or [])
            mastered = set(self.review_mastered_question_ids or [])
            return "review_mastered" if (targeted and targeted <= mastered) else "review_incomplete"
        if self.status == self.STATUS_CLEARED:
            return "cleared"
        return "incomplete"


class DungeonEnemy(models.Model):
    run = models.ForeignKey(DungeonRun, on_delete=models.CASCADE, related_name="enemies")
    enemy_index = models.PositiveIntegerField(default=0)

    x = models.IntegerField(default=0)
    y = models.IntegerField(default=0)

    hp = models.IntegerField(default=0)
    max_hp = models.IntegerField(default=0)
    is_defeated = models.BooleanField(default=False)

    # The questions dealt to this enemy, and the subset already used up. Both
    # are server-side only: the client is told how many remain, never which.
    question_ids = models.JSONField(default=list, blank=True)
    answered_question_ids = models.JSONField(default=list, blank=True)

    ROLE_GRUNT = "grunt"
    ROLE_GUARDIAN = "guardian"
    ROLE_CHOICES = [
        (ROLE_GRUNT, "Grunt"),
        (ROLE_GUARDIAN, "Guardian"),
    ]

    role = models.CharField(max_length=20, default=ROLE_GRUNT, choices=ROLE_CHOICES)
    name = models.CharField(max_length=50, default="Grunt")
    armor_active = models.BooleanField(default=False)

    # Streak of consecutive correct answers against this enemy. Resets on wrong answer,
    # and resets to 0 after triggering a combo strike.
    current_combo = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["enemy_index"]
        constraints = [
            models.UniqueConstraint(
                fields=["run", "enemy_index"], name="unique_enemy_index_per_run"
            )
        ]
        verbose_name_plural = "dungeon enemies"

    def __str__(self):
        return f"Enemy {self.enemy_index} of run {self.run_id}"

    @property
    def remaining_question_ids(self):
        answered = set(self.answered_question_ids or [])
        return [qid for qid in (self.question_ids or []) if qid not in answered]

    @property
    def has_questions_left(self):
        return bool(self.remaining_question_ids)


class DungeonInventory(models.Model):
    """One row per run. A run-scoped bag of counters doesn't earn a per-item
    table — it is short-lived and never queried across runs.
    """

    run = models.OneToOneField(DungeonRun, on_delete=models.CASCADE, related_name="inventory")
    key_pieces = models.PositiveIntegerField(default=0)
    skip_potions = models.PositiveIntegerField(default=0)
    health_potions = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name_plural = "dungeon inventories"

    def __str__(self):
        return f"Inventory for run {self.run_id}"
