# core/exceptions.py
import logging

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler

logger = logging.getLogger(__name__)


def api_exception_handler(exc, context):
    """DRF's handler, plus a JSON 500 for unexpected errors that never includes internal details."""
    response = exception_handler(exc, context)
    if response is None:
        view = context.get("view")
        logger.error("Unhandled error in %s", view.__class__.__name__ if view else "API", exc_info=exc)
        return Response({"error": "Something went wrong. Please try again.", "code": "server_error"},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    return response
