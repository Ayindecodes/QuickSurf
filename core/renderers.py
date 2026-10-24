# core/renderers.py
from rest_framework.renderers import JSONRenderer

from .request_id import current_request_id

DEFAULT_CODES = {
    400: "invalid",
    401: "not_authenticated",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    429: "throttled",  # same code DRF gives its own rate limits
    500: "server_error",
    502: "provider_error",
    503: "unavailable",
}


def error_envelope(data, status_code: int) -> dict:
    """
    Every /api/v1/ error in one shape:
        {"error": {"code": "...", "message": "...", "fields": {...}, ...extra}, "request_id": "..."}
    built from what views and DRF return: {"detail": ...}, {"error": ..., "code": ..., <extra>}, or
    field errors ({"field": ["message"]}). `fields` only appears for field errors; extra values such as
    `price` or `attempts_left` are kept next to the message.
    """
    if isinstance(data, dict) and isinstance(data.get("error"), dict):
        return data
    message, code, fields, extra = None, None, {}, {}
    if isinstance(data, list):
        message = " ".join(str(item) for item in data)
    elif isinstance(data, dict):
        for key, value in data.items():
            if key in ("error", "detail"):
                message = str(value)
                code = code or getattr(value, "code", None)
            elif key == "code" and not isinstance(value, (list, dict)):  # a list is a form field named "code"
                code = str(value)
            elif key in ("request_id", "messages"):  # messages: simplejwt's token internals, not field errors
                continue
            elif isinstance(value, (list, dict)):
                fields[key] = value
            else:
                extra[key] = value
    if message is None:
        message = "Please correct the highlighted fields." if fields else "The request failed."
    error = {"code": code or DEFAULT_CODES.get(status_code, "error"), "message": message, **extra}
    if fields:
        error["fields"] = fields
    return {"error": error, "request_id": current_request_id()}


class ApiJSONRenderer(JSONRenderer):
    """JSON, with /api/v1/ errors in one shape (error_envelope). Unversioned /api/ responses are unchanged."""

    def render(self, data, accepted_media_type=None, renderer_context=None):
        context = renderer_context or {}
        response, request = context.get("response"), context.get("request")
        if (
            response is not None
            and request is not None
            and response.status_code >= 400
            and request.path.startswith("/api/v1/")
        ):
            data = error_envelope(data, response.status_code)
        return super().render(data, accepted_media_type, renderer_context)
