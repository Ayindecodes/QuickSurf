# core/net.py
from django.conf import settings


def client_ip(request) -> str | None:
    """
    The caller's IP address, from the header the proxy in front of us sets (CLIENT_IP_HEADER):
    CF-Connecting-IP behind Cloudflare (Render), X-Real-IP behind our own Caddy (deploy/Caddyfile).
    That proxy overwrites the header, so clients can't fake it. With no header configured, the
    connection's address is used. X-Forwarded-For is never trusted: its first entry is whatever
    the client chose to send.
    """
    header = settings.CLIENT_IP_HEADER
    if header:
        value = request.META.get("HTTP_" + header.upper().replace("-", "_"), "").strip()
        if value:
            return value
    return request.META.get("REMOTE_ADDR")
