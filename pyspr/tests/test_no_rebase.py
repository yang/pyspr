"""Unit test for --no-rebase functionality."""

import os
import logging
from unittest.mock import MagicMock, patch
from dataclasses import dataclass
from typing import List, Optional

from pyspr.config import Config
from pyspr.spr import StackedPR
from pyspr.github import PullRequest

@dataclass
class MockGithubInfo:
    local_branch: str = "feature-branch"
    pull_requests: List[PullRequest] = None
    def key(self):
        return "mock-key"

def test_no_rebase_functionality(caplog: Any):
    """Test that --no-rebase properly skips rebasing.
    
    Mock all external dependencies to test only the core logic.
    """
    from typing import Any
    caplog.set_level(logging.DEBUG)
    
    # Create config
    config = Config({
        'repo': {
            'github_remote': 'origin',
            'github_branch': 'main',
        },
        'user': {
            'log_git_commands': True
        }
    })

    # Mock git and github 
    git_mock = MagicMock()
    github_mock = MagicMock()
    github_mock.get_info.return_value = MockGithubInfo()

    # Setup git mock for successful checks
    git_mock.must_git.side_effect = lambda cmd, *args, **kwargs: {
        "remote": "origin",
        "fetch": "",
        "rev-parse --verify origin/main": "",
        "rebase origin/main --autostash": ""
    }.get(cmd, "")

    # Test regular update - should rebase
    with patch.dict(os.environ, {}, clear=True):  # Ensure no SPR_NOREBASE
        spr = StackedPR(config, github_mock, git_mock)
        spr.fetch_and_get_github_info(None)

    # Verify rebase was attempted
    assert any("DEBUG: no_rebase=False" in record.message 
               for record in caplog.records)
    git_mock.must_git.assert_any_call("rebase origin/main --autostash")

    # Reset mock and clear logs
    git_mock.reset_mock()
    caplog.clear()

    # Reset git mock for no-rebase test
    git_mock.must_git.side_effect = lambda cmd, *args, **kwargs: {
        "remote": "origin",
        "fetch": "",
        "rev-parse --verify origin/main": "",
    }.get(cmd, "")
    
    # Test no-rebase update
    with patch.dict(os.environ, {"SPR_NOREBASE": "true"}):
        spr = StackedPR(config, github_mock, git_mock)
        spr.fetch_and_get_github_info(None)

    # Verify rebase was skipped
    assert any("DEBUG: no_rebase=True" in record.message 
               for record in caplog.records)
    
    # Check that rebase was never called
    rebase_calls = [call for call in git_mock.must_git.call_args_list 
                   if "rebase" in str(call)]
    assert not rebase_calls, "Should not call rebase when no_rebase=True"