import re
import time
from email.utils import mktime_tz, parsedate_tz

import httpx2
from tenacity import RetryCallState, wait_exponential
from tenacity.retry import retry_base

DEFAULT_RETRY_STATUS = (429, *range(500, 600))


def get_retry_response(state: RetryCallState) -> httpx2.Response | None:
    """Read a response returned normally or attached to a hook-raised HTTP error."""
    outcome = state.outcome
    if outcome is None:
        return None
    if outcome.failed:
        exception = outcome.exception()
        return (
            exception.response
            if isinstance(exception, httpx2.HTTPStatusError)
            else None
        )
    result = outcome.result()
    return result if isinstance(result, httpx2.Response) else None


class retry_if_status(retry_base):
    """Retry DLT's default statuses, including errors raised by response hooks."""

    def __call__(self, retry_state: RetryCallState) -> bool:
        response = get_retry_response(retry_state)
        return response is not None and response.status_code in DEFAULT_RETRY_STATUS


class wait_retry_after(wait_exponential):
    """DLT-alike retry, use Retry-After when valid, otherwise use exponential backoff."""

    def __call__(self, retry_state: RetryCallState) -> float:
        response = get_retry_response(retry_state)
        if response is not None:
            value = response.headers.get("Retry-After", "")
            if re.fullmatch(r"\s*[0-9]+\s*", value):
                try:
                    seconds = int(value)
                except ValueError:
                    return super().__call__(retry_state)
                return max(self.min, min(self.max, seconds))
            date = parsedate_tz(value)
            if date is not None:
                return max(self.min, min(self.max, mktime_tz(date) - time.time()))
        return super().__call__(retry_state)
