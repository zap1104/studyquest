import json

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from courses.models import XPTransaction

from dungeon import rooms, services
from dungeon.models import DungeonRun

from .test_services import NeverEncounter, correct_choice_id, make_quiz


class DungeonViewTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="delver", password="password123")
        self.client.force_login(self.user)

    def start_run(self, *, question_count=20, seed=99, user=None):
        user = user or self.user
        quiz = make_quiz(user, question_count=question_count)
        return services.start_or_resume_run(user, quiz, seed=seed)

    def post_json(self, url, payload):
        return self.client.post(url, data=json.dumps(payload), content_type="application/json")

    def engage(self, run):
        enemy = run.enemies.filter(is_defeated=False).first()
        grid = run.room_data["grid"]
        for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            nx, ny = enemy.x + dx, enemy.y + dy
            if rooms.is_walkable(grid, nx, ny):
                run.player_x, run.player_y = nx, ny
                run.save(update_fields=["player_x", "player_y"])
                direction = {
                    (0, 1): "up", (0, -1): "down", (1, 0): "left", (-1, 0): "right",
                }[(dx, dy)]
                services.move_player(run, direction, rng=NeverEncounter())
                run.refresh_from_db()
                return
        self.fail("Enemy lair has no walkable neighbour.")


class AccessControlTests(DungeonViewTestCase):
    def test_launch_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("dungeon:launch"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])

    def test_cannot_start_a_run_on_someone_elses_quiz(self):
        stranger = User.objects.create_user(username="stranger", password="password123")
        their_quiz = make_quiz(stranger, question_count=10)

        response = self.client.post(reverse("dungeon:start_run"), {"quiz_id": their_quiz.pk})

        self.assertEqual(response.status_code, 404, "must 404, never 403")
        self.assertFalse(DungeonRun.objects.filter(user=self.user).exists())

    def test_cannot_touch_someone_elses_run(self):
        stranger = User.objects.create_user(username="stranger", password="password123")
        their_run = self.start_run(user=stranger)

        for name in ("room", "state", "summary"):
            self.assertEqual(
                self.client.get(reverse(f"dungeon:{name}", args=[their_run.pk])).status_code,
                404,
            )

        self.assertEqual(
            self.post_json(reverse("dungeon:move", args=[their_run.pk]), {"direction": "up"}).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(reverse("dungeon:abandon", args=[their_run.pk])).status_code,
            404,
        )

    def test_a_nonexistent_quiz_id_is_a_404(self):
        response = self.client.post(reverse("dungeon:start_run"), {"quiz_id": 999999})
        self.assertEqual(response.status_code, 404)

    def test_move_rejects_a_get(self):
        run = self.start_run()
        self.assertEqual(
            self.client.get(reverse("dungeon:move", args=[run.pk])).status_code, 405
        )


class LaunchTests(DungeonViewTestCase):
    def test_launch_lists_playable_and_unplayable_quizzes(self):
        make_quiz(self.user, question_count=12, title="Long")
        make_quiz(self.user, question_count=3, title="Short")

        response = self.client.get(reverse("dungeon:launch"))

        self.assertEqual(response.status_code, 200)
        entries = {
            entry["quiz"].title: entry
            for group in response.context["catalog"]
            for entry in group["entries"]
        }
        self.assertTrue(entries["Long"]["is_playable"])
        self.assertFalse(entries["Short"]["is_playable"])

    def test_starting_a_short_quiz_explains_the_block(self):
        quiz = make_quiz(self.user, question_count=2)

        response = self.client.post(reverse("dungeon:start_run"), {"quiz_id": quiz.pk})

        self.assertEqual(response.status_code, 400)
        self.assertIn("at least 5", response.context["launch_error"])
        self.assertFalse(DungeonRun.objects.exists())

    def test_starting_twice_resumes_the_same_run(self):
        quiz = make_quiz(self.user, question_count=20)

        first = self.client.post(reverse("dungeon:start_run"), {"quiz_id": quiz.pk})
        second = self.client.post(reverse("dungeon:start_run"), {"quiz_id": quiz.pk})

        self.assertEqual(first["Location"], second["Location"])
        self.assertEqual(DungeonRun.objects.count(), 1)

    def test_finished_run_redirects_from_room_to_summary(self):
        run = self.start_run()
        services.abandon_run(run)

        response = self.client.get(reverse("dungeon:room", args=[run.pk]))

        self.assertRedirects(response, reverse("dungeon:summary", args=[run.pk]))


