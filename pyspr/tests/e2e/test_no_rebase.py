"""End-to-end test for --no-rebase functionality."""

import os
import tempfile
import uuid
import subprocess
import pytest
import io
from contextlib import redirect_stdout
from typing import Tuple

from pyspr.config import Config
from pyspr.git import RealGit
from pyspr.github import GitHubClient
from pyspr.spr import StackedPR

def run_cmd(cmd: str) -> None:
    """Run a shell command using subprocess with proper error handling."""
    subprocess.run(cmd, shell=True, check=True)

@pytest.fixture
def test_repo():
    """Use yang/teststack repo with a temporary test branch."""
    orig_dir = os.getcwd()
    owner = "yang"
    name = "teststack"
    repo_name = f"{owner}/{name}"
    test_branch = f"test-no-rebase-{uuid.uuid4().hex[:7]}"
    print(f"Using test branch {test_branch} in {repo_name}")
    
    # Read GitHub token
    with open("/home/ubuntu/code/pyspr/token") as f:
        token = f.read().strip()
        os.environ["GITHUB_TOKEN"] = token

    with tempfile.TemporaryDirectory() as tmpdir:
        os.chdir(tmpdir)

        # Clone the repo
        subprocess.run(["gh", "repo", "clone", repo_name], check=True)
        os.chdir("teststack")

        # Configure git
        run_cmd("git config user.name 'Test User'")
        run_cmd("git config user.email 'test@example.com'")
        
        # Ensure main branch is up to date
        run_cmd("git checkout main")
        run_cmd("git pull")
        
        repo_dir = os.path.abspath(os.getcwd())
        os.chdir(orig_dir)
        yield owner, name, test_branch, repo_dir

        # Cleanup
        os.chdir(repo_dir)
        run_cmd("git checkout main")
        try:
            run_cmd(f"git branch -D {test_branch}")
        except subprocess.CalledProcessError:
            print(f"Branch {test_branch} could not be deleted")
        try:
            run_cmd(f"git push origin --delete {test_branch}")
        except subprocess.CalledProcessError:
            print(f"Failed to delete remote branch {test_branch}, may not exist")

        os.chdir(orig_dir)

def test_no_rebase_functionality(test_repo):
    """Test that --no-rebase properly skips rebasing.
    
    1. First update normally and verify rebase happens
    2. Then update with --no-rebase and verify rebase is skipped
    """
    owner, name, test_branch, repo_dir = test_repo
    orig_dir = os.getcwd()
    os.chdir(repo_dir)

    # Create config with git command logging enabled
    config = Config({
        'repo': {
            'github_remote': 'origin',
            'github_branch': 'main',
            'github_repo_owner': owner,
            'github_repo_name': name,
        },
        'user': {
            'log_git_commands': True
        }
    })
    git_cmd = RealGit(config)
    github = GitHubClient(None, config)

    try:
        # Step 1: Create commits that need rebasing
        # First create our test branch
        run_cmd(f"git checkout -b {test_branch}")
        run_cmd(f"git push -u origin {test_branch}")

        # Create test commit on our branch
        run_cmd("echo 'branch change' > branch_change.txt")
        run_cmd("git add branch_change.txt")
        run_cmd('git commit -m "Branch change"')
        branch_sha = git_cmd.must_git("rev-parse HEAD").strip()

        # Create feature commit on main in origin
        run_cmd("git checkout main")
        orig_main_sha = git_cmd.must_git("rev-parse HEAD").strip()
        run_cmd("echo 'origin change' > origin_change.txt")
        run_cmd("git add origin_change.txt")
        run_cmd('git commit -m "Origin change"')
        run_cmd("git push origin main")
        origin_main_sha = git_cmd.must_git("rev-parse HEAD").strip()

        # Go back to test branch
        run_cmd(f"git checkout {test_branch}")
        
        # Get commit count before first update
        commit_count_before = len(git_cmd.must_git("log --oneline").splitlines())

        # Step 2: Test regular update - should rebase
        os.chdir(orig_dir)
        f = io.StringIO()
        with redirect_stdout(f):
            spr = StackedPR(config, github, git_cmd)
            # Pass None for reviewers and count to match CLI behavior
            spr.update_pull_requests(None, None, None)
        regular_update_log = f.getvalue()
        os.chdir(repo_dir)
        
        # Verify regular update rebased by:
        # 1. Checking git log shows our commit on top of origin's commit
        # 2. Checking the command log shows rebase command
        
        # Check commit order in git log
        log_output = git_cmd.must_git("log --oneline -n 2")
        log_shas = [line.split()[0] for line in log_output.splitlines()]
        assert len(log_shas) == 2, "Should have at least 2 commits"
        assert log_shas[1].startswith(origin_main_sha[:7]), "Origin commit should be second in log"
        
        # Check rebase happened
        assert "git rebase" in regular_update_log, "Regular update should perform rebase"
        
        # Get commit count after first update - should increase by 1 due to rebase
        commit_count_after_rebase = len(git_cmd.must_git("log --oneline").splitlines())
        assert commit_count_after_rebase > commit_count_before, "Commit count should increase after rebase"

        # Step 3: Reset to pre-rebase state 
        run_cmd(f"git reset --hard {branch_sha}")
        
        # Step 4: Test update with --no-rebase
        os.chdir(orig_dir)
        f = io.StringIO()
        with redirect_stdout(f):
            # Set env var like CLI does
            os.environ["SPR_NOREBASE"] = "true"
            spr = StackedPR(config, github, git_cmd)
            spr.update_pull_requests(None, None, None)
            del os.environ["SPR_NOREBASE"]
        no_rebase_log = f.getvalue()
        os.chdir(repo_dir)
        
        # Verify no-rebase skipped rebasing by:
        # 1. Checking git log shows our commit is NOT on top of origin's commit
        # 2. Checking the command log does NOT show rebase command
        
        # Check commit order in git log - should still be original commit
        curr_sha = git_cmd.must_git("rev-parse HEAD").strip()
        assert curr_sha == branch_sha, "HEAD should still be at original commit"
        
        # Check rebase was skipped
        assert "git rebase" not in no_rebase_log, "No-rebase update should skip rebase"
        
        # Get commit count after no-rebase - should be same as before
        commit_count_after_no_rebase = len(git_cmd.must_git("log --oneline").splitlines())
        assert commit_count_after_no_rebase == commit_count_before, "Commit count should not change without rebase"

    finally:
        # Return to original directory
        os.chdir(orig_dir)

