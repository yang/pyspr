"""Demo test to show the run_twice_in_mock_mode decorator in action."""

import logging
from pyspr.tests.e2e.decorators import run_twice_in_mock_mode
from pyspr.tests.e2e.test_helpers import RepoContext, run_cmd

logger = logging.getLogger(__name__)

@run_twice_in_mock_mode
def test_decorator_demo(test_repo_ctx: RepoContext) -> None:
    """Test that demonstrates running twice with preserved state."""
    ctx = test_repo_ctx
    
    # Get github info which includes PRs
    info = ctx.github.get_info(ctx, ctx.git_cmd)
    initial_pr_count = len(info.pull_requests) if info else 0
    logger.info(f"Found {initial_pr_count} existing PRs")
    
    # Create a test commit with a timestamp to make it unique
    import time
    timestamp = int(time.time() * 1000) % 10000
    ctx.make_commit("demo.txt", f"demo content {timestamp}", f"Demo commit for decorator test {timestamp}")
    
    # Run pyspr update
    run_cmd("pyspr update")
    
    # Check PRs after update
    info_after = ctx.github.get_info(ctx, ctx.git_cmd)
    final_pr_count = len(info_after.pull_requests) if info_after else 0
    logger.info(f"After update: {final_pr_count} PRs")
    
    # Verify we created at least one PR
    assert final_pr_count > initial_pr_count, f"Should have created at least one PR (had {initial_pr_count}, now {final_pr_count})"
    
    logger.info("Test completed successfully")