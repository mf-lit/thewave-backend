import os
import logging
from pathlib import Path
from typing import List
import yaml

logger = logging.getLogger(__name__)

# Global variable to store valid API keys
_valid_api_keys: List[str] = []

# Global flag to track if authentication is disabled
_auth_disabled: bool = False


def load_api_keys() -> List[str]:
    """
    Load API keys from config.yaml file or environment variable.
    
    Priority:
    1. DISABLE_API_AUTH environment variable (if set to "true", disables auth)
    2. API_KEYS environment variable (comma-separated list)
    3. config.yaml file
    
    Raises:
        ValueError: If no API keys are configured and DISABLE_API_AUTH is not set
    
    Returns:
        List[str]: List of valid API keys
    """
    global _valid_api_keys, _auth_disabled
    
    # Check if authentication should be disabled
    disable_auth = os.getenv("DISABLE_API_AUTH", "").lower() in ("true", "1", "yes")
    if disable_auth:
        _auth_disabled = True
        _valid_api_keys = []
        logger.warning("API authentication is DISABLED (DISABLE_API_AUTH=true)")
        return _valid_api_keys
    
    _auth_disabled = False
    
    # Check environment variable first
    env_keys = os.getenv("API_KEYS", "").strip()
    if env_keys:
        keys = [key.strip() for key in env_keys.split(",") if key.strip()]
        if keys:
            _valid_api_keys = keys
            logger.info(f"Loaded {len(keys)} API key(s) from API_KEYS environment variable")
            return _valid_api_keys
    
    # Try to load from config.yaml file
    script_dir = Path(__file__).parent
    config_file = script_dir / "config.yaml"
    
    if not config_file.exists():
        raise ValueError(
            f"API authentication is required but no API keys found. "
            f"Either:\n"
            f"  1. Set DISABLE_API_AUTH=true to disable authentication, or\n"
            f"  2. Create config.yaml with API keys, or\n"
            f"  3. Set API_KEYS environment variable"
        )
    
    try:
        with open(config_file, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        
        if config is None:
            raise ValueError("Config file is empty or contains no data")
        
        if not isinstance(config, dict):
            raise ValueError(f"Config file must contain a YAML object (dictionary), but got {type(config).__name__}")
        
        api_keys = config.get("api_keys", [])
        if not isinstance(api_keys, list):
            raise ValueError("api_keys must be an array in config.yaml")
        
        # Filter out empty strings and normalize
        _valid_api_keys = [str(key).strip() for key in api_keys if key and str(key).strip()]
        
        if not _valid_api_keys:
            raise ValueError(
                "API authentication is required but no valid API keys found in config.yaml. "
                "Either set DISABLE_API_AUTH=true to disable authentication, or add valid API keys."
            )
        
        logger.info(f"Loaded {len(_valid_api_keys)} API key(s) from config.yaml")
        return _valid_api_keys
    except yaml.YAMLError as e:
        raise ValueError(f"Failed to parse config.yaml: {str(e)}")
    except ValueError:
        # Re-raise ValueError as-is
        raise
    except Exception as e:
        raise ValueError(f"Failed to load API keys from config.yaml: {str(e)}")


def is_valid_api_key(key: str) -> bool:
    """
    Check if the provided API key is valid.
    
    Args:
        key: API key to validate
    
    Returns:
        bool: True if key is valid, False otherwise
    """
    # If authentication is disabled, always return True
    if _auth_disabled:
        return True
    
    if not _valid_api_keys:
        return False
    
    if not key:
        return False
    
    # Case-sensitive comparison
    return key.strip() in _valid_api_keys


def require_api_key():
    """
    Flask before_request handler to check x-api-key header.
    This should be registered with @app.before_request.
    
    Returns:
        None if authentication passes, or a Flask response if it fails
    """
    from flask import request, jsonify
    
    # Skip authentication for OPTIONS requests (CORS preflight)
    if request.method == "OPTIONS":
        return None
    
    # If authentication is disabled, allow all requests
    if _auth_disabled:
        return None
    
    # Get API key from header
    api_key = request.headers.get("x-api-key") or request.headers.get("X-API-Key")
    
    if not api_key:
        logger.warning(f"Authentication failed: Missing x-api-key header from {request.remote_addr}")
        return jsonify({"error": "Missing x-api-key header"}), 403
    
    if not is_valid_api_key(api_key):
        logger.warning(f"Authentication failed: Invalid API key from {request.remote_addr}")
        return jsonify({"error": "Invalid API key"}), 403
    
    # Authentication passed
    return None
