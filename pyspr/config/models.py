"""Pydantic models for config types."""

from typing import Optional, Dict, List
from pydantic import BaseModel, Field

# No dictionary-like access mixins - using pure Pydantic models with attribute access

class RepoConfig(BaseModel):
    """Repository configuration."""
    # GitHub connection settings
    github_remote: str = "origin"
    github_branch: str = "main"
    github_branch_target: str = "main"  # Target branch for PRs, defaults to main
    github_repo_owner: Optional[str] = None
    github_repo_name: Optional[str] = None
    github_host: str = "github.com"  # GitHub host, defaults to github.com

    # Branch naming
    branch_prefix: str = "pyspr/"  # Prefix for PR branch names (e.g., "pyspr/" -> "pyspr/{commit_id}")

    # PR and merge settings
    merge_queue: bool = False
    merge_method: str = "squash"  # Merge method, one of: merge, squash, rebase
    merge_check: bool = False  # Whether to run merge checks
    show_pr_titles_in_stack: bool = False
    branch_push_individually: bool = False  # Whether to push branches individually
    auto_close_prs: bool = False  # Whether to automatically close PRs

    # Labels for PRs
    labels: List[str] = Field(default_factory=list)  # Labels to apply to PRs

    class Config:
        """Pydantic config."""
        extra = "allow"  # Allow extra fields for backward compatibility

class UserConfig(BaseModel):
    """User configuration."""
    no_rebase: bool = False
    log_git_commands: bool = False
    best_effort: bool = False  # Skip pushes that fail due to merge queue

    class Config:
        """Pydantic config."""
        extra = "allow"  # Allow extra fields

class ToolConfig(BaseModel):
    """Tool configuration."""
    concurrency: int = 0
    pretend: bool = False

    no_verify: bool = False  # Whether to skip pre-push hooks

    # Git index.lock handling (for NFS lag issues)
    index_lock_wait_enabled: bool = True  # Whether to wait for index.lock

    class Config:
        """Pydantic config."""
        extra = "allow"  # Allow extra fields

class StateConfig(BaseModel):
    """State configuration for merge checks."""
    merge_check_commit: Dict[str, str] = Field(default_factory=dict)
    
    class Config:
        """Pydantic config."""
        extra = "allow"  # Allow extra fields

class PysprConfig(BaseModel):
    """Full pyspr configuration."""
    repo: RepoConfig = Field(default_factory=RepoConfig)
    user: UserConfig = Field(default_factory=UserConfig) 
    tool: ToolConfig = Field(default_factory=ToolConfig)
    state: Optional[StateConfig] = None
    
    # No get method - use direct attribute access instead

    class Config:
        """Pydantic config."""
        extra = "allow"  # Allow extra fields