"""Config module."""

from typing import Dict, Any, Protocol, TypeVar, overload, Union, Optional
from .models import RepoConfig, UserConfig, PysprConfig

T = TypeVar('T')

class ConfigProtocol(Protocol):
    """Protocol for config objects."""
    repo: Dict[str, Any]
    user: Dict[str, Any]
    tool: Dict[str, Any]
    state: Optional[Dict[str, Any]]
    @overload
    def get(self, key: str) -> Any: ...
    @overload
    def get(self, key: str, default: T) -> Union[Any, T]: ...
    def get(self, key: str, default: Any = None) -> Any: ...

class Config:
    """Config object holding repository and user config."""
    def __init__(self, config: Dict[str, Dict[str, Any]]):
        """Initialize with parsed config dict."""
        pyspr_config = PysprConfig(
            repo=RepoConfig(**(config.get('repo', {}))),
            user=UserConfig(**(config.get('user', {}))),
            tool=config.get('tool', {}).get('pyspr', {}),
        )
        # Convert to lower case dicts for backward compatibility
        self.repo: Dict[str, Any] = {k.lower(): v for k, v in pyspr_config.repo.model_dump().items()}
        self.user: Dict[str, Any] = {k.lower(): v for k, v in pyspr_config.user.model_dump().items()}
        # Convert noRebase to no_rebase for consistency
        if 'norebase' in self.user:
            self.user['no_rebase'] = self.user.pop('norebase')
        self.tool: Dict[str, Any] = {k.lower(): v for k, v in pyspr_config.tool.items()}
        self.state: Optional[Dict[str, Any]] = None

        # Convert concurrency to int if present
        if 'concurrency' in self.tool:
            try:
                self.tool['concurrency'] = int(self.tool['concurrency'])
            except (TypeError, ValueError):
                self.tool['concurrency'] = 0  # Invalid values become 0
        
    def get(self, key: str, default: Any = None) -> Any:
        """Get a config value from any section."""
        key_lower = key.lower()
        # Check tool section first
        if key_lower in self.tool:
            return self.tool[key_lower]
        # Then repo section
        if key_lower in self.repo:
            return self.repo[key_lower]
        # Then user section 
        if key_lower in self.user:
            return self.user[key_lower]
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