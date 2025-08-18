from .models import ProviderLog

def make_provider_logger(user):
    def _save(direction, endpoint, payload, status_code=None, provider="vtpass"):
        ProviderLog.objects.create(
            user=user, provider=provider, direction=direction,
            endpoint=endpoint, payload=payload, status_code=status_code
        )
    return _save
