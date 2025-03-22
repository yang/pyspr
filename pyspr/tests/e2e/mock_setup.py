"""Helper functions to set up the mock environment for the main application."""

import os
import logging
from typing import Optional, Union, Type

from pyspr.config import Config
from pyspr.github import GitHubClient
from pyspr.tests.e2e.mock_repo import MockGitHubClient

logger = logging.getLogger(__name__)

def should_use_mock_github() -> bool:
    """Check if we should use mock GitHub.
    
    This is determined by the SPR_USE_REAL_GITHUB environment variable.
    If it's not set or set to "false", we use mock GitHub.
    """
    return os.environ.get("SPR_USE_REAL_GITHUB", "false").lower() != "true"

def get_github_client_class() -> Type[GitHubClient]:
    """Get the GitHub client class to use based on environment."""
    if should_use_mock_github():
        logger.info("Using MOCK GitHub client")
        # Set environment variable to indicate we're using mock GitHub
        os.environ["SPR_USING_MOCK_GITHUB"] = "true"
        return MockGitHubClient
    else:
        logger.info("Using REAL GitHub client")
        # Clear environment variable if it was set
        if "SPR_USING_MOCK_GITHUB" in os.environ:
            del os.environ["SPR_USING_MOCK_GITHUB"]
        return GitHubClient

def create_github_client(ctx: Optional[object], config: Config) -> GitHubClient:
    """Create a GitHub client based on environment."""
    client_class = get_github_client_class()
    return client_class(ctx, config)