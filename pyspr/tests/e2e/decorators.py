"""Test decorators for running tests with different configurations."""

import os
import functools
import logging
from typing import Callable, TypeVar, Any, cast
# pytest import removed as unused
from pyspr.tests.e2e.test_helpers import RepoContext, run_cmd

logger = logging.getLogger(__name__)

F = TypeVar('F', bound=Callable[..., Any])

def run_twice_in_mock_mode(func: F) -> F:
    """Decorator to run a test twice in mock mode, reusing fake GitHub state and git repo.
    
    This decorator:
    1. Only runs when in mock mode (SPR_USING_MOCK_GITHUB=true)
    2. Runs the test function twice with the same git repo and fake GitHub state
    3. Resets git working directory between runs but preserves GitHub state
    4. Helps ensure tests work correctly with existing PRs and state
    
    Usage:
        @run_twice_in_mock_mode
        def test_something(test_repo_ctx):
            # test code here
    """
    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        # Check if we're in mock mode
        is_mock = os.environ.get("SPR_USING_MOCK_GITHUB", "").lower() == "true"
        
        if not is_mock:
            # In real GitHub mode, just run once
            logger.info(f"Running {func.__name__} once (real GitHub mode)")
            return func(*args, **kwargs)
        
        # Find the test_repo_ctx in args/kwargs
        test_repo_ctx = None
        if args and isinstance(args[0], RepoContext):
            test_repo_ctx = args[0]
        elif 'test_repo_ctx' in kwargs:
            test_repo_ctx = kwargs['test_repo_ctx']
        
        if not test_repo_ctx:
            # Can't find repo context, just run normally
            logger.warning("Could not find test_repo_ctx, running test normally")
            return func(*args, **kwargs)
        
        # Save current directory
        orig_dir = os.getcwd()
        
        try:
            logger.info(f"=== Running {func.__name__} FIRST time (mock mode) ===")
            
            # First run - normal
            result = func(*args, **kwargs)
            
            logger.info("=== Resetting git state for second run ===")
            
            # Reset git state but preserve fake GitHub state
            os.chdir(test_repo_ctx.repo_dir)
            
            # Save current branch name (usually test_local)
            current_branch = run_cmd("git rev-parse --abbrev-ref HEAD").strip()
            
            # Clean up any uncommitted changes
            run_cmd("git reset --hard HEAD")
            run_cmd("git clean -fd")
            
            # Go back to main branch
            run_cmd("git checkout main")
            
            # Update main to match origin/main (including merged PRs from first run)
            run_cmd("git reset --hard origin/main")
            
            # Delete the test branch
            run_cmd(f"git branch -D {current_branch} || true")
            
            # Create a fresh test branch
            run_cmd(f"git checkout -b {current_branch}")
            
            logger.info(f"=== Running {func.__name__} SECOND time (mock mode with existing GitHub state) ===")
            
            # Second run - with same repo and preserved GitHub state
            result = func(*args, **kwargs)
            
            return result
            
        finally:
            # Restore original directory
            os.chdir(orig_dir)
    
    return cast(F, wrapper)


def preserve_fake_github_state(test_func: F) -> F:
    """Decorator to preserve fake GitHub state across test runs.
    
    This decorator modifies the test to use a persistent directory for fake GitHub
    state instead of creating a new temporary directory each time.
    """
    @functools.wraps(test_func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
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
    
    return cast(F, wrapper)