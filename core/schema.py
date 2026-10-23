# core/schema.py


def canonical_paths_only(endpoints):
    """
    drf-spectacular preprocessing hook: drop the no-trailing-slash aliases (kept so older clients
    still work) so each operation appears once in the API docs.
    """
    return [endpoint for endpoint in endpoints if endpoint[0].endswith("/")]
