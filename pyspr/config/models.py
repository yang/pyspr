"""Pydantic models for config types."""

from typing import Optional, Dict, Any
from pydantic import BaseModel, Field

class RepoConfig(BaseModel):
    """Repository configuration."""
    github_remote: str = "origin"
    github_branch: str = "main" 
    github_repo_owner: Optional[str] = None
    github_repo_name: Optional[str] = None
    merge_queue: bool = False
    show_pr_titles_in_stack: bool = False

    class Config:
        """Pydantic config."""
        extra = "allow"  # Allow extra fields for backward compatibility

class UserConfig(BaseModel):
    """User configuration."""
    no_rebase: bool = False  # Renamed from noRebase for consistency
    log_git_commands: bool = False

    class Config:
        """Pydantic config."""
        extra = "allow"  # Allow extra fields

class ToolConfig(BaseModel):
    """Tool configuration."""
    concurrency: int = 0
    pretend: bool = False

    class Config:
        """Pydantic config."""
        extra = "allow"  # Allow extra fields

class PysprConfig(BaseModel):
    """Full pyspr configuration."""
    repo: RepoConfig = Field(default_factory=RepoConfig)
    user: UserConfig = Field(default_factory=UserConfig) 
    tool: Dict[str, Any] = Field(default_factory=dict)
    state: Optional[Dict[str, Any]] = None

    class Config:
        """Pydantic config."""
        extra = "allow"  # Allow extra fields