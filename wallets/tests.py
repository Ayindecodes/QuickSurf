import importlib
import io
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from unittest import skipUnless

from django.apps import apps
from django.contrib.auth.models import Permission
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from services.models import AirtimeTransaction
from users.models import User
from wallets import services as wallet_services
from wallets.models import Transaction, Wallet


def _check_wallets():
    """Raises CommandError if any wallet doesn't equal the sum of its ledger."""
    call_command("check_wallets", stdout=io.StringIO())


class WalletServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="ledger@example.com", password="StrongPass123")

    def _wallet(self):
        return Wallet.objects.get(user=self.user)

    def test_topup_is_credited_once_per_reference(self):
        self.assertTrue(wallet_services.credit_topup(self.user, Decimal("1000.00"), "PAY-1"))
        self.assertFalse(wallet_services.credit_topup(self.user, Decimal("1000.00"), "PAY-1"))

        self.assertEqual(self._wallet().balance, Decimal("1000.00"))
        self.assertEqual(Transaction.objects.filter(type="topup", reference="PAY-1").count(), 1)
        _check_wallets()

    def test_capture_spends_the_held_money_once(self):
        wallet_services.credit_topup(self.user, Decimal("1000.00"), "PAY-2")
        wallet_services.hold(self.user, Decimal("300.00"), "airtime:A-1")
        wallet = self._wallet()
        self.assertEqual((wallet.balance, wallet.locked), (Decimal("700.00"), Decimal("300.00")))

        self.assertTrue(wallet_services.capture(self.user, "airtime:A-1"))
        self.assertFalse(wallet_services.capture(self.user, "airtime:A-1"))
        self.assertFalse(wallet_services.release(self.user, "airtime:A-1"))

        wallet = self._wallet()
        self.assertEqual((wallet.balance, wallet.locked), (Decimal("700.00"), Decimal("0.00")))
        _check_wallets()

    def test_release_returns_the_held_money_once(self):
        wallet_services.credit_topup(self.user, Decimal("1000.00"), "PAY-3")
        wallet_services.hold(self.user, Decimal("300.00"), "data:D-1")

        self.assertTrue(wallet_services.release(self.user, "data:D-1"))
        self.assertFalse(wallet_services.release(self.user, "data:D-1"))
        self.assertFalse(wallet_services.capture(self.user, "data:D-1"))

        wallet = self._wallet()
        self.assertEqual((wallet.balance, wallet.locked), (Decimal("1000.00"), Decimal("0.00")))
        _check_wallets()

    def test_hold_refuses_more_than_the_balance(self):
        wallet_services.credit_topup(self.user, Decimal("100.00"), "PAY-4")

        with self.assertRaises(wallet_services.InsufficientFunds):
            wallet_services.hold(self.user, Decimal("100.01"), "airtime:A-2")

        self.assertFalse(Transaction.objects.filter(type="hold").exists())
        self.assertEqual(self._wallet().balance, Decimal("100.00"))

    def test_adjustment_needs_a_note_and_cannot_go_below_zero(self):
        staff = User.objects.create_user(email="staff@example.com", password="StrongPass123", is_staff=True)
        wallet_services.credit_topup(self.user, Decimal("100.00"), "PAY-5")

        with self.assertRaises(ValueError):
            wallet_services.adjust(self.user, Decimal("50.00"), note="  ")
        with self.assertRaises(wallet_services.InsufficientFunds):
            wallet_services.adjust(self.user, Decimal("-150.00"), note="Reverse duplicate credit")

        entry = wallet_services.adjust(self.user, Decimal("-40.00"), note="Reverse duplicate credit", created_by=staff)

        self.assertEqual(entry.balance_delta, Decimal("-40.00"))
        self.assertEqual(entry.created_by, staff)
        self.assertEqual(self._wallet().balance, Decimal("60.00"))
        _check_wallets()

    def test_check_wallets_flags_a_balance_changed_outside_the_ledger(self):
        wallet_services.credit_topup(self.user, Decimal("100.00"), "PAY-6")
        Wallet.objects.filter(user=self.user).update(balance=Decimal("500.00"))

        with self.assertRaises(CommandError):
            _check_wallets()


@skipUnless(connection.vendor == "postgresql", "needs real row locks (Postgres)")
class WalletConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="race@example.com", password="StrongPass123")
        wallet_services.credit_topup(self.user, Decimal("1000.00"), "PAY-RACE-SEED")

    def _in_parallel(self, attempt, count=8):
        def run(i):
            try:
                return attempt(i)
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=count) as pool:
            return list(pool.map(run, range(count)))

    def test_concurrent_purchases_never_overspend_the_wallet(self):
        def attempt(i):
            try:
                wallet_services.hold(self.user, Decimal("300.00"), f"airtime:RACE-{i}")
                return True
            except wallet_services.InsufficientFunds:
                return False

        results = self._in_parallel(attempt)

        wallet = Wallet.objects.get(user=self.user)
        self.assertEqual(results.count(True), 3)
        self.assertEqual((wallet.balance, wallet.locked), (Decimal("100.00"), Decimal("900.00")))
        _check_wallets()

    def test_concurrent_credits_for_one_payment_credit_it_once(self):
        results = self._in_parallel(
            lambda i: wallet_services.credit_topup(self.user, Decimal("500.00"), "PAY-RACE-1"))

        self.assertEqual(results.count(True), 1)
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("1500.00"))
        _check_wallets()


