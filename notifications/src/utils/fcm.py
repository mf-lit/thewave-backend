"""FCM token validation utilities."""
import re
from typing import Tuple

FCM_TOKEN_MIN_LENGTH = 140
FCM_TOKEN_MAX_LENGTH = 200
FCM_TOKEN_PATTERN = re.compile(r"^[a-zA-Z0-9:_-]+$")


def validate_fcm_token(fcm_token: str) -> Tuple[bool, str]:
    """Validate FCM token format.

    FCM tokens are typically 152-163 characters, containing alphanumeric
    characters and special characters (:, -, _).
    """
    if not fcm_token or not isinstance(fcm_token, str):
        return False, "FCM token must be a non-empty string"

    token_length = len(fcm_token)
    if not (FCM_TOKEN_MIN_LENGTH <= token_length <= FCM_TOKEN_MAX_LENGTH):
        return False, f"FCM token length must be between {FCM_TOKEN_MIN_LENGTH}-{FCM_TOKEN_MAX_LENGTH} characters, got {token_length}"

    if not FCM_TOKEN_PATTERN.match(fcm_token):
        return False, "FCM token contains invalid characters. Only alphanumeric, colon, hyphen, and underscore are allowed"

    return True, ""

