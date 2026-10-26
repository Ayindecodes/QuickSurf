# core/request_id.py
"""
Every request gets an ID, returned in the X-Request-ID header and added to every log line written
while handling it, so one customer report can be traced through the logs.
"""

import contextvars
import json
import logging
import re
import uuid

_current = contextvars.ContextVar("request_id", default="-")
_VALID_INCOMING = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


def current_request_id() -> str:
    return _current.get()


class RequestIdMiddleware:
    """Use the caller's X-Request-ID if it looks sane (e.g. from a proxy), otherwise make one."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        incoming = request.headers.get("X-Request-ID", "")
        request_id = incoming if _VALID_INCOMING.match(incoming) else uuid.uuid4().hex
        request.request_id = request_id
        token = _current.set(request_id)
        try:
            response = self.get_response(request)
        finally:
            _current.reset(token)
        response["X-Request-ID"] = request_id
        return response


class RequestIdFilter(logging.Filter):
    """Adds `request_id` to every log record ("-" outside a request, e.g. in cron jobs)."""

    def filter(self, record):
        # Django logs request errors after the response has left the middleware, but attaches the request.
        request = getattr(record, "request", None)
        record.request_id = getattr(request, "request_id", None) or current_request_id()
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line, which log search tools can filter on."""

    def format(self, record):
        entry = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", "-"),
            "message": record.getMessage(),
        }
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)
