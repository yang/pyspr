"""Test fixtures for end-to-end tests with mock or real GitHub."""

import os
import logging
from typing import Generator, Tuple, Any, Optional
import pytest
from _pytest.fixtures import FixtureRequest

from pyspr.tests.e2e.test_helpers import RepoContext, create_repo_context, run_cmd
from pyspr.tests.e2e.mock_repo import create_mock_repo_context

logger = logging.getLogger(__name__)

def should_use_real_github() -> bool:
    """Check if tests should use real GitHub API.
    
    Default to using mock GitHub (SPR_USING_MOCK_GITHUB=true) unless explicitly set to false.
    This is the opposite of should_use_mock_github() in mock_setup.py.
    """
    env_value = os.environ.get("SPR_USING_MOCK_GITHUB")
    if env_value is None:
        # Default to using mock GitHub for tests
        return False
    return env_value.lower() != "true"

@pytest.fixture
def github_environment():
    """Set up GitHub environment for tests.
    
    This is automatically used by all tests to ensure mock environment
    is properly set up before any tests run.
    
    For tests, we explicitly set SPR_USING_MOCK_GITHUB=true by default,
    which overrides the default in mock_setup.py (which is false for the main application).
    """
    if not should_use_real_github():
        logger.info("Setting up MOCK GitHub environment")
        # Set environment variable to indicate we're using mock GitHub
        os.environ["SPR_USING_MOCK_GITHUB"] = "true"
    else:
        logger.info("Using REAL GitHub API")
        # Set environment variable to indicate we're using real GitHub
        os.environ["SPR_USING_MOCK_GITHUB"] = "false"
    
    yield

@pytest.fixture
def test_repo_ctx(request: FixtureRequest) -> Generator[RepoContext, None, None]:
    """Test repo fixture using real or mock GitHub based on environment variable."""
    assert request.node is not None, "pytest request.node should not be None"
    node: Any = request.node
    
    if should_use_real_github():
        # Use real GitHub
        logger.info("Using REAL GitHub for tests")
        yield from create_repo_context("yang", "teststack", node.name)
    else:
        # Use mock GitHub
        logger.info("Using MOCK GitHub for tests")
        yield from create_mock_repo_context("yang", "teststack", node.name)

@pytest.fixture
def test_mq_repo_ctx(request: FixtureRequest) -> Generator[RepoContext, None, None]:
    """Merge queue test repo fixture using real or mock GitHub."""
    assert request.node is not None, "pytest request.node should not be None"
    node: Any = request.node
    
    if should_use_real_github():
        # Use real GitHub
        logger.info("Using REAL GitHub for merge queue tests")
        yield from create_repo_context("yangenttest1", "teststack", node.name)
    else:
        # Use mock GitHub
        logger.info("Using MOCK GitHub for merge queue tests")
        yield from create_mock_repo_context("yangenttest1", "teststack", node.name)

def create_test_repo(owner: str, name: str) -> Generator[Tuple[str, str, str, str], None, None]:
    """Legacy test repo fixture factory with support for both real and mock GitHub."""
    if should_use_real_github():
        # Use real GitHub
        logger.info(f"Using REAL GitHub for legacy fixture {owner}/{name}")
        yield from create_real_test_repo(owner, name)
    else:
        # Use mock GitHub with same return format
        logger.info(f"Using MOCK GitHub for legacy fixture {owner}/{name}")
        ctx = create_mock_repo_context(owner, name, "legacy_fixture")
        try:
            repo_ctx = next(ctx)
            yield owner, name, repo_ctx.branch, repo_ctx.repo_dir
        finally:
            try:
                next(ctx)  # Exhaust generator to ensure cleanup
            except StopIteration:
                pass

def create_real_test_repo(owner: str, name: str) -> Generator[Tuple[str, str, str, str], None, None]:
    """Original test repo fixture factory using real GitHub."""
    # Import here to avoid circular imports
    from pyspr.tests.e2e.test_helpers import create_test_repo as original_create_test_repo
    yield from original_create_test_repo(owner, name)