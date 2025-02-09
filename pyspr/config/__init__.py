"""Config module."""

from typing import Dict, Any

class Config:
    """Config object holding repository and user config."""
    def __init__(self, config: Dict[str, Dict[str, Any]]):
        """Initialize with parsed config dict."""
        repo_config: Dict[str, Any] = config.get('repo', {})
        user_config: Dict[str, Any] = config.get('user', {})
        tool_config: Dict[str, Any] = config.get('tool', {}).get('pyspr', {})
        self.repo: Dict[str, Any] = {k.lower(): v for k, v in repo_config.items()} 
        self.user: Dict[str, Any] = {k.lower(): v for k, v in user_config.items()}
        self.tool: Dict[str, Any] = {k.lower(): v for k, v in tool_config.items()}

        # Convert concurrency to int if present
        if 'concurrency' in self.tool:
            try:
                self.tool['concurrency'] = int(self.tool['concurrency'])
            except (TypeError, ValueError):
                self.tool['concurrency'] = 0  # Invalid values become 0
        
    def get(self, key: str, default: Any = None) -> Any:
        """Get a config value from any section."""
        # Check tool section first
        if key.lower() in self.tool:
            return self.tool[key.lower()]
        # Then repo section
        if key.lower() in self.repo:
            return self.repo[key.lower()]
        # Then user section
        if key.lower() in self.user:
            return self.user[key.lower()]
        return default

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