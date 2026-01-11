"""Authentication middleware for Flask API."""
import logging
from functools import wraps
from typing import Callable

from flask import request

from src.utils.config import is_auth_disabled, is_valid_api_key

logger = logging.getLogger(__name__)


def require_api_key(f: Callable) -> Callable:
    """Decorator to require x-api-key header for API endpoints.
    
    If DISABLE_API_AUTH environment variable is set, authentication is skipped.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Skip authentication if disabled via environment variable
        if is_auth_disabled():
            logger.debug(f"API authentication disabled, skipping auth check for {request.path}")
            return f(*args, **kwargs)
        
        api_key = request.headers.get("x-api-key")
        
        if not api_key:
            logger.warning(f"Missing x-api-key header for {request.path}")
            return {"error": "Missing x-api-key header"}, 401
        
        if not is_valid_api_key(api_key):
            logger.warning(f"Invalid API key provided for {request.path}")
            return {"error": "Invalid API key"}, 401
        
        return f(*args, **kwargs)
    
    return decorated_function
