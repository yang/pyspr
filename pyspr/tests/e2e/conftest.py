"""Configuration for pytest."""

import pytest
import os
import logging

# Import fixtures to make them available to all tests
from pyspr.tests.e2e.fixtures import (
    github_environment,
    test_repo_ctx,
    test_mq_repo_ctx,
    create_test_repo
)

# Configure logging
logger = logging.getLogger(__name__)

def pytest_configure(config):
    """Configure pytest."""
    # Set SPR_MOCK_GITHUB=true by default
    if "SPR_USE_REAL_GITHUB" not in os.environ:
        os.environ["SPR_USE_REAL_GITHUB"] = "false"
        logger.info("Set SPR_USE_REAL_GITHUB=false (default)")
    
    # Log if we're using real GitHub
    if os.environ.get("SPR_USE_REAL_GITHUB", "").lower() == "true":
        logger.warning("Using REAL GitHub API - tests may be slow or fail with API rate limits")