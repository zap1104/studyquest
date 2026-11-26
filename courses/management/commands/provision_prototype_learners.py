import json
import os

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import identify_hasher
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from courses.models import UserProfile


class Command(BaseCommand):
    help = "Provision separately configured Free and Plus prototype learner accounts."

    @transaction.atomic
    def handle(self, *args, **options):
        try:
            accounts = json.loads(os.environ["STUDYQUEST_PROTOTYPE_LEARNERS"])
            if set(accounts) != {"free", "plus"}:
                raise ValueError
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
        for plan, account in accounts.items():
            user, created = users.get_or_create(
                username=account["username"],
                defaults={"password": account["password_hash"]},
            )
            profile, _ = UserProfile.objects.get_or_create(user=user)
            if created:
                profile.plan = plan
                profile.save(update_fields=["plan"])
            elif (user.is_staff or user.is_superuser or not user.is_active
                  or user.password != account["password_hash"] or profile.plan != plan):
                raise CommandError("A requested prototype username is already in use.")

        self.stdout.write("Verified distinct Free and Plus prototype learner accounts.")
        if users.filter(username="admin", is_active=True, is_staff=True, is_superuser=True).exists():
            self.stdout.write("Verified existing admin account with Django superuser access.")