class LedgerOpeningMigrationTests(TestCase):
    def test_opening_rows_match_current_balances_and_keep_pending_purchases_settleable(self):
        user = User.objects.create_user(email="old@example.com", password="StrongPass123")
        Wallet.objects.filter(user=user).update(balance=Decimal("700.00"), locked=Decimal("300.00"))
        AirtimeTransaction.objects.create(user=user, network="mtn", phone="08031234567", amount=Decimal("300.00"),
                                          status="pending", client_reference="AIR-OLD-1")

        migration = importlib.import_module("wallets.migrations.0004_ledger_opening_balances")
        migration.open_ledger(apps, None)

        _check_wallets()
        wallet = Wallet.objects.get(user=user)
        self.assertEqual((wallet.balance, wallet.locked), (Decimal("700.00"), Decimal("300.00")))

        self.assertTrue(wallet_services.release(user, "airtime:AIR-OLD-1"))
        wallet.refresh_from_db()
        self.assertEqual((wallet.balance, wallet.locked), (Decimal("1000.00"), Decimal("0.00")))
        _check_wallets()

    def test_stale_locked_money_is_listed_then_removed_not_refunded(self):
        # The old lock endpoint added 200 to locked without taking it from balance.
        user = User.objects.create_user(email="stale@example.com", password="StrongPass123")
        Wallet.objects.filter(user=user).update(balance=Decimal("70.00"), locked=Decimal("230.00"))
        AirtimeTransaction.objects.create(user=user, network="mtn", phone="08031234567", amount=Decimal("30.00"),
                                          status="pending", client_reference="AIR-OLD-2")
        importlib.import_module("wallets.migrations.0004_ledger_opening_balances").open_ledger(apps, None)

        listing = io.StringIO()
        call_command("clear_stale_locked", stdout=listing)
        self.assertIn("200.00 locked with no pending purchase", listing.getvalue())
        self.assertEqual(Wallet.objects.get(user=user).locked, Decimal("230.00"))

        call_command("clear_stale_locked", "--apply", stdout=io.StringIO())

        wallet = Wallet.objects.get(user=user)
        self.assertEqual((wallet.balance, wallet.locked), (Decimal("70.00"), Decimal("30.00")))
        self.assertTrue(wallet_services.release(user, "airtime:AIR-OLD-2"))
        wallet.refresh_from_db()
        self.assertEqual((wallet.balance, wallet.locked), (Decimal("100.00"), Decimal("0.00")))
        _check_wallets()


class WalletEndpointTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="wallet@example.com", password="StrongPass123")
        wallet_services.credit_topup(self.user, Decimal("2000.00"), "PAY-SEED")
        self.client.force_authenticate(self.user)

    def test_transactions_list_shows_ledger_entries(self):
        response = self.client.get(reverse("wallet-transactions"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        [entry] = response.data["results"]
        self.assertEqual(entry["type"], "topup")
        self.assertEqual(entry["reference"], "PAY-SEED")
        self.assertEqual(entry["balance_after"], "2000.00")

    def test_summary_returns_balance_and_locked(self):
        response = self.client.get(reverse("wallet-summary"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["balance"], "2000.00")
        self.assertEqual(response.data["locked"], "0.00")

    def test_users_cannot_lock_or_unlock_funds_any_more(self):
        self.assertEqual(self.client.post("/api/wallet/lock/", {"amount": "500.00"}).status_code, 404)
        self.assertEqual(self.client.post("/api/wallet/unlock/", {"amount": "500.00"}).status_code, 404)

    def test_adjustments_need_the_adjust_permission_and_a_note(self):
        url = reverse("wallet-adjust")
        payload = {"user": self.user.id, "amount": "-500.00", "note": "Reverse duplicate Paystack credit"}

        self.assertEqual(self.client.post(url, payload, format="json").status_code, status.HTTP_403_FORBIDDEN)

        staff = User.objects.create_user(email="ops@example.com", password="StrongPass123", is_staff=True)
        self.client.force_authenticate(staff)
        self.assertEqual(self.client.post(url, payload, format="json").status_code, status.HTTP_403_FORBIDDEN)
        staff.user_permissions.add(Permission.objects.get(codename="adjust_wallet"))  # the Finance role
        staff = User.objects.get(pk=staff.pk)  # reload: permissions are cached on the object
        self.client.force_authenticate(staff)
        no_note = self.client.post(url, {"user": self.user.id, "amount": "-500.00"}, format="json")
        adjusted = self.client.post(url, payload, format="json")

        self.assertEqual(no_note.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(adjusted.status_code, status.HTTP_201_CREATED)
        entry = Transaction.objects.get(type="adjustment")
        self.assertEqual(entry.created_by, staff)
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("1500.00"))
