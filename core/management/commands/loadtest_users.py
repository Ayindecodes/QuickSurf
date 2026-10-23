# core/management/commands/loadtest_users.py
import json
import secrets

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from rest_framework_simplejwt.tokens import RefreshToken

from users.models import User
from users.security import set_pin
from wallets import services as wallet_services


class Command(BaseCommand):
    help = (
        "Create funded, verified users with a PIN for scripts/load_test.py, and write their tokens to a "
        "JSON file. Refused in production and with LIVE providers: it creates money."
    )

    def add_arguments(self, parser):
        parser.add_argument("--count", type=int, default=20)
        parser.add_argument("--balance", default="2000.00", help="Naira each user starts with (default: 2000.00)")
        parser.add_argument("--pin", default="2580")
        parser.add_argument("--output", required=True, help="Where to write the users and tokens (JSON)")

    def handle(self, *args, **opts):
        if settings.IS_PRODUCTION or settings.PROVIDER_MODE == "LIVE":
            raise CommandError("Refusing to create load-test users in production or with LIVE providers.")

        run = secrets.token_hex(3)
        users = []
        for i in range(opts["count"]):
            user = User.objects.create_user(
                email=f"loadtest+{run}-{i}@example.invalid", password=secrets.token_urlsafe(18)
            )
            user.email_verified_at = timezone.now()
            user.save(update_fields=["email_verified_at"])
            wallet_services.adjust(user, opts["balance"], note=f"Load test {run}: starting balance")
            set_pin(user, opts["pin"])
            refresh = RefreshToken.for_user(user)
            users.append({"email": user.email, "access": str(refresh.access_token), "pin": opts["pin"]})

        with open(opts["output"], "w") as f:
            json.dump({"run": run, "balance": opts["balance"], "users": users}, f)
        self.stdout.write(self.style.SUCCESS(f"Created {len(users)} load-test users (run {run}) in {opts['output']}"))
