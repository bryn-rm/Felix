"""
Shared Google API helpers used by both GmailService and CalendarService.
"""

import asyncio
import logging

from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

# Wall-clock cap on a single execute() call. Intentionally longer than the
# underlying httplib2 socket timeout (20s in gmail_service / calendar_service)
# so the socket fires first and the worker thread exits cleanly instead of
# being orphaned when wait_for cancels.
EXECUTE_TIMEOUT_SECONDS = 25.0


async def execute_with_backoff(request, context: str = "", max_retries: int = 3):
    """
    Execute a Google API request with exponential back-off.

    Retries on HTTP 429 and on asyncio.TimeoutError (socket/wall-clock stalls).
    Waits 2^attempt seconds before each retry (2s, 4s, 8s).
    Raises after max_retries exhausted.
    """
    for attempt in range(max_retries + 1):
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(request.execute),
                timeout=EXECUTE_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            if attempt < max_retries:
                wait_seconds = 2 ** (attempt + 1)
                logger.warning(
                    "Google API timeout [%s] after %.1fs — retrying in %ds (attempt %d/%d)",
                    context, EXECUTE_TIMEOUT_SECONDS, wait_seconds, attempt + 1, max_retries,
                )
                await asyncio.sleep(wait_seconds)
                continue
            logger.error(
                "Google API timeout [%s] after %.1fs — giving up after %d attempts",
                context, EXECUTE_TIMEOUT_SECONDS, max_retries + 1,
            )
            raise
        except HttpError as e:
            code = e.resp.status if hasattr(e, "resp") else None
            if code == 429 and attempt < max_retries:
                wait_seconds = 2 ** (attempt + 1)
                logger.warning(
                    "Google API 429 rate limit [%s] — retrying in %ds (attempt %d/%d)",
                    context, wait_seconds, attempt + 1, max_retries,
                )
                await asyncio.sleep(wait_seconds)
                continue
            raise
