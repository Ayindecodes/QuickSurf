# core/schema.py


def canonical_paths_only(endpoints):
    """
    drf-spectacular preprocessing hook: document each operation once, under /api/v1/. The unversioned
    /api/ routes and no-trailing-slash aliases still work (older clients, payment callbacks) but aren't
    documented.
    """
    return [endpoint for endpoint in endpoints if endpoint[0].startswith("/api/v1/") and endpoint[0].endswith("/")]
