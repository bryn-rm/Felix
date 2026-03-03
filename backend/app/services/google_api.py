"""
Shared Google API helpers used by both GmailService and CalendarService.
"""

import asyncio
import logging

from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)


async def execute_with_backoff(request, context: str = "", max_retries: int = 3):
    """
    Execute a Google API request with exponential back-off on HTTP 429.

    Waits 2^attempt seconds before each retry (2s, 4s, 8s).
    Raises the HttpError after max_retries exhausted.
    """
    for attempt in range(max_retries + 1):
        try:
            return await asyncio.to_thread(request.execute)
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
