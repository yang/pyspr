"""Mock GitHub module interface for testing."""

import os
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

# Create mock classes dictionary for easier access
mock_classes = {
    "Github": FakeGithub,
    "Repository": FakeRepository,
    "PullRequest": FakePullRequest,
    "GithubException": FakeGithubException,
    "BadCredentialsException": FakeBadCredentialsException,
    "UnknownObjectException": FakeUnknownObjectException
}

def create_fake_github(token: Optional[str] = None) -> FakeGithub:
    """Create a fake GitHub instance for direct injection.
    
    This is the recommended way to create a fake GitHub client.
    """
    return FakeGithub(token=token)