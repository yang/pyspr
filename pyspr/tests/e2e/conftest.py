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
    # Set SPR_MOCK_GITHUB=true by default
    if "SPR_USE_REAL_GITHUB" not in os.environ:
        os.environ["SPR_USE_REAL_GITHUB"] = "false"
        logger.info("Set SPR_USE_REAL_GITHUB=false (default)")
    
    # Log if we're using real GitHub
    if os.environ.get("SPR_USE_REAL_GITHUB", "").lower() == "true":
        logger.warning("Using REAL GitHub API - tests may be slow or fail with API rate limits")