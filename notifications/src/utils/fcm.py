"""FCM token validation utilities."""
import re
from typing import Tuple


def validate_fcm_token(fcm_token: str) -> Tuple[bool, str]:
    """Validate FCM token format.

    FCM tokens typically:
    - Are 152-163 characters long
    - Contain alphanumeric characters and some special characters (:, -, _)
    - May start with specific prefixes depending on platform

    Args:
        fcm_token: FCM token string to validate

    Returns:
        Tuple of (is_valid, error_message)
    """
    if not fcm_token or not isinstance(fcm_token, str):
        return False, "FCM token must be a non-empty string"

    # Check length (FCM tokens are typically 152-163 characters)
    if len(fcm_token) < 140 or len(fcm_token) > 200:
        return (
            False,
            f"FCM token length must be between 140-200 characters, got {len(fcm_token)}",
        )

    # Check format: alphanumeric with allowed special characters
    # FCM tokens can contain: letters, numbers, colons, hyphens, underscores
    pattern = r"^[a-zA-Z0-9:_-]+$"
    if not re.match(pattern, fcm_token):
        return False, "FCM token contains invalid characters. Only alphanumeric, colon, hyphen, and underscore are allowed"

    return True, ""

