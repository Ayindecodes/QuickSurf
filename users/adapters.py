# users/adapters.py

from allauth.account.adapter import DefaultAccountAdapter


class StaffOnlyAccountAdapter(DefaultAccountAdapter):
    """
    allauth's pages are only for staff signing in to the admin (with an authenticator app).
    Customers sign up and log in through the JWT API, so public signup here is closed.
    """

    def is_open_for_signup(self, request):
        return False
