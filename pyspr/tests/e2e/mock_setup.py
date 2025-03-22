"""Helper functions to set up the mock environment for the main application."""

import os
import logging
import sys
from typing import Optional, Any

from pyspr.config import Config
from pyspr.github import GitHubClient
from pyspr.tests.e2e.mock_github_module import install_mock_github, create_fake_github
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
    
    When mocking, we replace the underlying PyGithub instance with our fake version.
    """
    if should_use_mock_github():
        logger.info("Using MOCK GitHub client")
        # Set environment variable to indicate we're using mock GitHub
        os.environ["SPR_USING_MOCK_GITHUB"] = "true"
        
        # Install the mock GitHub module if PyGithub hasn't been imported yet
        mock_installed = install_mock_github()
        logger.info(f"Mock module installed: {mock_installed}")
        
        # Create the GitHub client (will use mocked PyGithub if module was replaced)
        client = GitHubClient(ctx, config)
        
        # If mock module installation failed (because PyGithub was already imported),
        # directly replace the client attribute with our FakeGithub
        if not mock_installed and hasattr(client, 'client'):
            # Replace the PyGithub instance with our fake version
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