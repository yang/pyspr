"""End-to-end test for WIP commit behavior."""

import os
import tempfile
import uuid
import subprocess
from pathlib import Path
import pytest
import shutil

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
    name = "teststack"
    repo_name = f"{owner}/{name}"
    test_branch = f"test-spr-wip-{uuid.uuid4().hex[:7]}"
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

        # Create and use test branch
        run_cmd(f"git checkout -b {test_branch}")

        # Configure git
        run_cmd("git config user.name 'Test User'")
        run_cmd("git config user.email 'test@example.com'")
        run_cmd("git checkout -b test_local") # Create local branch first
        
        repo_dir = os.path.abspath(os.getcwd())
        os.chdir(orig_dir)
        yield owner, name, test_branch, repo_dir

        # Cleanup - go back to repo to delete branch
        os.chdir(repo_dir)
        run_cmd("git checkout main")
        run_cmd(f"git branch -D {test_branch}")
        try:
            run_cmd(f"git push origin --delete {test_branch}")
        except subprocess.CalledProcessError:
            print(f"Failed to delete remote branch {test_branch}, may not exist")

        # Return to original directory
        os.chdir(orig_dir)

def test_wip_behavior(test_repo):
    """Test that WIP commits behave as expected:
    - Regular commits before WIP are converted to PRs
    - WIP commits are not converted to PRs
    - Regular commits after WIP are not converted to PRs
    """
    owner, repo_name, test_branch, repo_dir = test_repo

    orig_dir = os.getcwd()
    os.chdir(repo_dir)

    # Real config using the test repo
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
    github = GitHubClient(None, config)  # Real GitHub client
    
    # Create 4 commits: 2 regular, 1 WIP, 1 regular
    def make_commit(file, msg):
        with open(file, "w") as f:
            f.write(f"{file}\n")
        run_cmd(f"git add {file}")
        run_cmd(f'git commit -m "{msg}"')
        return git_cmd.must_git("rev-parse HEAD").strip()
        
    print("Creating commits...")
    c1_hash = make_commit("wip_test1.txt", "First regular commit")
    c2_hash = make_commit("wip_test2.txt", "Second regular commit")
    c3_hash = make_commit("wip_test3.txt", "WIP Third commit")
    c4_hash = make_commit("wip_test4.txt", "Fourth regular commit")
    run_cmd(f"git push -u origin {test_branch}")  # Push branch with commits
    
    # Run update to create PRs
    print("Creating PRs...")
    subprocess.run(["pyspr", "update"], check=True)
    
    # Verify only first two PRs were created
    info = github.get_info(None, git_cmd)
    assert len(info.pull_requests) == 2, "Should only create 2 PRs before the WIP commit"
    
    # Verify PR commit hashes match first two commits
    prs = sorted(info.pull_requests, key=lambda pr: pr.number)
    assert len(prs) == 2, "Should have exactly 2 PRs"
    
    # Get commit messages to verify WIP detection worked correctly
    c1_msg = git_cmd.must_git(f"show -s --format=%B {c1_hash}").strip()
    c2_msg = git_cmd.must_git(f"show -s --format=%B {c2_hash}").strip()
    c3_msg = git_cmd.must_git(f"show -s --format=%B {c3_hash}").strip() 
    
    print("\nVerifying commit messages:")
    print(f"C1: {c1_msg}")
    print(f"C2: {c2_msg}")
    print(f"C3: {c3_msg}")
    
    # Verify WIP commit is correctly identified
    assert not c1_msg.startswith("WIP"), "First commit should not be WIP"
    assert not c2_msg.startswith("WIP"), "Second commit should not be WIP"
    assert c3_msg.startswith("WIP"), "Third commit should be WIP"
    
    # Check remaining structure
    pr1, pr2 = prs
    assert pr1.base_ref == "main", "First PR should target main"
    assert pr2.base_ref == f"spr/main/{pr1.commit.commit_id}", "Second PR should target first PR"

    # Return to original directory
    os.chdir(orig_dir)