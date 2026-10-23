# 2. Versioned API and one error format

- Status: accepted
- Date: 2026-10-23

## Context

The API grew without versioning. Errors came in at least four shapes:
- `{"detail": ...}`;
- `{"error": ...}`;
- DRF field errors;
- `{"error": ..., "code": ...}`.

Few of them carried a code a client could act on, so a frontend had to match on English message text.
The old frontend depends on the current shapes and can't change at the same moment as the backend.

## Decision

- **Every endpoint is also served under `/api/v1/`.** The unversioned `/api/` paths stay, unchanged,
  until the old frontend is gone. The OpenAPI schema lists only the v1 paths.
- **On `/api/v1/` every error response has the same shape,** built by a renderer
  (`core/renderers.py`) from whatever the view returned:
  ```json
  {"error": {"code": "...", "message": "...", "fields": {...}, ...extra}, "request_id": "..."}
  ```
  - `code` is stable and machine-readable. It is the view's own code (`insufficient_funds`,
    `pin_invalid`), DRF's (`not_authenticated`, `throttled`), or one picked by HTTP status
    (`invalid`, `not_found`).
  - `fields` appears only for validation errors.
  - Extra values a client needs (`price`, `attempts_left`, `locked_until`) sit next to the message.
- **Views keep returning simple dicts.** The envelope is applied in one place, so legacy responses
  are untouched.

## Consequences

- Clients switch on `code`, show `message`, and can quote `request_id` to support.
- New error cases need a `code` in the view (tests assert the important ones), and they need adding to
  `docs/API.md`.
- Breaking changes go into a `/api/v2/` later, rather than changing v1 under existing clients.
- The unversioned paths are a second public surface until they are removed. Remove them once the new
  frontend is the only client.
