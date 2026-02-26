# services/provider.py
from django.conf import settings

# IMPORTANT: do not import vtpass_mock anywhere else at import-time
if settings.PROVIDER_MODE == "LIVE":
    from . import vtpass_client as impl
else:
    from . import vtpass_mock as impl  # still useful locally

# Public surface your views will call
vtpass_balance = impl.vtpass_balance
vtpass_pay = impl.vtpass_pay
vtpass_requery = impl.vtpass_requery
vtpass_variations = impl.vtpass_variations
