"""Config module."""

from typing import Dict, Any
from .models import RepoConfig, UserConfig, PysprConfig, ToolConfig

class Config(PysprConfig):
    """Config object holding repository and user config.
    
    This is a subclass of PysprConfig for backward compatibility.
    It directly exposes the Pydantic models with dictionary-like access.
    """
    def __init__(self, config: Dict[str, Dict[str, Any]]):
        """Initialize with parsed config dict."""
        # Extract configuration sections with proper defaults
        repo_config = config.get('repo', {})
        user_config = config.get('user', {})
        tool_section = config.get('tool', {})
        tool_config = tool_section.get('pyspr', {})
        
        # Initialize the PysprConfig parent class
        super().__init__(
            repo=RepoConfig.model_validate(repo_config),
            user=UserConfig.model_validate(user_config),
            tool=ToolConfig.model_validate(tool_config),
            state=None
        )

def default_config() -> Config:
    """Get default config without parsing git."""
    return Config({
        'repo': {
            'github_remote': 'origin',
            'github_branch': 'main',
        },
        'user': {},
        'tool': {
            'pyspr': {
                'concurrency': 0
            }
        }
    })