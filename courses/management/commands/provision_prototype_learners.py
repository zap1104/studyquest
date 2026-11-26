import json
import os

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import identify_hasher
from django.contrib.auth.models import Permission
from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ValidationError
from django.db import transaction

from courses.models import UserProfile


class Command(BaseCommand):
    help = "Provision prototype learner accounts and an optional staff plan manager."

    @transaction.atomic
    def handle(self, *args, **options):
        try:
            accounts = json.loads(os.environ["STUDYQUEST_PROTOTYPE_LEARNERS"])
            if set(accounts) != {"free", "plus"}:
                raise ValueError
            staff_config = os.environ.get("STUDYQUEST_PROTOTYPE_ADMIN")
            if staff_config:
                accounts["admin"] = json.loads(staff_config)
            usernames = set()
            for account in accounts.values():
                username = account["username"]
                if not isinstance(username, str) or not username.startswith("sq-demo-") or username in usernames:
                    raise ValueError
                get_user_model()._meta.get_field("username").run_validators(username)
                identify_hasher(account["password_hash"])
                usernames.add(username)
        except (AttributeError, KeyError, TypeError, ValueError, ValidationError):
            raise CommandError("Invalid prototype learner provisioning configuration.") from None

        users = get_user_model().objects
        for role, account in accounts.items():
            is_staff = role == "admin"
            plan = "plus" if role == "plus" else "free"
            user, created = users.get_or_create(
                username=account["username"],
                defaults={"password": account["password_hash"], "is_staff": is_staff},
            )
            profile, _ = UserProfile.objects.get_or_create(user=user)
            if created:
                profile.plan = plan
                profile.save(update_fields=["plan"])
                if is_staff:
                    user.user_permissions.set(Permission.objects.filter(
                        content_type__app_label="courses",
                        content_type__model="userprofile",
                        codename__in=("view_userprofile", "change_userprofile"),
                    ))
            elif (user.is_staff != is_staff or user.is_superuser or not user.is_active
                  or user.password != account["password_hash"] or profile.plan != plan):
                raise CommandError("A requested prototype username is already in use.")

        self.stdout.write("Verified distinct Free and Plus prototype learner accounts.")
        if staff_config:
            staff = users.get(username=accounts["admin"]["username"])
            if not staff.has_perms(("courses.view_userprofile", "courses.change_userprofile")):
                raise CommandError("The prototype staff account is missing plan-management permissions.")
            self.stdout.write("Verified separate staff account for learner plan management.")
