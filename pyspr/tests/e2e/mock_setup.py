"""Helper functions to set up the mock environment for the main application."""

import os
import logging
from typing import Optional, Any

from pyspr.config import Config
from pyspr.github import GitHubClient
from pyspr.tests.e2e.mock_github_module import create_fake_github
from pyspr.tests.e2e.fake_pygithub import FakeGithub

logger = logging.getLogger(__name__)

def should_use_mock_github() -> bool:
    """Check if we should use mock GitHub.
    
    This is determined by the SPR_USE_REAL_GITHUB environment variable.
    If it's not set or set to "false", we use mock GitHub.
    """
    return os.environ.get("SPR_USE_REAL_GITHUB", "false").lower() != "true"

def create_github_client(ctx: Optional[object], config: Config) -> GitHubClient:
    """Create a GitHub client based on environment, using real GitHubClient always.
    
    When mocking, we directly inject our fake GitHub instance into the GitHub client.
    """
    if should_use_mock_github():
        logger.info("Using MOCK GitHub client")
        # Set environment variable to indicate we're using mock GitHub
        os.environ["SPR_USING_MOCK_GITHUB"] = "true"
        
        # Create the GitHub client with default initialization
        client = GitHubClient(ctx, config)
        
        # Always directly replace the client attribute with our FakeGithub
        client.client = create_fake_github()
        logger.info("Injected fake GitHub instance directly")
        
        # Make sure repo is initialized
        if config.repo.get('github_repo_owner') and config.repo.get('github_repo_name'):
            owner = config.repo.get('github_repo_owner')
            name = config.repo.get('github_repo_name')
            client._repo = client.client.get_repo(f"{owner}/{name}")
            logger.info(f"Initialized repository: {owner}/{name}")
        
        return client
    else:
        logger.info("Using REAL GitHub client")
        # Clear environment variable if it was set
        if "SPR_USING_MOCK_GITHUB" in os.environ:
            del os.environ["SPR_USING_MOCK_GITHUB"]
        return GitHubClient(ctx, config)