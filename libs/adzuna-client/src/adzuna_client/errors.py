class AdzunaAPIError(Exception):
    """Deliberately doesn't subclass httpx.HTTPStatusError and doesn't carry
    the original httpx.Request/Response - those objects hold the
    unredacted URL (with app_id/app_key as query params) internally, and
    a caller inspecting e.request.url, or a logging/telemetry layer that
    serializes the exception more deeply than str(e), would still leak
    the credential even after the message text was redacted. Confirmed
    by an independent Codex review: message-only redaction wasn't enough."""

    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        super().__init__(message)
