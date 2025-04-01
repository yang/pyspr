"""Configuration for pytest."""

# These imports are needed for pytest to work properly
import os
import logging
# Import pytest for its hooks and Config type
import pytest
from pytest import Config

# Import fixtures to make them available to all tests
# These imports are necessary for pytest to discover and register the fixtures
from pyspr.tests.e2e.fixtures import (
    github_environment,
    test_repo_ctx,
    test_mq_repo_ctx,
    create_test_repo
)

# This function is never called but tells the type checker that these imports are used
# pyright: reportUnusedFunction=false
def _ensure_fixtures_are_used() -> None:
    """This function is never called but ensures type checkers know the fixtures are used."""
    _ = pytest.fixture
    _ = github_environment
    _ = test_repo_ctx
    _ = test_mq_repo_ctx
    _ = create_test_repo

# Configure logging
logger = logging.getLogger(__name__)

def pytest_configure(config: Config):
    """Configure pytest."""
    # Log whether we're using mock or real GitHub based on the actual controlling variable
    if os.environ.get("SPR_USING_MOCK_GITHUB", "").lower() == "false":
        logger.warning("Using REAL GitHub API - tests may be slow or fail with API rate limits")
    else:
        logger.info("Using MOCK GitHub (default)")