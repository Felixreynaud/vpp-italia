"""Exceptions Huawei SmartPVMS NBI — codes d'erreur officiels v24.6."""

from __future__ import annotations


class HuaweiError(Exception):
    """Base exception for all Huawei connector errors."""


class HuaweiAuthError(HuaweiError):
    """Authentication or authorisation failure.

    Raised on HTTP 401 or Huawei failCode 401 (no permission)
    and failCode 305 (session expired).
    """

    def __init__(self, message: str, fail_code: int | None = None) -> None:
        super().__init__(message)
        self.fail_code = fail_code


class HuaweiAPIError(HuaweiError):
    """Generic API error returned by Huawei endpoints.

    Attributes:
        fail_code: Huawei internal error code from the response body.
        http_status: HTTP status code if available.
    """

    # Huawei documented fail codes
    FAIL_CODES: dict[int, str] = {
        305: "Session expired — token must be refreshed",
        401: "No permission for this operation",
        407: "Access frequency too high — rate limit exceeded (1 call / 5 min)",
        429: "Too many requests — back off and retry",
    }

    def __init__(
        self,
        message: str,
        fail_code: int | None = None,
        http_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.fail_code = fail_code
        self.http_status = http_status

    @classmethod
    def from_fail_code(cls, fail_code: int, detail: str = "") -> "HuaweiAPIError":
        description = cls.FAIL_CODES.get(fail_code, f"Unknown Huawei error {fail_code}")
        message = f"[{fail_code}] {description}"
        if detail:
            message += f" — {detail}"
        return cls(message, fail_code=fail_code)

    @property
    def is_rate_limit(self) -> bool:
        return self.fail_code in (407, 429)

    @property
    def is_session_expired(self) -> bool:
        return self.fail_code == 305


class HuaweiTaskError(HuaweiError):
    """Async dispatch task failed or timed out.

    Attributes:
        request_id: Huawei task identifier for the failed command.
        task_status: Raw status code returned (2 = timeout).
    """

    def __init__(self, message: str, request_id: str | None = None, task_status: int | None = None) -> None:
        super().__init__(message)
        self.request_id = request_id
        self.task_status = task_status
