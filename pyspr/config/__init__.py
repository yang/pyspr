"""Config module."""

from typing import Dict, Any

class Config:
    """Config object holding repository and user config."""
    def __init__(self, config: Dict[str, Dict[str, Any]]):
        """Initialize with parsed config dict."""
        repo_config: Dict[str, Any] = config.get('repo', {})
        user_config: Dict[str, Any] = config.get('user', {})
        self.repo: Dict[str, Any] = {k.lower(): v for k, v in repo_config.items()} 
        self.user: Dict[str, Any] = {k.lower(): v for k, v in user_config.items()}

def default_config() -> Config:
    """Get default config without parsing git."""
    return Config({
        'repo': {
            'github_remote': 'origin',
            'github_branch': 'main',
        },
        'user': {}
    })