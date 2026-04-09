from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from core.models import LoginActivity
from users.models import User
from wallets.models import Wallet


class AuthFlowTests(APITestCase):
    def test_register_returns_tokens_and_creates_wallet(self):
        response = self.client.post(
            reverse("user-register"),
            {
                "email": "tester@example.com",
                "password": "StrongPass123",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)

        user = User.objects.get(email="tester@example.com")
        wallet = Wallet.objects.get(user=user)

        self.assertEqual(wallet.balance, 0)
        self.assertEqual(wallet.locked, 0)
        self.assertEqual(LoginActivity.objects.filter(user=user).count(), 1)
