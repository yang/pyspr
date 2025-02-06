"""Config module."""

from typing import Dict, Optional

from .config_parser import parse_config

class Config:
    """Config object holding repository and user config."""
    def __init__(self, config: Dict):
        """Initialize with parsed config dict."""
        self.repo = {k.lower(): v for k, v in config.get('repo', {}).items()} 
        self.user = {k.lower(): v for k, v in config.get('user', {}).items()}

def default_config() -> Config:
    """Get default config without parsing git."""
    return Config({
        'repo': {
            'github_remote': 'origin',
            'github_branch': 'main',
        },
        'user': {}
    })