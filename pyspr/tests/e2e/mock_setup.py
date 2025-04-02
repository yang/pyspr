"""Helper functions to set up the mock environment for the main application."""

import os
import logging
from typing import Optional

from pyspr.config.models import PysprConfig
from pyspr.github import GitHubClient, PyGithubProtocol
from typing import Any
from pyspr.typing import StackedPRContextProtocol
from pyspr.tests.e2e.fake_pygithub import create_fake_github

logger = logging.getLogger(__name__)

class FakeGithubAdapter(PyGithubProtocol):
    """Adapter that wraps FakeGithub and implements PyGithubProtocol."""
    
    def __init__(self, fake_github: Any):
        """Initialize with a FakeGithub instance."""
        self.fake_github = fake_github
    
    def get_repo(self, full_name_or_id: str) -> Any:
        """Get a repository by full name or ID."""
        return self.fake_github.get_repo(full_name_or_id)
    
    def get_user(self, login: Any = None, **kwargs: Any) -> Any:
        """Get a user by login or the authenticated user if login is None."""
        # Forward only the parameters that FakeGithub.get_user accepts
        create = kwargs.get('create', False)
        return self.fake_github.get_user(login, create=create)
    
    # Forward any other attribute access to the wrapped FakeGithub instance
    def __getattr__(self, name: str) -> Any:
        return getattr(self.fake_github, name)


def should_use_mock_github() -> bool:
    """Check if we should use mock GitHub.
    
    This is determined by the SPR_USING_MOCK_GITHUB environment variable.
    For the main application, defaults to false unless explicitly set to true.
    For tests, the default is controlled by the test fixtures.
    
    Note: This function is used by both the test code and the main application.
    """
    env_value = os.environ.get("SPR_USING_MOCK_GITHUB")
    if env_value is None:
        # Default to false for main application
        return False
    return env_value.lower() == "true"

def create_github_client(ctx: Optional[StackedPRContextProtocol], config: PysprConfig, force_mock: bool = False) -> GitHubClient:
    """Create a GitHub client based on environment.
    
    When mocking, we directly inject our fake GitHub instance into the GitHub client.
    
    Args:
        ctx: The context object to pass to the GitHub client
        config: The configuration to use
        force_mock: If True, always use mock GitHub regardless of environment variables
    """
    # Determine whether to use mock GitHub
    # Priority: 1. force_mock parameter, 2. environment variable, 3. default behavior
    if force_mock:
        use_mock = True
    else:
        use_mock = should_use_mock_github()
        
    if use_mock:
        logger.info("Using MOCK GitHub client")
        # Set environment variable to indicate we're using mock GitHub
        os.environ["SPR_USING_MOCK_GITHUB"] = "true"
        
        # Debug current directory before creating fake client
        logger.info(f"Current directory before creating fake GitHub: {os.getcwd()}")
        
        # Create a fake GitHub instance
        fake_github = create_fake_github()
        
        # Log fake GitHub's data directory
        logger.info(f"Fake GitHub data directory: {fake_github.data_dir}")
        
        # Create an adapter that wraps the fake GitHub instance
        github_adapter = FakeGithubAdapter(fake_github)
        
        # Create the GitHub client with the adapter
        client = GitHubClient(ctx, config, github_client=github_adapter)
        logger.info("Created GitHubClient with fake PyGithub implementation")
        
        # Make sure repo is initialized
        # Use github_repo_owner and github_repo_name if available
        owner = config.repo.github_repo_owner
        name = config.repo.github_repo_name
        if owner and name:
            # Set repository using the client's repo property
            client.repo = client.client.get_repo(f"{owner}/{name}")
            logger.info(f"Initialized repository: {owner}/{name}")
            
            # Force saving state after initialization using public method
            fake_github.save_state()
            logger.info("Forced saving initial state")
            
            # Create a test PR for debugging
            if True:  # We'll always do this for debugging
                logger.info("Creating test PR in mock GitHub for debugging")
                repo = client.repo
                if repo:
                    try:
                        test_pr = repo.create_pull(
                            title="Test PR from mock setup",
                            body="This is a test PR",
                            base="main",
                            head="test-branch"
                        )
                        logger.info(f"Created test PR #{test_pr.number}")
                        fake_github.save_state()
                        logger.info(f"Saved state after creating test PR, dictionary now has {len(fake_github.pull_requests)} entries")
                    except Exception as e:
                        logger.error(f"Failed to create test PR: {e}")
        
        return client
    else:
        logger.info("Using REAL GitHub client")
        # Set environment variable to indicate we're using real GitHub
        os.environ["SPR_USING_MOCK_GITHUB"] = "false"
        
        # Import here to avoid circular imports
        from pyspr.github import find_github_token, RealPyGithubAdapter
        from github import Github
        
        # Get GitHub token
        token = find_github_token()
        if not token:
            error_msg = "No GitHub token found. Try one of:\n1. Set GITHUB_TOKEN env var\n2. Log in with 'gh auth login'\n3. Put token in /home/ubuntu/code/pyspr/token file"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        # Create a real PyGithub client and wrap it in an adapter
        real_client = Github(token)
        github_client = RealPyGithubAdapter(real_client)
            
        # Create and return the GitHub client
        return GitHubClient(ctx, config, github_client=github_client)