class BootstrapPayloadTests(DungeonViewTestCase):
    """The page must not ship answers to the browser, in any form."""

    def test_rendered_page_never_marks_which_choice_is_right(self):
        """Both options must be on the page - that is the question. What must
        never be there is any signal of which one scores."""
        run = self.start_run()
        self.engage(run)

        html = self.client.get(reverse("dungeon:room", args=[run.pk])).content.decode()

        self.assertNotIn("is_correct", html)
        self.assertNotIn("answer_data", html)
        self.assertNotIn("correct_choice_id", html)
        self.assertNotIn("accepted_answers", html)

        question = json.loads(
            html.split('id="dungeon-bootstrap" type="application/json">')[1].split("</script>")[0]
        )["state"]["battle"]["question"]

        for choice in question["choices"]:
            self.assertEqual(set(choice), {"id", "text"})
        self.assertNotIn("explanation", question)

    def test_identification_answers_never_reach_the_page(self):
        from courses.models import Chapter, Course, Question, Quiz

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
                explanation="It is the VLAN tagging standard.",
            )

        run = services.start_or_resume_run(self.user, quiz, seed=11)
        self.engage(run)

        html = self.client.get(reverse("dungeon:room", args=[run.pk])).content.decode()

        self.assertNotIn("802.1Q", html)
        self.assertNotIn("dot1q", html)
        self.assertNotIn("VLAN tagging standard", html)

    def test_state_endpoint_never_contains_a_correct_answer(self):
        run = self.start_run()
        self.engage(run)

        body = self.client.get(reverse("dungeon:state", args=[run.pk])).content.decode()

        self.assertNotIn("is_correct", body)
        self.assertNotIn("answer_data", body)
        self.assertNotIn("accepted_answers", body)

    def test_bootstrap_carries_the_endpoints_and_display_settings(self):
        run = self.start_run()
        response = self.client.get(reverse("dungeon:room", args=[run.pk]))

        bootstrap = response.context["bootstrap"]
        self.assertIn("sprites.json", bootstrap["urls"]["sprites"])
        self.assertEqual(
            bootstrap["urls"]["move"], reverse("dungeon:move", args=[run.pk])
        )
        self.assertEqual(
            bootstrap["state"]["display"]["tile_size"],
            services.display_settings()["tile_size"],
        )

    def test_tile_size_is_published_to_the_template_from_config(self):
        run = self.start_run()
        response = self.client.get(reverse("dungeon:room", args=[run.pk]))

        expected = services.display_settings()
        self.assertEqual(response.context["tile_size"], expected["tile_size"])
        self.assertEqual(response.context["icon_size"], expected["icon_size"])
        self.assertContains(response, f"--dq-tile: {expected['tile_size']}px")
        self.assertContains(response, f"--dq-ui-scale: {expected['ui_scale']}")


class RunApiTests(DungeonViewTestCase):
    def test_move_returns_the_new_position(self):
        run = self.start_run()
        start = (run.player_x, run.player_y)

        response = self.post_json(
            reverse("dungeon:move", args=[run.pk]), {"direction": "up"}
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["result"]["moved"])
        run.refresh_from_db()
        self.assertNotEqual((run.player_x, run.player_y), start)

    def test_a_bad_direction_is_a_400_with_a_message(self):
        run = self.start_run()

        response = self.post_json(
            reverse("dungeon:move", args=[run.pk]), {"direction": "sideways"}
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["ok"])
        self.assertTrue(response.json()["error"])

    def test_malformed_json_is_a_400(self):
        run = self.start_run()

        response = self.client.post(
            reverse("dungeon:move", args=[run.pk]),
            data="{not json",
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)

    def test_answering_grades_server_side(self):
        run = self.start_run()
        self.engage(run)
        question = services.current_question(run.active_enemy)

        response = self.post_json(reverse("dungeon:answer", args=[run.pk]), {
            "question_id": question.id,
            "answer": {"choice_id": correct_choice_id(question.id)},
        })

        result = response.json()["result"]
        self.assertEqual(result["outcome"], "correct")
        self.assertEqual(result["damage_to_enemy"], 1)

    def test_answering_a_question_you_were_not_asked_is_rejected(self):
        run = self.start_run()
        self.engage(run)
        asked = services.current_question(run.active_enemy)
        other = run.quiz.questions.exclude(pk=asked.pk).first()

        response = self.post_json(reverse("dungeon:answer", args=[run.pk]), {
            "question_id": other.id,
            "answer": {"choice_id": correct_choice_id(other.id)},
        })

        self.assertEqual(response.status_code, 400)

    def test_using_an_item_you_lack_is_rejected(self):
        run = self.start_run()

        response = self.post_json(
            reverse("dungeon:use_item", args=[run.pk]), {"item": "health_potion"}
        )

        self.assertEqual(response.status_code, 400)

    def test_abandoning_ends_the_run_and_lands_on_the_summary(self):
        run = self.start_run()

        response = self.client.post(reverse("dungeon:abandon", args=[run.pk]))

        self.assertRedirects(response, reverse("dungeon:summary", args=[run.pk]))
        run.refresh_from_db()
        self.assertEqual(run.status, DungeonRun.STATUS_ABANDONED)


