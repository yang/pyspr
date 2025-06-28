"""Test decorators for running tests with different configurations."""

import os
import functools
import logging
from typing import Callable, TypeVar, Any
import pytest

logger = logging.getLogger(__name__)

F = TypeVar('F', bound=Callable[..., Any])

def run_twice_in_mock_mode(func: F) -> F:
    """Decorator to run a test twice in mock mode, reusing fake GitHub state.
    
    This decorator:
    1. Only runs when in mock mode (SPR_USING_MOCK_GITHUB=true)
    2. Runs the test function twice with the same fake GitHub state
    3. Helps ensure tests are idempotent and work with existing state
    
    Usage:
        @run_twice_in_mock_mode
        def test_something(test_repo_ctx):
            # test code here
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        # Check if we're in mock mode
        is_mock = os.environ.get("SPR_USING_MOCK_GITHUB", "").lower() == "true"
        
        if not is_mock:
            # In real GitHub mode, just run once
            logger.info(f"Running {func.__name__} once (real GitHub mode)")
            return func(*args, **kwargs)
        
        # In mock mode, run twice
        logger.info(f"=== Running {func.__name__} FIRST time (mock mode) ===")
        
        # First run - normal
        result = func(*args, **kwargs)
        
        logger.info(f"=== Running {func.__name__} SECOND time (mock mode with existing state) ===")
        
        # Set environment variable to preserve state for second run
        os.environ["SPR_PRESERVE_FAKE_GITHUB_STATE"] = "true"
        try:
            # Second run - with preserved state
            result = func(*args, **kwargs)
        finally:
            # Clean up environment variable
            os.environ.pop("SPR_PRESERVE_FAKE_GITHUB_STATE", None)
        
        return result
    
    return wrapper


def preserve_fake_github_state(test_func: F) -> F:
    """Decorator to preserve fake GitHub state across test runs.
    
    This decorator modifies the test to use a persistent directory for fake GitHub
    state instead of creating a new temporary directory each time.
    """
    @functools.wraps(test_func)
    def wrapper(*args, **kwargs):
        # Set environment variable to indicate we want to preserve state
        original_preserve = os.environ.get("SPR_PRESERVE_FAKE_GITHUB_STATE")
        os.environ["SPR_PRESERVE_FAKE_GITHUB_STATE"] = "true"
        
        try:
            return test_func(*args, **kwargs)
        finally:
            # Restore original value
            if original_preserve is None:
                os.environ.pop("SPR_PRESERVE_FAKE_GITHUB_STATE", None)
            else:
                os.environ["SPR_PRESERVE_FAKE_GITHUB_STATE"] = original_preserve
    
    return wrapper