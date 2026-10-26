from django.apps import AppConfig


class UsersConfig(AppConfig):
    # Signal handlers live in users/models.py, which Django imports when the app loads.
    default_auto_field = "django.db.models.BigAutoField"
    name = "users"