class ExitAndXpTests(DungeonViewTestCase):
    def clear_and_stand_at_door(self, run):
        for enemy in list(run.enemies.all()):
            self.engage(run)
            while run.active_enemy_id:
                question = services.current_question(run.active_enemy)
                services.answer_question(
                    run, question.id, {"choice_id": correct_choice_id(question.id)}
                )
        door = run.room_data["door"]
        run.player_x, run.player_y = door["x"], door["y"]
        run.save(update_fields=["player_x", "player_y"])

    def test_exit_is_refused_while_the_door_is_sealed(self):
        run = self.start_run()
        door = run.room_data["door"]
        run.player_x, run.player_y = door["x"], door["y"]
        run.save(update_fields=["player_x", "player_y"])

        response = self.client.post(reverse("dungeon:exit_room", args=[run.pk]))

        self.assertEqual(response.status_code, 400)
        self.assertIn("sealed", response.json()["error"])

    def test_clearing_the_room_awards_xp_once_across_repeated_posts(self):
        run = self.start_run()
        self.clear_and_stand_at_door(run)
        url = reverse("dungeon:exit_room", args=[run.pk])

        first = self.client.post(url)
        self.assertEqual(first.status_code, 200)
        awarded = first.json()["result"]["xp_awarded"]
        self.assertGreater(awarded, 0)

        # A double-submit (impatient player, flaky network, refresh) must not pay twice.
        second = self.client.post(url)
        third = self.client.post(url)
        self.assertEqual(second.status_code, 400)
        self.assertEqual(third.status_code, 400)

        profile = self.user.userprofile
        profile.refresh_from_db()
        self.assertEqual(profile.total_xp, awarded)
        self.assertEqual(XPTransaction.objects.filter(user=self.user).count(), 1)

    def test_summary_reports_the_run(self):
        run = self.start_run()
        self.clear_and_stand_at_door(run)
        self.client.post(reverse("dungeon:exit_room", args=[run.pk]))

        response = self.client.get(reverse("dungeon:summary", args=[run.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["enemies_defeated"], run.enemies.count())
        self.assertContains(response, "DUNGEON CLEARED")


class LaunchCopyTests(DungeonViewTestCase):
    """The launch screen explains the rules, so it must read them from config
    rather than restate them - otherwise retuning the game silently makes the
    page lie."""

    def test_rules_come_from_config_not_prose(self):
        from dungeon.combat_config import resolve_combat_rules

        rules = resolve_combat_rules(plan="free")
        response = self.client.get(reverse("dungeon:launch"))

        self.assertEqual(response.context["rules"], rules)
        self.assertContains(response, f"asks {rules.questions_per_enemy} questions")
        self.assertContains(response, f"{rules.base_player_hp} hearts")

    def test_plus_player_sees_their_own_numbers(self):
        self.user.userprofile.plan = "plus"
        self.user.userprofile.save(update_fields=["plan"])

        from dungeon.combat_config import resolve_combat_rules

        response = self.client.get(reverse("dungeon:launch"))

        self.assertContains(
            response, f"{resolve_combat_rules(plan='plus').base_player_hp} hearts"
        )


class MalformedInputTests(DungeonViewTestCase):
    """A hostile client can send anything. Garbage is a 4xx, never a 500."""

    def test_json_that_is_not_an_object_is_a_400(self):
        run = self.start_run()
        for body in ("[1, 2]", '"up"', "42", "null"):
            response = self.client.post(
                reverse("dungeon:move", args=[run.pk]), data=body, content_type="application/json"
            )
            self.assertEqual(response.status_code, 400, body)

    def test_non_string_direction_is_a_400(self):
        run = self.start_run()
        for direction in (["up"], {"x": 1}, 7, None):
            response = self.post_json(reverse("dungeon:move", args=[run.pk]), {"direction": direction})
            self.assertEqual(response.status_code, 400, direction)

    def test_non_numeric_quiz_id_is_a_404(self):
        for quiz_id in ("abc", "", "1; DROP TABLE"):
            response = self.client.post(reverse("dungeon:start_run"), {"quiz_id": quiz_id})
            self.assertEqual(response.status_code, 404, quiz_id)

    def test_non_object_answer_is_graded_as_wrong_not_crashed(self):
        run = self.start_run()
        self.engage(run)
        question = services.current_question(run.active_enemy)

        response = self.post_json(reverse("dungeon:answer", args=[run.pk]), {
            "question_id": question.id, "answer": ["not", "an", "object"],
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["result"]["outcome"], "incorrect")

    def test_non_string_item_is_a_400(self):
        run = self.start_run()
        response = self.post_json(reverse("dungeon:use_item", args=[run.pk]), {"item": ["health_potion"]})
        self.assertEqual(response.status_code, 400)
