# core/net.py


def client_ip(request) -> str | None:
    """
    The caller's IP address. On Render, requests arrive through Cloudflare, which sets
    CF-Connecting-IP to the real client and overwrites any value the client sends.
    X-Forwarded-For isn't trusted: its first entry is whatever the client chose to send.
    """
    return request.META.get("HTTP_CF_CONNECTING_IP") or request.META.get("REMOTE_ADDR")
