"""Mock GitHub module interface for testing."""

import os
import sys
import logging
from typing import Optional, Dict, Any

# Import the fake PyGithub
from pyspr.tests.e2e.fake_pygithub import FakeGithub, FakeRepository, FakePullRequest

logger = logging.getLogger(__name__)

# Create fake exceptions
class FakeGithubException(Exception):
    """Base exception class for fake GitHub."""
    pass

class FakeBadCredentialsException(FakeGithubException):
    """Fake bad credentials exception."""
    pass

class FakeUnknownObjectException(FakeGithubException):
    """Fake unknown object exception."""
    pass

# Create fake module
class MockGitHubModule:
    """Mock github module containing all necessary classes."""
    Github = FakeGithub
    Repository = FakeRepository
    PullRequest = FakePullRequest
    
    # Exceptions
    GithubException = FakeGithubException
    BadCredentialsException = FakeBadCredentialsException
    UnknownObjectException = FakeUnknownObjectException


def install_mock_github():
    """Install the mock GitHub module by replacing PyGithub in sys.modules."""
    if "github" not in sys.modules:
        # Only replace if it hasn't been imported yet
        sys.modules["github"] = MockGitHubModule
        logger.info("Installed mock GitHub module")
        return True
    return False


def create_fake_github(token: Optional[str] = None) -> FakeGithub:
    """Create a fake GitHub instance for direct injection.
    
    This approach is preferred over module replacement when possible.
    """
    return FakeGithub(token=token)