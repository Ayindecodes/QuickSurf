# users/adapters.py

from allauth.account.adapter import DefaultAccountAdapter
from allauth.account.models import EmailAddress
from allauth.account.utils import setup_user_email

class NoUsernameAccountAdapter(DefaultAccountAdapter):
    def save_user(self, request, user, form, commit=True):
        data = form.cleaned_data
        user.email = data.get('email')
        user.set_password(data.get("password1"))
        user.is_active = True  # keep as True since we're confirming email immediately

        if commit:
            user.save()

        # Set up and auto-confirm the email
        setup_user_email(request, user, [])
        EmailAddress.objects.update_or_create(
            user=user,
            email=user.email,
            defaults={'primary': True, 'verified': True}
        )

        return user

