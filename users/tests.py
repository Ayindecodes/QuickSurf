import importlib
import re
from datetime import timedelta
from decimal import Decimal
from unittest.mock import Mock, patch

from django.apps import apps
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from core.models import LoginActivity
from notifications import sms
from notifications.models import EmailLog
from services.models import ProviderLog
from users import limits
from users.models import OneTimeCode, TransactionPin, User
from users.security import close_account, set_pin
from wallets import services as wallet_services
from wallets.models import Wallet

STRONG = "Tropical-Lantern-42"
DELIVERED = {
    "provider": {
        "code": "000",
        "response_description": "TRANSACTION SUCCESSFUL",
        "content": {"transactions": {"status": "delivered", "phone": "08031234567"}},
    }
}


def _emailed_code():
    return re.search(r"\b(\d{6})\b", mail.outbox[-1].body).group(1)


class AuthTestCase(APITestCase):
    def setUp(self):
        cache.clear()  # rate-limit counters live in the cache

    def _login(self, email, password=STRONG):
        return self.client.post(reverse("user-login"), {"email": email, "password": password}, format="json")


class AuthFlowTests(AuthTestCase):
    def test_register_returns_tokens_creates_wallet_and_emails_a_code(self):
        response = self.client.post(
            reverse("user-register"), {"email": "Tester@Example.com", "password": STRONG}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)
        self.assertTrue(response.data["email_verification_sent"])

        user = User.objects.get(email="tester@example.com")
        self.assertEqual(Wallet.objects.get(user=user).balance, 0)
        self.assertEqual(LoginActivity.objects.filter(user=user).count(), 1)
        self.assertFalse(user.email_verified)

        code = _emailed_code()
        stored = EmailLog.objects.get(to=user.email)
        self.assertNotIn(code, stored.body)  # the code itself is never stored
        self.assertIn("******", stored.body)

    def test_register_rejects_weak_passwords(self):
        for weak in ("password", "12345678", "short1"):
            with self.subTest(password=weak):
                response = self.client.post(
                    reverse("user-register"), {"email": "weak@example.com", "password": weak}, format="json"
                )
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn("password", response.data)
        self.assertFalse(User.objects.filter(email="weak@example.com").exists())

    def test_register_rejects_an_email_that_differs_only_in_case(self):
        User.objects.create_user(email="dup@example.com", password=STRONG)

        response = self.client.post(
            reverse("user-register"), {"email": "DUP@example.com", "password": STRONG}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("email", response.data)

    def test_login_returns_tokens_and_records_activity(self):
        user = User.objects.create_user(email="login@example.com", password=STRONG)

        response = self._login("login@example.com")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)
        self.assertEqual(LoginActivity.objects.filter(user=user).count(), 1)

    def test_login_ignores_email_case(self):
        User.objects.create_user(email="case@example.com", password=STRONG)

        self.assertEqual(self._login("CASE@Example.com").status_code, status.HTTP_200_OK)
        token = self.client.post(
            reverse("token_obtain_pair"), {"email": "Case@EXAMPLE.com", "password": STRONG}, format="json"
        )
        self.assertEqual(token.status_code, status.HTTP_200_OK)

    def test_login_attempts_are_limited_per_email(self):
        User.objects.create_user(email="target@example.com", password=STRONG)

        wrong = [self._login("target@example.com", "not-the-password").status_code for _ in range(10)]
        then_right = self._login("target@example.com")

        self.assertEqual(set(wrong), {status.HTTP_401_UNAUTHORIZED})
        self.assertEqual(then_right.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_logout_revokes_the_refresh_token(self):
        User.objects.create_user(email="bye@example.com", password=STRONG)
        tokens = self._login("bye@example.com").data
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")

        logout = self.client.post(reverse("user-logout"), {"refresh": tokens["refresh"]}, format="json")
        refresh = self.client.post(reverse("token_refresh"), {"refresh": tokens["refresh"]}, format="json")

        self.assertEqual(logout.status_code, status.HTTP_200_OK)
        self.assertEqual(refresh.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_refreshing_rotates_the_refresh_token(self):
        User.objects.create_user(email="rotate@example.com", password=STRONG)
        old = self._login("rotate@example.com").data["refresh"]

        first = self.client.post(reverse("token_refresh"), {"refresh": old}, format="json")
        reused = self.client.post(reverse("token_refresh"), {"refresh": old}, format="json")

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertNotEqual(first.data["refresh"], old)
        self.assertEqual(reused.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_dashboard_shows_verification_and_pin_status(self):
        user = User.objects.create_user(email="dash@example.com", password=STRONG)
        self.client.force_authenticate(user)

        response = self.client.get(reverse("user-dashboard"))

        self.assertEqual((response.data["email_verified"], response.data["has_pin"]), (False, False))


class EmailVerificationTests(AuthTestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(email="verify@example.com", password=STRONG)
        self.client.force_authenticate(self.user)

    def _confirm(self, code):
        return self.client.post(reverse("verify-email-confirm"), {"code": code}, format="json")

    def test_the_emailed_code_verifies_the_email(self):
        self.client.post(reverse("verify-email-request"))
        code = _emailed_code()
        wrong = "000000" if code != "000000" else "111111"

        self.assertEqual(self._confirm(wrong).status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self._confirm(code).status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertTrue(self.user.email_verified)

    def test_a_code_stops_working_after_five_wrong_tries(self):
        self.client.post(reverse("verify-email-request"))
        code = _emailed_code()
        wrong = "000000" if code != "000000" else "111111"

        for _ in range(OneTimeCode.MAX_ATTEMPTS):
            self._confirm(wrong)

        self.assertEqual(self._confirm(code).status_code, status.HTTP_400_BAD_REQUEST)
        self.user.refresh_from_db()
        self.assertFalse(self.user.email_verified)

    def test_codes_expire_and_cannot_be_resent_within_a_minute(self):
        self.assertEqual(self.client.post(reverse("verify-email-request")).status_code, status.HTTP_200_OK)
        self.assertEqual(
            self.client.post(reverse("verify-email-request")).status_code, status.HTTP_429_TOO_MANY_REQUESTS
        )
        code = _emailed_code()
        OneTimeCode.objects.update(expires_at=timezone.now() - timedelta(minutes=1))

        self.assertEqual(self._confirm(code).status_code, status.HTTP_400_BAD_REQUEST)

    def test_unverified_users_cannot_top_up_and_existing_users_were_let_through(self):
        response = self.client.post(reverse("payments_init"), {"amount": "500.00"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data["code"], "email_unverified")

        importlib.import_module("users.migrations.0003_existing_users_verified").treat_existing_users_as_verified(
            apps, None
        )
        self.user.refresh_from_db()
        self.assertTrue(self.user.email_verified)


class PasswordResetTests(AuthTestCase):
    def setUp(self):
        super().setUp()
        User.objects.create_user(email="reset@example.com", password=STRONG)

    def _request(self, email):
        return self.client.post(reverse("password-reset-request"), {"email": email}, format="json")

    def _confirm(self, code, new_password="Another-Strong-Phrase-77"):
        return self.client.post(
            reverse("password-reset-confirm"),
            {"email": "reset@example.com", "code": code, "new_password": new_password},
            format="json",
        )

    def test_reset_with_the_emailed_code_changes_the_password_and_signs_out_everywhere(self):
        old_refresh = self._login("reset@example.com").data["refresh"]

        self._request("RESET@example.com")
        response = self._confirm(_emailed_code())

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        refresh = self.client.post(reverse("token_refresh"), {"refresh": old_refresh}, format="json")
        self.assertEqual(refresh.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(self._login("reset@example.com", "Another-Strong-Phrase-77").status_code, status.HTTP_200_OK)
        self.assertTrue(User.objects.get(email="reset@example.com").email_verified)

    def test_reset_request_does_not_reveal_unknown_emails(self):
        known, unknown = self._request("reset@example.com"), self._request("nobody@example.com")

        self.assertEqual(known.data, unknown.data)
        self.assertEqual(len(mail.outbox), 1)

    def test_a_weak_new_password_is_rejected_without_using_up_the_code(self):
        self._request("reset@example.com")
        code = _emailed_code()

        weak = self._confirm(code, new_password="password")
        strong = self._confirm(code)

        self.assertEqual(weak.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("new_password", weak.data)
        self.assertEqual(strong.status_code, status.HTTP_200_OK)

    def test_a_wrong_code_is_rejected(self):
        self._request("reset@example.com")
        code = _emailed_code()

        response = self._confirm("000000" if code != "000000" else "111111")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self._login("reset@example.com").status_code, status.HTTP_200_OK)  # unchanged


class TransactionPinTests(AuthTestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(email="pin@example.com", password=STRONG)
        wallet_services.credit_topup(self.user, Decimal("1000.00"), f"SEED-{self.user.id}")
        self.client.force_authenticate(self.user)

    def _buy(self, pin, client_reference):
        with patch("services.views.purchase_airtime", return_value=DELIVERED):
            return self.client.post(
                reverse("airtime-purchase"),
                {
                    "amount": "300",
                    "network": "mtn",
                    "phone": "08031234567",
                    "client_reference": client_reference,
                    "pin": pin,
                },
                format="json",
            )

    def _balance(self):
        return Wallet.objects.get(user=self.user).balance

    def test_setting_a_pin_needs_the_account_password_and_a_hard_to_guess_pin(self):
        url = reverse("user-pin")

        wrong_password = self.client.post(url, {"pin": "2580", "password": "nope"}, format="json")
        guessable = self.client.post(url, {"pin": "1234", "password": STRONG}, format="json")
        good = self.client.post(url, {"pin": "2580", "password": STRONG}, format="json")

        self.assertIn("password", wrong_password.data)
        self.assertIn("pin", guessable.data)
        self.assertEqual(good.status_code, status.HTTP_200_OK)
        self.assertTrue(self.client.get(url).data["has_pin"])

    def test_purchases_need_the_right_pin(self):
        no_pin_set = self._buy("2580", "PIN-1")
        set_pin(self.user, "2580")
        wrong = self._buy("1111", "PIN-2")
        right = self._buy("2580", "PIN-3")

        self.assertEqual((no_pin_set.status_code, no_pin_set.data["code"]), (403, "pin_not_set"))
        self.assertEqual((wrong.status_code, wrong.data["code"], wrong.data["attempts_left"]), (403, "pin_invalid", 4))
        self.assertEqual(right.status_code, status.HTTP_201_CREATED)
        self.assertEqual(self._balance(), Decimal("700.00"))

    def test_five_wrong_pins_lock_purchases_for_a_while(self):
        set_pin(self.user, "2580")

        attempts = [self._buy("1111", f"LOCK-{i}") for i in range(TransactionPin.MAX_ATTEMPTS)]
        while_locked = self._buy("2580", "LOCK-OK-1")

        self.assertEqual(attempts[-1].data["code"], "pin_locked")
        self.assertEqual(while_locked.data["code"], "pin_locked")
        self.assertEqual(self._balance(), Decimal("1000.00"))

        TransactionPin.objects.filter(user=self.user).update(locked_until=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self._buy("2580", "LOCK-OK-2").status_code, status.HTTP_201_CREATED)

    def test_the_pin_never_reaches_the_logs_and_phone_numbers_are_masked(self):
        set_pin(self.user, "2580")

        self._buy("2580", "LOG-1")

        logged = ProviderLog.objects.get(client_reference="LOG-1")
        self.assertNotIn("pin", logged.request_payload)
        self.assertEqual(logged.request_payload["phone"], "080***4567")
        self.assertEqual(logged.response_payload["provider"]["content"]["transactions"]["phone"], "080***4567")


class ProfileTests(AuthTestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(email="me@example.com", password=STRONG, first_name="Ada")
        self.client.force_authenticate(self.user)
        self.url = reverse("user-me")

    def test_get_and_patch_profile_ignores_email_and_phone(self):
        response = self.client.patch(
            self.url, {"last_name": "Obi", "phone": "+2348031234567", "email": "new@example.com"}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["email"], "me@example.com")
        self.assertEqual(response.data["phone"], "")  # only an SMS code can set it
        self.assertFalse(response.data["phone_verified"])
        self.assertFalse(response.data["has_pin"])
        self.assertEqual(response.data["limits"]["level"], "basic")
        self.assertEqual(self.client.get(self.url).data["last_name"], "Obi")

    def test_change_password_signs_out_other_devices_and_returns_new_tokens(self):
        old_refresh = self._login("me@example.com").data["refresh"]
        url = reverse("user-change-password")

        wrong = self.client.post(
            url, {"current_password": "nope", "new_password": "Brand-New-Lantern-7"}, format="json"
        )
        weak = self.client.post(url, {"current_password": STRONG, "new_password": "123"}, format="json")
        changed = self.client.post(
            url, {"current_password": STRONG, "new_password": "Brand-New-Lantern-7"}, format="json"
        )

        self.assertEqual(wrong.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(weak.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(changed.status_code, status.HTTP_200_OK)
        self.assertIn("refresh", changed.data)
        refresh = reverse("token_refresh")
        self.assertEqual(self.client.post(refresh, {"refresh": old_refresh}, format="json").status_code, 401)
        self.assertEqual(
            self.client.post(refresh, {"refresh": changed.data["refresh"]}, format="json").status_code, 200
        )
        self.assertEqual(self._login("me@example.com", "Brand-New-Lantern-7").status_code, status.HTTP_200_OK)

    def test_closing_an_account_is_refused_while_the_wallet_holds_money(self):
        wallet_services.credit_topup(self.user, Decimal("50.00"), "PAY-CLOSE")

        response = self.client.delete(self.url, {"password": STRONG}, format="json")

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["code"], "account_not_empty")
        self.assertTrue(User.objects.get(pk=self.user.pk).is_active)

    def test_closing_an_account_anonymises_it_and_keeps_the_ledger(self):
        wallet_services.credit_topup(self.user, Decimal("50.00"), "PAY-CLOSE")
        wallet_services.adjust(self.user, Decimal("-50.00"), "Refunded to bank on request")
        set_pin(self.user, "2580")
        refresh = self._login("me@example.com").data["refresh"]

        wrong = self.client.delete(self.url, {"password": "nope"}, format="json")
        closed = self.client.delete(self.url, {"password": STRONG}, format="json")

        self.assertEqual(wrong.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(closed.status_code, status.HTTP_204_NO_CONTENT)
        user = User.objects.get(pk=self.user.pk)
        self.assertFalse(user.is_active)
        self.assertEqual((user.email, user.first_name, user.phone), (f"closed-{user.pk}@closed.invalid", "", ""))
        self.assertFalse(TransactionPin.objects.filter(user=user).exists())
        self.assertEqual(Wallet.objects.get(user=user).transactions.count(), 2)
        self.assertEqual(self._login("me@example.com").status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(
            self.client.post(reverse("token_refresh"), {"refresh": refresh}, format="json").status_code, 401
        )


@override_settings(SMS_BACKEND="locmem")
class PhoneVerificationTests(AuthTestCase):
    def setUp(self):
        super().setUp()
        sms.outbox.clear()
        self.user = User.objects.create_user(
            email="phone@example.com", password=STRONG, email_verified_at=timezone.now()
        )
        self.client.force_authenticate(self.user)

    def _request(self, phone="+234 803 123 4567"):
        return self.client.post(reverse("phone-request"), {"phone": phone}, format="json")

    def _texted_code(self):
        return re.search(r"\b(\d{6})\b", sms.outbox[-1][1]).group(1)

    def _confirm(self, code):
        return self.client.post(reverse("phone-confirm"), {"code": code}, format="json")

    def test_texted_code_verifies_the_number_and_raises_the_level(self):
        requested = self._request()
        confirmed = self._confirm(self._texted_code())

        self.assertEqual(requested.status_code, status.HTTP_200_OK)
        self.assertEqual(sms.outbox[-1][0], "08031234567")
        self.assertEqual(confirmed.status_code, status.HTTP_200_OK)
        self.assertEqual((confirmed.data["phone"], confirmed.data["phone_verified"]), ("08031234567", True))
        self.assertEqual(confirmed.data["limits"]["level"], "verified")

    def test_wrong_codes_fail_and_the_code_dies_after_five_tries(self):
        self._request()
        code = self._texted_code()
        wrong = [self._confirm("000000" if code != "000000" else "111111").status_code for _ in range(5)]

        self.assertEqual(set(wrong), {status.HTTP_400_BAD_REQUEST})
        self.assertEqual(self._confirm(code).status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(User.objects.get(pk=self.user.pk).phone_verified)

    def test_a_number_verifies_on_one_account_only(self):
        other = User.objects.create_user(email="other@example.com", password=STRONG)
        self._request()
        code = self._texted_code()
        self.client.force_authenticate(other)
        self._request()
        other_code = self._texted_code()

        self.client.force_authenticate(self.user)
        first = self._confirm(code)
        self.client.force_authenticate(other)
        second = self._confirm(other_code)  # its code was sent before the number was taken
        third = self._request()

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(second.data["code"], "phone_taken")
        self.assertEqual(third.status_code, status.HTTP_409_CONFLICT)
        self.assertFalse(User.objects.get(pk=other.pk).phone_verified)

    def test_resend_waits_a_minute_and_sms_failures_are_reported(self):
        self._request()
        too_soon = self._request()
        OneTimeCode.objects.update(created_at=timezone.now() - timedelta(minutes=2))
        with patch("users.views.send_phone_code", return_value=False):
            failed = self._request()

        self.assertEqual(too_soon.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertEqual(failed.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(failed.data["code"], "sms_failed")

    def test_closing_the_account_frees_the_number(self):
        self._request()
        self._confirm(self._texted_code())

        close_account(User.objects.get(pk=self.user.pk))

        self.assertFalse(User.objects.filter(phone="08031234567", phone_verified_at__isnull=False).exists())


class SmsBackendTests(TestCase):
    @override_settings(SMS_BACKEND="console", IS_PRODUCTION=True)
    def test_console_backend_never_prints_codes_in_production(self):
        with patch("builtins.print") as printed, self.assertLogs("notifications.sms", level="ERROR"):
            self.assertFalse(sms.send_sms("08031234567", "Your QuickSurf code is 123456."))
        printed.assert_not_called()

    @override_settings(SMS_BACKEND="termii", TERMII_API_KEY="key", TERMII_SENDER_ID="QuickSurf", TERMII_CHANNEL="dnd")
    def test_termii_gets_the_international_number_and_reports_failures(self):
        ok = Mock(status_code=200, headers={"Content-Type": "application/json"})
        ok.json.return_value = {"message_id": "123", "message": "Successfully Sent"}
        refused = Mock(status_code=400, headers={"Content-Type": "application/json"})
        refused.json.return_value = {"message": "Insufficient balance"}

        with patch("notifications.sms.requests.post", return_value=ok) as post:
            self.assertTrue(sms.send_sms("08031234567", "hello"))
        payload = post.call_args.kwargs["json"]
        with (
            patch("notifications.sms.requests.post", return_value=refused),
            self.assertLogs("notifications.sms", "ERROR"),
        ):
            self.assertFalse(sms.send_sms("08031234567", "hello"))

        self.assertEqual(
            (payload["to"], payload["from"], payload["channel"], payload["api_key"]),
            ("2348031234567", "QuickSurf", "dnd", "key"),
        )


@override_settings(
    TRANSACTION_PIN_REQUIRED=False, BASIC_DAILY_PURCHASE_LIMIT=500, DAILY_PURCHASE_LIMIT=5000, NEW_ACCOUNT_DAILY_LIMIT=0
)
class VerificationLevelTests(AuthTestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(
            email="levels@example.com", password=STRONG, email_verified_at=timezone.now()
        )
        wallet_services.credit_topup(self.user, Decimal("3000.00"), "PAY-LEVELS")
        self.client.force_authenticate(self.user)

    def _buy(self, reference, amount="400"):
        with patch("services.views.purchase_airtime", return_value=DELIVERED):
            return self.client.post(
                reverse("airtime-purchase"),
                {"amount": amount, "network": "mtn", "phone": "08031234567", "client_reference": reference},
                format="json",
            )

    def test_basic_accounts_get_the_basic_limit_until_the_phone_is_verified(self):
        self.assertEqual(self._buy("LV-1").status_code, status.HTTP_201_CREATED)
        refused = self._buy("LV-2")
        User.objects.filter(pk=self.user.pk).update(phone="08031234567", phone_verified_at=timezone.now())
        self.user.refresh_from_db()
        self.client.force_authenticate(self.user)
        allowed = self._buy("LV-3")

        self.assertEqual(refused.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual((refused.data["code"], refused.data["level"]), ("daily_limit", "basic"))
        self.assertEqual(allowed.status_code, status.HTTP_201_CREATED)

    def test_a_flagged_account_drops_back_to_basic(self):
        User.objects.filter(pk=self.user.pk).update(
            phone="08031234567", phone_verified_at=timezone.now(), flagged_at=timezone.now(), flag_reason="test"
        )
        self.user.refresh_from_db()

        self.assertEqual(limits.level(self.user), "basic")
        self.assertEqual(limits.daily_purchase_limit(self.user), 500)


@override_settings(DEVICE_MAX_ACCOUNTS=3)
class DeviceCheckTests(AuthTestCase):
    def _sign_in_everyone(self, count, **headers):
        emails = [f"dev{i}-{len(headers)}@example.com" for i in range(count)]
        for email in emails:
            User.objects.create_user(email=email, password=STRONG)
            self.client.post(reverse("user-login"), {"email": email, "password": STRONG}, format="json", **headers)
        return [User.objects.get(email=email) for email in emails]

    def test_one_device_signing_in_to_too_many_accounts_flags_the_newest(self):
        users = self._sign_in_everyone(4, HTTP_X_DEVICE_ID="device-abc")

        self.assertEqual([bool(u.flagged_at) for u in users], [False, False, False, True])
        self.assertIn("4 accounts", users[3].flag_reason)
        self.assertEqual(LoginActivity.objects.filter(user=users[0]).first().device_fingerprint, "d:device-abc")

    def test_without_an_app_device_id_shared_ips_never_flag_anyone(self):
        users = self._sign_in_everyone(5)  # same browser and IP for all: like a busy mobile network

        self.assertFalse(any(u.flagged_at for u in users))
