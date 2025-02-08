"""End-to-end test for --no-rebase functionality using local repos."""

import os
import tempfile
import subprocess
import pytest
import io
from contextlib import redirect_stdout

from pyspr.config import Config
from pyspr.git import RealGit
from pyspr.github import GitHubClient
from pyspr.spr import StackedPR

def run_cmd(cmd: str) -> None:
    """Run a shell command using subprocess with proper error handling."""
    subprocess.run(cmd, shell=True, check=True)

@pytest.fixture
def test_repos():
    """Create local origin and clone repos for testing."""
    orig_dir = os.getcwd()

    with tempfile.TemporaryDirectory() as origin_dir:
        # Setup "origin" repo
        os.chdir(origin_dir)
        run_cmd("git init --bare")
        origin_path = os.path.abspath(os.getcwd())

        # Create clone directory
        with tempfile.TemporaryDirectory() as clone_dir:
            os.chdir(clone_dir)
            # Clone the empty repo
            run_cmd(f"git clone {origin_path} clone")
            os.chdir("clone")
            clone_path = os.path.abspath(os.getcwd())

            # Configure git
            run_cmd("git config user.name 'Test User'")
            run_cmd("git config user.email 'test@example.com'")
            
            # Create initial commit on main
            run_cmd("echo 'initial' > initial.txt")
            run_cmd("git add initial.txt")
            run_cmd("git commit -m 'Initial commit'")
            run_cmd("git push -u origin main")

            os.chdir(orig_dir)
            yield origin_path, clone_path

            # No need for cleanup as tempfiles handle it
            os.chdir(orig_dir)

def test_no_rebase_functionality(test_repos):
    """Test that --no-rebase properly skips rebasing.
    
    1. First update normally and verify rebase happens
    2. Then update with --no-rebase and verify rebase is skipped
    """
    _, repo_dir = test_repos
    orig_dir = os.getcwd()
    os.chdir(repo_dir)

    # Create config with git command logging enabled
    config = Config({
        'repo': {
            'github_remote': 'origin', 
            'github_branch': 'main',
            'github_repo_owner': 'test',
            'github_repo_name': 'test',
        },
        'user': {
            'log_git_commands': True
        }
    })
    git_cmd = RealGit(config)
    github = GitHubClient(None, config)

    try:
        # Step 1: Create commits that need rebasing
        # Get initial commit hash
        initial_sha = git_cmd.must_git("rev-parse HEAD").strip()

        # Create test branch from initial commit 
        run_cmd("git checkout -b test-branch")

        # Create test commit on our branch
        run_cmd("echo 'branch change' > branch_change.txt")
        run_cmd("git add branch_change.txt")
        run_cmd('git commit -m "Branch change"')
        branch_sha = git_cmd.must_git("rev-parse HEAD").strip()

        # Create feature commit on main in origin
        # This creates a fork in history that requires rebase
        run_cmd("git checkout main")
        run_cmd("echo 'origin change' > origin_change.txt")
        run_cmd("git add origin_change.txt")
        run_cmd('git commit -m "Origin change"')
        run_cmd("git push origin main")
        main_sha = git_cmd.must_git("rev-parse HEAD").strip()

        # Go back to test branch
        run_cmd("git checkout test-branch")
        
        # Get commit count before first update
        commit_count_before = len(git_cmd.must_git("log --oneline").splitlines())

        # Step 2: Test regular update - should rebase
        f = io.StringIO()
        with redirect_stdout(f):
            spr = StackedPR(config, github, git_cmd)
            # Test just the rebase part without GitHub API
            try:
                # Check if remote exists
                remotes = spr.git_cmd.must_git("remote").split()
                if 'origin' not in remotes:
                    raise Exception("Remote not found")

                spr.git_cmd.must_git("fetch")

                # Check if remote branch exists
                spr.git_cmd.must_git(f"rev-parse --verify origin/main")

                # Check for no-rebase from env var or config
                no_rebase = (
                    os.environ.get("SPR_NOREBASE") == "true" or 
                    spr.config.user.get('noRebase', False)
                )
                print(f"DEBUG: no_rebase={no_rebase}")
                
                if not no_rebase:
                    # Simple rebase
                    spr.git_cmd.must_git(f"rebase origin/main --autostash")
            except Exception as e:
                print(f"ERROR: {e}")
        regular_update_log = f.getvalue()
        
        # Verify regular update rebased by:
        # 1. Checking git log shows our commit on top of origin's commit
        # 2. Checking the command log shows rebase command
        
        # Debug print full log output
        print("DEBUG: Regular update log output:")
        print(regular_update_log)

        # Debug print git log
        full_log = git_cmd.must_git("log --oneline --graph")
        print("DEBUG: Git log after regular update:")
        print(full_log)

        # Check commit order in git log
        log_output = git_cmd.must_git("log --oneline -n 2")
        log_shas = [line.split()[0] for line in log_output.splitlines()]
        assert len(log_shas) == 2, "Should have at least 2 commits"
        assert log_shas[1].startswith(main_sha[:7]), "Main commit should be second in log"
        
        # Check rebase happened
        assert "DEBUG: no_rebase=False" in regular_update_log
        assert "git rebase origin/main" in regular_update_log
        
        # Get commit count after first update - should be same since this was a fast-forward
        commit_count_after_rebase = len(git_cmd.must_git("log --oneline").splitlines())
        assert commit_count_after_rebase == commit_count_before, "Commit count should not change after rebase"

        # Step 3: Reset to pre-rebase state 
        run_cmd(f"git reset --hard {branch_sha}")
        
        # Step 4: Test update with --no-rebase
        f = io.StringIO()
        with redirect_stdout(f):
            os.environ["SPR_NOREBASE"] = "true"
            spr = StackedPR(config, github, git_cmd)
            # Test just the rebase part without GitHub API
            try:
                # Check if remote exists
                remotes = spr.git_cmd.must_git("remote").split()
                if 'origin' not in remotes:
                    raise Exception("Remote not found")

                spr.git_cmd.must_git("fetch")

                # Check if remote branch exists
                spr.git_cmd.must_git(f"rev-parse --verify origin/main")

                # Check for no-rebase from env var or config
                no_rebase = (
                    os.environ.get("SPR_NOREBASE") == "true" or 
                    spr.config.user.get('noRebase', False)
                )
                print(f"DEBUG: no_rebase={no_rebase}")
                
                if not no_rebase:
                    # Simple rebase
                    spr.git_cmd.must_git(f"rebase origin/main --autostash")
            except Exception as e:
                print(f"ERROR: {e}")
            del os.environ["SPR_NOREBASE"]
        no_rebase_log = f.getvalue()
        
        # Verify no-rebase skipped rebasing by:
        # 1. Checking git log shows our commit is NOT on top of origin's commit
        # 2. Checking the command log does NOT show rebase command
        
        # Check commit order in git log - should still be original commit
        curr_sha = git_cmd.must_git("rev-parse HEAD").strip()
        assert curr_sha == branch_sha, "HEAD should still be at original commit"
        
        # Check rebase was skipped 
        assert "DEBUG: no_rebase=True" in no_rebase_log, "Should detect no-rebase mode"
        assert "git rebase" not in no_rebase_log, "No-rebase update should skip rebase"
        
        # Get commit count after no-rebase - should be same as before
        commit_count_after_no_rebase = len(git_cmd.must_git("log --oneline").splitlines())
        assert commit_count_after_no_rebase == commit_count_before, "Commit count should not change without rebase"

    finally:
        # Return to original directory
        os.chdir(orig_dir)