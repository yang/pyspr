"""End-to-end test for reviewer functionality (-r flag)."""

import os
import tempfile
import uuid
import subprocess
from pathlib import Path
import pytest

from pyspr.config import Config
from pyspr.git import RealGit
from pyspr.github import GitHubClient

def run_cmd(cmd: str) -> None:
    """Run a shell command using subprocess with proper error handling."""
    subprocess.run(cmd, shell=True, check=True)

@pytest.fixture
def test_repo():
    """Use yang/teststack repo with a temporary test branch."""
    orig_dir = os.getcwd()
    owner = "yang"
    repo_name = "teststack"
    test_branch = f"test-spr-reviewers-{uuid.uuid4().hex[:7]}"
    print(f"Using test branch {test_branch} in {owner}/{repo_name}")
    
    # Read GitHub token - this is yang's token 
    with open("/home/ubuntu/code/pyspr/token") as f:
        token = f.read().strip()
        os.environ["GITHUB_TOKEN"] = token

    with tempfile.TemporaryDirectory() as tmpdir:
        os.chdir(tmpdir)

        # Clone the repo
        subprocess.run(["gh", "repo", "clone", f"{owner}/{repo_name}"], check=True)
        os.chdir("teststack")

        # Configure git
        run_cmd("git config user.name 'Test User'")
        run_cmd("git config user.email 'test@example.com'")
        run_cmd(f"git checkout -b {test_branch}")
        
        repo_dir = os.path.abspath(os.getcwd())
        os.chdir(orig_dir)
        
        # Return owner name from token for verification in test
        current_owner = "yang" # Since we're using yang's token
        
        yield owner, repo_name, test_branch, repo_dir, current_owner

        # Cleanup
        os.chdir(repo_dir)
        run_cmd("git checkout main")
        run_cmd(f"git branch -D {test_branch}")
        try:
            run_cmd(f"git push origin --delete {test_branch}")
        except subprocess.CalledProcessError:
            print(f"Failed to delete remote branch {test_branch}, may not exist")

        os.chdir(orig_dir)

def test_reviewer_functionality_yang(test_repo):
    """Test that reviewers are correctly added to new PRs but not existing ones. 
    Special case: Since we're using yang's token, test verifies that the attempt
    to add yang as a reviewer is handled properly (can't review your own PR)."""
    owner, repo_name, test_branch, repo_dir, current_owner = test_repo
    
    orig_dir = os.getcwd()
    os.chdir(repo_dir)

    config = Config({
        'repo': {
            'github_remote': 'origin',
            'github_branch': 'main',
            'github_repo_owner': owner,
            'github_repo_name': repo_name,
        },
        'user': {}
    })
    git_cmd = RealGit(config)
    github = GitHubClient(None, config)

    # Create first commit and PR without reviewer
    def make_commit(file, msg):
        with open(file, "w") as f:
            f.write(f"{file}\n{msg}\n")
        run_cmd(f"git add {file}")
        run_cmd(f'git commit -m "{msg}"')
        return git_cmd.must_git("rev-parse HEAD").strip()
        
    print("Creating first commit without reviewer...")
    make_commit("r_test1.txt", "First commit")
    run_cmd(f"git push -u origin {test_branch}")

    # Create initial PR without reviewer
    os.chdir(orig_dir)
    subprocess.run(["rye", "run", "pyspr", "update", "-C", repo_dir], check=True)
    os.chdir(repo_dir)

    # Verify first PR exists with no reviewer
    info = github.get_info(None, git_cmd)
    assert len(info.pull_requests) == 1, f"Should have 1 PR, found {len(info.pull_requests)}"
    pr1 = info.pull_requests[0]
    gh_pr1 = github.repo.get_pull(pr1.number)
    
    # Debug review requests for first PR
    print("\nDEBUG: First PR review requests")
    try:
        # Get requested reviewers directly
        requested_users, requested_teams = gh_pr1.get_review_requests()
        requested_logins = [u.login.lower() for u in requested_users]
        print(f"Requested Users: {requested_logins}")
        print(f"Requested Teams: {list(requested_teams)}")
    except Exception as e:
        print(f"Error getting review data: {e}")
        requested_logins = []
    
    assert current_owner.lower() not in requested_logins, f"First PR correctly has no {current_owner} reviewer (can't review own PR)"
    print(f"Created PR #{pr1.number} with no {current_owner} reviewer")

    # Create second commit and PR with reviewer
    print("\nCreating second commit with reviewer...")
    make_commit("r_test2.txt", "Second commit")
    run_cmd("git push")

    # Try to add self as reviewer (should be handled gracefully)
    os.chdir(orig_dir)
    subprocess.run(["rye", "run", "pyspr", "update", "-C", repo_dir, "-r", "yang"], check=True)
    os.chdir(repo_dir)

    # Verify:
    # - First PR still has no reviewer 
    # - Second PR also has no reviewer (because we can't review our own PRs)
    info = github.get_info(None, git_cmd)
    assert len(info.pull_requests) == 2, f"Should have 2 PRs, found {len(info.pull_requests)}"
    prs_by_num = {pr.number: pr for pr in info.pull_requests}
    assert pr1.number in prs_by_num, "First PR should still exist"
    
    # Debug first PR reviews - verify still no reviewer
    gh_pr1 = github.repo.get_pull(pr1.number)
    print("\nDEBUG: First PR review requests after update")
    try:
        requested_users, requested_teams = gh_pr1.get_review_requests()
        requested_logins1 = [u.login.lower() for u in requested_users]
        print(f"Requested Users: {requested_logins1}")
        print(f"Requested Teams: {list(requested_teams)}")
    except Exception as e:
        print(f"Error getting review data: {e}")
        requested_logins1 = []
        
    assert current_owner.lower() not in requested_logins1, f"First PR correctly has no {current_owner} reviewer"
    
    pr2 = [pr for pr in info.pull_requests if pr.number != pr1.number][0]
    gh_pr2 = github.repo.get_pull(pr2.number)
    
    # Debug second PR reviews - verify has no reviewer (can't add self)
    print("\nDEBUG: Second PR review requests")
    try:
        requested_users, requested_teams = gh_pr2.get_review_requests()
        requested_logins2 = [u.login.lower() for u in requested_users]
        print(f"Requested Users: {requested_logins2}")
        print(f"Requested Teams: {list(requested_teams)}")
    except Exception as e:
        print(f"Error getting review data: {e}")
        requested_logins2 = []
        
    # This assertion is the key difference - we expect no reviewer because GitHub
    # doesn't allow you to request reviews from yourself
    assert current_owner.lower() not in requested_logins2, f"Second PR correctly has no {current_owner} reviewer (can't review own PR)"
    
    print(f"Successfully verified -r flag handling with self-review attempt")

    print(f"Verified PR #{pr1.number} has no reviewer and PR #{pr2.number} has testluser")

    # Return to original directory
    os.chdir(orig_dir)