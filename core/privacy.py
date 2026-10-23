# core/privacy.py
"""Keep personal data and secrets out of logs."""

MASKED_KEYS = {"phone", "unique_element", "billersCode", "email"}
DROPPED_KEYS = {"pin", "password", "new_password"}


def mask_value(value) -> str:
    if value in (None, ""):
        return ""
    text = str(value)
    if "@" in text:
        name, _, domain = text.partition("@")
        return f"{name[:2]}***@{domain}"
    if text.isdigit() and len(text) >= 7:
        return f"{text[:3]}***{text[-4:]}"
    return f"{text[:3]}***{text[-3:]}" if len(text) > 6 else "***"


def mask_pii(data):
    """A copy of data with phone numbers and emails masked and PINs/passwords removed."""
    if isinstance(data, dict):
        return {
            key: (mask_value(value) if key in MASKED_KEYS else mask_pii(value))
            for key, value in data.items()
            if key not in DROPPED_KEYS
        }
    if isinstance(data, list):
        return [mask_pii(item) for item in data]
    return data
