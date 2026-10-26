import io
from decimal import Decimal

from django.core.cache import cache
from django.core.management import call_command
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from rewards.models import LoyaltyLedger
from users.models import User
from wallets.models import Transaction, Wallet


class RewardsTests(APITestCase):
    def setUp(self):
        cache.clear()  # the redeem rate limit lives in the cache
        self.user = User.objects.create_user(email="points@example.com", password="StrongPass123")
        LoyaltyLedger.objects.create(
            user=self.user, points=150, reason="Airtime purchase", txn_type="airtime", txn_id="1"
        )
        self.client.force_authenticate(self.user)

    def _redeem(self, points):
        return self.client.post(reverse("rewards-redeem"), {"points": points}, format="json")

    def test_rewards_shows_points_and_their_value(self):
        response = self.client.get(reverse("rewards"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["points"], 150)
        self.assertEqual(response.data["naira_value"], "150.00")
        self.assertEqual(response.data["min_redemption"], 100)
        self.assertEqual(response.data["recent"][0]["points"], 150)

    def test_redeeming_credits_the_wallet_through_the_ledger(self):
        response = self._redeem(120)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {"credited": "120.00", "points": 30, "balance": "120.00"})
        entry = Transaction.objects.get(type="reward")
        redemption = LoyaltyLedger.objects.get(txn_type="redemption")
        self.assertEqual((entry.reference, redemption.points), (redemption.txn_id, -120))
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("120.00"))
        call_command("check_wallets", stdout=io.StringIO())

    def test_redemption_needs_the_minimum_and_enough_points(self):
        too_few = self._redeem(50)
        too_many = self._redeem(200)

        self.assertEqual(too_few.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(too_few.data["code"], "redemption_refused")
        self.assertEqual(too_many.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(too_many.data["error"], "You have 150 points.")
        self.assertFalse(Transaction.objects.filter(type="reward").exists())

    @override_settings(REWARDS_ENABLED=False)
    def test_nothing_is_redeemed_while_rewards_are_off(self):
        self.assertEqual(self._redeem(100).status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("0.00"))

    @override_settings(REWARD_POINT_VALUE=Decimal("0.50"))
    def test_point_value_sets_the_naira_credited(self):
        self.assertEqual(self._redeem(100).data["credited"], "50.00")
