from django.db import IntegrityError
from .models import IdempotencyKey

class DuplicateRequest(Exception): ...

def ensure(user, key: str):
    try:
        obj, created = IdempotencyKey.objects.get_or_create(user=user, key=key)
    except IntegrityError:
        obj = IdempotencyKey.objects.get(user=user, key=key)
        created = False
    if not created and obj.success:
        raise DuplicateRequest("Duplicate request")
    return obj

def finalize(user, key: str, response_json: dict | None = None):
    IdempotencyKey.objects.filter(user=user, key=key).update(success=True, response_json=response_json or {})
