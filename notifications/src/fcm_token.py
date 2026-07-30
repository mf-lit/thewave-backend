"""FCM registration token format checks.

Real tokens are ~152-163 characters of ``[A-Za-z0-9:_-]``. The bounds are
deliberately loose either side of that; the point is to reject obvious
rubbish before it reaches the clients table, not to authenticate the token.
"""
from __future__ import annotations

import re

from .models import ValidationError

MIN_LENGTH = 140
MAX_LENGTH = 200
TOKEN_PATTERN = re.compile(r"^[a-zA-Z0-9:_-]+$")


def validate_fcm_token(token: str) -> str:
    """Return the token, or raise ValidationError with the client-facing message."""
    if not token or not isinstance(token, str):
        raise ValidationError("FCM token must be a non-empty string")

    length = len(token)
    if not (MIN_LENGTH <= length <= MAX_LENGTH):
        raise ValidationError(
            f"FCM token length must be between {MIN_LENGTH}-{MAX_LENGTH} characters, "
            f"got {length}"
        )

    if not TOKEN_PATTERN.match(token):
        raise ValidationError(
            "FCM token contains invalid characters. Only alphanumeric, colon, "
            "hyphen, and underscore are allowed"
        )
    return token