def test_no_rebase_pr_stacking(test_repo: Tuple[str, str, str, str]):
    """Test that stacking PRs with --no-rebase preserves earlier PRs.

    1. Create initial PR and push with regular update
    2. Create second PR and push with --no-rebase
    3. Verify first PR commit hash is unchanged
    4. Verify stack links are properly updated
    """
    owner, name, test_branch, repo_dir = test_repo
    orig_dir = os.getcwd()
    os.chdir(repo_dir)

    config = Config({
        'repo': {
            'github_remote': 'origin',
            'github_branch': 'main',
            'github_repo_owner': owner,
            'github_repo_name': name,
        },
        'user': {}
    })
    git_cmd = RealGit(config)
    github = GitHubClient(None, config)

    try:
        # Create unique tag for test run
        unique_tag = f"test-norebase-stack-{uuid.uuid4().hex[:8]}"
        
        def make_commit(file_name: str, msg: str) -> str:
            full_msg = f"{msg} [test-tag:{unique_tag}]"
            with open(file_name, "w") as f:
                f.write(f"first change in {file_name}\n")
            run_cmd(f"git add {file_name}")
            run_cmd(f'git commit -m "{full_msg}"')
            return git_cmd.must_git("rev-parse HEAD").strip()

        # Step 1: Create initial PR
        run_cmd(f"git checkout -b {test_branch}")
        run_cmd(f"git push -u origin {test_branch}")
        first_commit_hash = make_commit("first.txt", "First commit")

        # Regular update for first PR
        os.chdir(orig_dir)
        subprocess.run(["rye", "run", "pyspr", "update", "-C", repo_dir], check=True)
        os.chdir(repo_dir)
        
        def get_test_prs() -> list:
            result = []
            for pr in github.get_info(None, git_cmd).pull_requests:
                if pr.from_branch.startswith('spr/main/'):
                    try:
                        # Look for our unique tag in the commit message
                        commit_msg = git_cmd.must_git(f"show -s --format=%B {pr.commit.commit_hash}")
                        if f"test-tag:{unique_tag}" in commit_msg:
                            result.append(pr)
                    except:  # Skip failures since we're just filtering
                        pass
            return result

        # Get first PR info
        info = github.get_info(None, git_cmd)
        assert info is not None, "GitHub info should not be None"
        prs = get_test_prs()
        assert len(prs) == 1, "Should have 1 PR"
        first_pr = prs[0]
        first_pr_number = first_pr.number
        first_pr_hash = first_pr.commit.commit_hash 

        # Step 2: Add second commit and update with --no-rebase
        second_commit_hash = make_commit("second.txt", "Second commit")

        os.chdir(orig_dir)
        subprocess.run(["rye", "run", "pyspr", "update", "-C", repo_dir, "-nr"], check=True)
        os.chdir(repo_dir)

        # Step 3: Verify first PR commit hash unchanged
        info = github.get_info(None, git_cmd)
        assert info is not None, "GitHub info should not be None"
        prs = get_test_prs()
        assert len(prs) == 2, "Should have 2 PRs"

        # Find first PR by number
        first_pr_after = next((pr for pr in prs if pr.number == first_pr_number), None)
        assert first_pr_after is not None, "First PR should still exist"
        assert first_pr_after.commit.commit_hash == first_pr_hash, "First PR commit hash should be unchanged"

        # Find second PR
        second_pr = next((pr for pr in prs if pr.number != first_pr_number), None)
        assert second_pr is not None, "Second PR should exist"
        assert second_pr.commit.commit_hash == second_commit_hash, "Second PR should have correct commit hash"

        # Step 4: Verify stack links updated
        assert first_pr_after.base_ref == "main", "First PR should target main"
        assert second_pr.base_ref is not None and second_pr.base_ref.startswith('spr/main/'), \
               "Second PR should target branch"
        assert first_pr_after.commit.commit_id in second_pr.base_ref, \
               "Second PR should target first PR's commit"

        # Verify PR bodies 
        assert github.repo is not None, "GitHub repo should be available"
        gh_first_pr = github.repo.get_pull(first_pr_number)
        gh_second_pr = github.repo.get_pull(second_pr.number)
        assert str(second_pr.number) in gh_first_pr.body, "First PR body should link to second PR"
        assert str(first_pr_number) in gh_second_pr.body, "Second PR body should link to first PR" 

    finally:
        os.chdir(orig_dir)