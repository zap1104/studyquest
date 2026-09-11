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

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="dungeon_runs")
    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE, related_name="dungeon_runs")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_IN_PROGRESS)

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
