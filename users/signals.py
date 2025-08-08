# users/signals.py

from django.dispatch import receiver
from allauth.account.signals import user_signed_up
from wallets.models import Wallet

@receiver(user_signed_up)
def create_wallet_for_new_user(request, user, **kwargs):
    Wallet.objects.get_or_create(user=user)
