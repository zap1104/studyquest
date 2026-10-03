from django.contrib.auth.models import User
from django.test import TestCase

from courses.models import Chapter, Course, Quiz, Question, Choice
from courses.schemas import GeneratedJourney
from courses.services import _generate_mock_journey, get_assessment_mix
from courses.services import persist_journey as _build_journey


class MockJourneySchemaTests(TestCase):
    """Proves the fallback path can never silently drift from the same
    contract Gemini output must satisfy.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            username='schema_test_user', password='temporary-test-password'
        )
        self.course = Course.objects.create(
            user=self.user, title='Cloud Platforms', description='Schema integration test'
        )

    def test_mock_journey_matches_generated_journey_schema(self):
        result = _generate_mock_journey(self.course)
        validated = GeneratedJourney.model_validate(result)

        self.assertEqual(validated.schema_version, '1.0')
        self.assertGreaterEqual(len(validated.chapters), 1)

    def test_mock_questions_include_explanations(self):
        result = _generate_mock_journey(self.course)
        validated = GeneratedJourney.model_validate(result)

        questions = [
            question
            for chapter in validated.chapters
            for question in chapter.quiz.questions
        ]

        self.assertTrue(questions)
        self.assertTrue(all(q.explanation.strip() for q in questions))

    def test_all_format_mock_has_controlled_mixed_distribution(self):
        result = _generate_mock_journey(
            self.course,
            ["multiple_choice", "identification", "enumeration"],
        )
        validated = GeneratedJourney.model_validate(result)
        questions = validated.chapters[0].quiz.questions
        counts = {
            question_type: sum(q.type == question_type for q in questions)
            for question_type in get_assessment_mix(
                ["multiple_choice", "identification", "enumeration"]
            )
        }

        self.assertEqual(len(questions), 10)
        self.assertEqual(counts, {
            "multiple_choice": 3,
            "true_false": 2,
            "identification": 3,
            "enumeration": 2,
        })
        self.assertEqual(questions[5].accepted_answers, ["Zachman Framework", "Zachman"])
        self.assertEqual(len(questions[-1].expected_items), 3)


class BuildJourneyTransactionTests(TestCase):
    """Proves _build_journey's @transaction.atomic actually protects
    against partial writes — not just that it's decorated, but that a
    failure partway through genuinely rolls back everything, including
    chapters that were created before the failure point.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            username='rollback_test_user', password='temporary-test-password'
        )
        self.course = Course.objects.create(
            user=self.user, title='Rollback Test Course', description='Test'
        )

    def test_successful_build_persists_everything(self):
        _build_journey(self.course)

        self.course.refresh_from_db()
        self.assertTrue(Chapter.objects.filter(course=self.course).exists())
        self.assertTrue(Quiz.objects.filter(chapter__course=self.course).exists())
        self.assertTrue(Question.objects.filter(quiz__chapter__course=self.course).exists())
        self.assertTrue(Choice.objects.filter(question__quiz__chapter__course=self.course).exists())

    def test_partial_failure_rolls_back_entire_course(self):
        """Simulates a malformed journey (valid enough to pass Pydantic
        upstream in a real scenario, but missing a required Django-side
        key) reaching _build_journey directly. No chapters, quizzes,
        questions, or choices should persist if any chapter fails.
        """
        malformed_journey = {
            'course': {
                'title': 'Broken Course',
                'description': 'This should never be saved.',
            },
            'chapters': [
                {
                    'order': 1,
                    'title': 'Good Chapter',
                    'overview': 'This chapter is well-formed.',
                    'quiz': {
                        'title': 'Good Quiz',
                        'questions': [
                            {
                                'order': 1,
                                'text': 'A fine question?',
                                'explanation': 'A fine explanation.',
                                'choices': [
                                    {'text': 'A', 'is_correct': True},
                                    {'text': 'B', 'is_correct': False},
                                ],
                            }
                        ],
                    },
                },
                {
                    'order': 2,
                    # 'title' deliberately missing -> KeyError inside the loop
                    'overview': 'This chapter is broken.',
                    'quiz': {'title': 'Broken Quiz', 'questions': []},
                },
            ],
        }

        with self.assertRaises(KeyError):
            _build_journey(self.course, journey_override=malformed_journey)

        self.assertEqual(Chapter.objects.filter(course=self.course).count(), 0)
        self.assertEqual(Quiz.objects.filter(chapter__course=self.course).count(), 0)
        self.assertEqual(Question.objects.filter(quiz__chapter__course=self.course).count(), 0)