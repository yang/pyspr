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
    # Log whether we're using mock or real GitHub based on the actual controlling variable
    if os.environ.get("SPR_USING_MOCK_GITHUB", "").lower() == "false":
        logger.warning("Using REAL GitHub API - tests may be slow or fail with API rate limits")
    else:
        logger.info("Using MOCK GitHub (default)")