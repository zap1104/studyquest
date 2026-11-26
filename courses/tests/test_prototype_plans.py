import os
from datetime import timedelta
from django.contrib.auth.models import User
from django.test import TestCase
from django.core.management import call_command
from django.core.management.base import CommandError
from django.urls import reverse
from django.utils import timezone
from django.contrib.auth.hashers import make_password
from unittest.mock import patch
import json

from courses.credits import get_generation_eligibility
from courses.models import CourseCreditTransaction


class PrototypePlanTests(TestCase):
    def test_plan_ui_and_upload_limits_follow_account(self):
        for plan, file_limit, bundle_limit in [("free", 10, 20), ("plus", 25, 50)]:
            with self.subTest(plan=plan):
                user = User.objects.create_user(username=f"test-{plan}")
                user.userprofile.plan = plan
                user.userprofile.save(update_fields=["plan"])
                self.client.force_login(user)
                response = self.client.get(reverse("courses:course_create"))
                self.assertIn(f"{file_limit} MB each, {bundle_limit}.0 MB total", response.content.decode().replace("\xa0", " "))
                self.assertContains(response, f"const MAX_FILE_BYTES = {file_limit * 1024 * 1024}")
                self.assertContains(response, "Both plans include all study tools.")
                current = f"StudyQuest {plan.title()}</h3>"
                self.assertContains(response, current)
                self.assertContains(response, "Your current plan", count=1)

    def test_plus_cooldown_starts_when_oldest_counted_credit_expires(self):
        user = User.objects.create_user(username="credit-timing")
        profile = user.userprofile
        profile.plan = "plus"
        profile.save(update_fields=["plan"])
        now = timezone.now()
        first = now - timedelta(days=5)
        for index, when in enumerate([first, now - timedelta(days=3), now - timedelta(days=1)]):
            entry = CourseCreditTransaction.objects.create(
                user=user, transaction_type="weekly_spend", amount=-1, reference_key=f"test:{index}"
            )
            CourseCreditTransaction.objects.filter(pk=entry.pk).update(created_at=when)
        self.assertEqual(get_generation_eligibility(profile).available_at, first + timedelta(days=7))

    def test_provision_creates_two_ordinary_users_without_modifying_admin(self):
        admin = User.objects.create_superuser(username="admin", password="local-test-password")
        configuration = {
            key: {"username": f"sq-demo-{key}", "password_hash": make_password(f"test-password-{key}")}
            for key in ("free", "plus")
        }
        staff_config = {
            "username": "sq-demo-admin", "password_hash": make_password("local-staff-password")
        }
        with patch.dict(os.environ, {
            "STUDYQUEST_PROTOTYPE_LEARNERS": json.dumps(configuration),
            "STUDYQUEST_PROTOTYPE_ADMIN": json.dumps(staff_config),
        }):
            call_command("provision_prototype_learners")
            call_command("provision_prototype_learners")
            staff = User.objects.get(username="sq-demo-admin")
            self.assertTrue(staff.is_staff)
            self.assertFalse(staff.is_superuser)
            self.assertEqual(staff.get_all_permissions(), {"courses.view_userprofile", "courses.change_userprofile"})
            self.client.force_login(staff)
            plus = User.objects.get(username="sq-demo-plus")
            profile_edit = reverse("admin:courses_userprofile_change", args=[plus.userprofile.pk])
            response = self.client.get(profile_edit)
            self.assertContains(response, 'name="plan"')
            self.assertNotContains(response, 'name="total_xp"')
            self.assertTrue(plus.check_password("test-password-plus"))
            self.assertEqual(plus.userprofile.plan, "plus")
            self.assertFalse(plus.is_staff)
            self.assertFalse(plus.is_superuser)
            self.assertEqual(User.objects.get(username="sq-demo-free").userprofile.plan, "free")
            plus.set_password("changed-test-password")
            plus.save(update_fields=["password"])
            with self.assertRaises(CommandError):
                call_command("provision_prototype_learners")
        self.assertTrue(User.objects.get(pk=admin.pk).is_superuser)

