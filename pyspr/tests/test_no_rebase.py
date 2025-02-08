"""Unit test for --no-rebase functionality."""

import os
import pytest
import io
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch

from pyspr.config import Config
from pyspr.spr import StackedPR

def test_no_rebase_functionality():
    """Test that --no-rebase properly skips rebasing.
    
    Mock all external dependencies to test only the core logic.
    """
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

    # Test regular update - should rebase
    with patch.dict(os.environ, {}, clear=True):  # Ensure no SPR_NOREBASE
        f = io.StringIO()
        with redirect_stdout(f):
            spr = StackedPR(config, github_mock, git_mock)
            spr.fetch_and_get_github_info(None)
        regular_update_log = f.getvalue()

    # Verify rebase was attempted
    assert "DEBUG: no_rebase=False" in regular_update_log
    git_mock.must_git.assert_any_call("rebase origin/main --autostash")

    # Reset mock
    git_mock.reset_mock()

    # Test no-rebase update
    with patch.dict(os.environ, {"SPR_NOREBASE": "true"}):
        f = io.StringIO()
        with redirect_stdout(f):
            spr = StackedPR(config, github_mock, git_mock)
            spr.fetch_and_get_github_info(None)
        no_rebase_log = f.getvalue()

    # Verify rebase was skipped
    assert "DEBUG: no_rebase=True" in no_rebase_log
    
    # Check that rebase was never called
    rebase_calls = [call for call in git_mock.must_git.call_args_list 
                   if "rebase" in str(call)]
    assert not rebase_calls, "Should not call rebase when no_rebase=True"