"""
Configuration module for DUCK-E
Automatically generates OAI_CONFIG_LIST from environment variables
"""
import json
import os
from typing import List, Dict, Any


def generate_oai_config_list() -> List[Dict[str, Any]]:
    """
    Automatically generate OAI_CONFIG_LIST from OPENAI_API_KEY environment variable.

    Creates configurations for:
    - gpt-5-mini: Fast, efficient model for general queries
    - gpt-5: Advanced model for complex reasoning
    - gpt-realtime: Specialized for voice interaction

    Returns:
        List of OpenAI configuration dictionaries
    """
    api_key = os.getenv('OPENAI_API_KEY')

    if not api_key:
        raise ValueError(
            "OPENAI_API_KEY environment variable is required. "
            "Please set it in your .env file or environment."
        )

    # Check if OAI_CONFIG_LIST already exists in environment
    existing_config = os.getenv('OAI_CONFIG_LIST')
    if existing_config:
        try:
            # Use existing configuration if provided
            return json.loads(existing_config)
        except json.JSONDecodeError:
            # Fall back to auto-generation if JSON is invalid
            pass

    # Auto-generate configuration from OPENAI_API_KEY
    config_list = [
        {
            "model": "gpt-5-mini",
            "api_key": api_key,
            "tags": ["gpt-5-mini", "fast"]
        },
        {
            "model": "gpt-5",
            "api_key": api_key,
            "tags": ["gpt-5-full", "advanced"]
        },
        {
            "model": "gpt-realtime",
            "api_key": api_key,
            "tags": ["gpt-realtime", "voice"]
        }
    ]

    return config_list


def get_realtime_config() -> List[Dict[str, Any]]:
    """
    Get configuration specifically for realtime models.
    Filters by 'gpt-realtime' tag.

    Returns:
        List of realtime model configurations
    """
    config_list = generate_oai_config_list()
    return [
        config for config in config_list
        if "gpt-realtime" in config.get("tags", [])
    ]


def get_swarm_config() -> List[Dict[str, Any]]:
    """
    Get configuration for swarm models (gpt-5 and gpt-5-mini).

    Returns:
        List of swarm model configurations
    """
    config_list = generate_oai_config_list()
    return [
        config for config in config_list
        if config.get("model") in ["gpt-5", "gpt-5-mini"]
    ]


def validate_config() -> bool:
    """
    Validate that the configuration is properly set up.

    Returns:
        True if configuration is valid, False otherwise
    """
    try:
        config_list = generate_oai_config_list()

        # Check that we have at least one configuration
        if not config_list:
            return False

        # Check that each config has required fields
        for config in config_list:
            if not config.get("model"):
                return False
            if not config.get("api_key"):
                return False

        # Check that we have a realtime model
        realtime_configs = get_realtime_config()
        if not realtime_configs:
            return False

        return True
    except Exception:
        return False


# Export configuration on module import for convenience
try:
    OAI_CONFIG_LIST = generate_oai_config_list()
    REALTIME_CONFIG_LIST = get_realtime_config()
    SWARM_CONFIG_LIST = get_swarm_config()
except Exception as e:
    # If configuration fails, set to empty lists
    # This allows the application to start and provide helpful error messages
    OAI_CONFIG_LIST = []
    REALTIME_CONFIG_LIST = []
    SWARM_CONFIG_LIST = []
