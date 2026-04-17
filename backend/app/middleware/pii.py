"""
PII redaction helpers for log output.

Usage:
    from app.middleware.pii import mask_email
    logger.info("Connected: %s", mask_email("alice@example.com"))
    # → "Connected: a***@example.com"
"""

import re

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")


def mask_email(email: str | None) -> str:
    """Mask an email address for safe logging: 'alice@example.com' → 'a***@example.com'."""
    if not email or "@" not in email:
        return "<no-email>"
    local, domain = email.split("@", 1)
    return f"{local[0]}***@{domain}" if local else f"***@{domain}"


def redact_pii(text: str) -> str:
    """Replace all email addresses in a string with masked versions."""
    return _EMAIL_RE.sub(lambda m: mask_email(m.group(0)), text)
