"""Configuration management utilities."""
import logging
import os
from pathlib import Path
from typing import List, Optional

import yaml

logger = logging.getLogger(__name__)


def is_auth_disabled() -> bool:
    """Check if API authentication is disabled via environment variable."""
    return os.getenv("DISABLE_API_AUTH", "").lower() in ("1", "true", "yes")


def get_config_path() -> Path:
    """Get the path to the config.yaml file."""
    # Get the project root (parent of src directory)
    project_root = Path(__file__).parent.parent.parent
    config_path = project_root / "config" / "config.yaml"
    return config_path


def validate_config_file() -> None:
    """Validate that config/config.yaml exists and is valid.
    
    Raises:
        FileNotFoundError: If config file is missing and DISABLE_API_AUTH is not set
        ValueError: If config file is invalid (empty or missing api_keys)
    """
    if is_auth_disabled():
        logger.info("API authentication is disabled via DISABLE_API_AUTH environment variable")
        return
    
    config_path = get_config_path()
    
    if not config_path.exists():
        raise FileNotFoundError(
            f"Config file not found at {config_path}. "
            "API authentication requires config/config.yaml. "
            "Set DISABLE_API_AUTH=1 to disable authentication."
        )
    
    try:
        with open(config_path, "r") as f:
            config = yaml.safe_load(f)
        
        if not config:
            raise ValueError(
                f"Config file at {config_path} is empty. "
                "Please add api_keys to the config file."
            )
        
        api_keys = config.get("api_keys", [])
        if not api_keys:
            raise ValueError(
                f"No api_keys found in config file at {config_path}. "
                "Please add at least one API key to the config file."
            )
        
        # Filter out None/empty values
        valid_keys = [key for key in api_keys if key]
        if not valid_keys:
            raise ValueError(
                f"All API keys in config file at {config_path} are empty. "
                "Please add at least one valid API key."
            )
        
        logger.info(f"Config file validated. Loaded {len(valid_keys)} API key(s)")
    except yaml.YAMLError as e:
        raise ValueError(f"Failed to parse YAML config file at {config_path}: {e}")
    except Exception as e:
        if isinstance(e, (FileNotFoundError, ValueError)):
            raise
        raise ValueError(f"Failed to load config file at {config_path}: {e}")


def load_api_keys() -> List[str]:
    """Load valid API keys from config/config.yaml."""
    if is_auth_disabled():
        logger.debug("API authentication disabled, returning empty key list")
        return []
    
    config_path = get_config_path()
    
    if not config_path.exists():
        logger.warning(f"Config file not found at {config_path}. API authentication will fail.")
        return []
    
    try:
        with open(config_path, "r") as f:
            config = yaml.safe_load(f)
        
        if not config:
            logger.warning("Config file is empty. API authentication will fail.")
            return []
        
        api_keys = config.get("api_keys", [])
        if not api_keys:
            logger.warning("No API keys found in config file. API authentication will fail.")
            return []
        
        # Filter out None/empty values and return as list
        valid_keys = [key for key in api_keys if key]
        logger.info(f"Loaded {len(valid_keys)} API key(s) from config")
        return valid_keys
    except yaml.YAMLError as e:
        logger.error(f"Failed to parse YAML config file: {e}")
        return []
    except Exception as e:
        logger.error(f"Failed to load config file: {e}")
        return []


def is_valid_api_key(api_key: Optional[str]) -> bool:
    """Check if the provided API key is valid."""
    if is_auth_disabled():
        return True  # Skip validation when auth is disabled
    
    if not api_key:
        return False
    
    valid_keys = load_api_keys()
    return api_key in valid_keys
