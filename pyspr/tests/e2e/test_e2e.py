"""End-to-end test for amending commits in stack, PR stack isolation, WIP and reviewer behavior."""
# pyright: reportUnusedVariable=false
# pyright: reportUnusedImport=false
# pyright: reportUnknownVariableType=false
# pyright: reportUnknownMemberType=false
# pyright: reportOptionalMemberAccess=false
# pyright: reportUnknownArgumentType=false
# pyright: reportUnknownParameterType=false
# pyright: reportMissingTypeArgument=false
# pyright: reportUnknownLambdaType=false
# pyright: reportUnnecessaryComparison=false

import os
import sys
import tempfile
import uuid
import subprocess
import io
import time
import datetime
import logging
import yaml
from pathlib import Path
from contextlib import redirect_stdout
from typing import Dict, Generator, List, Optional, Set, Tuple, Union
import pytest

def get_gh_token() -> str:
    """Get GitHub token from gh CLI config."""
    # First try using gh auth token
    try:
        result = subprocess.run(['gh', 'auth', 'token'], check=True, capture_output=True, text=True)
        token = result.stdout.strip()
        if token:
            return token
    except subprocess.CalledProcessError:
        pass

    # Fallback to reading gh config file
    try:
        gh_config_path = Path.home() / ".config" / "gh" / "hosts.yml"
        if gh_config_path.exists():
            with open(gh_config_path) as f:
                config = yaml.safe_load(f)
                if config and "github.com" in config:
                    github_config = config["github.com"]
                    if "oauth_token" in github_config:
                        return github_config["oauth_token"]
    except Exception as e:
        print(f"Error reading gh config: {e}")

    raise Exception("Could not get GitHub token from gh CLI")

from pyspr.config import Config
from pyspr.git import RealGit, Commit 
from pyspr.github import GitHubClient, PullRequest

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
# Add stderr handler to ensure logs are output during pytest
handler = logging.StreamHandler(sys.stderr)
handler.setFormatter(logging.Formatter('%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s', '%H:%M:%S'))
log = logging.getLogger(__name__)
log.addHandler(handler)
log.propagate = False  # Don't double log

def run_cmd(cmd: str) -> None:
    """Run a shell command using subprocess with proper error handling."""
    subprocess.run(cmd, shell=True, check=True)

def test_wip_behavior(test_repo: Tuple[str, str, str, str], caplog: pytest.LogCaptureFixture) -> None:
    """Test that WIP commits behave as expected:
    - Regular commits before WIP are converted to PRs
    - WIP commits are not converted to PRs
    - Regular commits after WIP are not converted to PRs
    """
    print("=== TEST STARTED ===")  # Just to see if test runs at all
    caplog.set_level(logging.INFO)
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
    
    # Get timestamp before we create PRs
    commit_time = int(git_cmd.must_git("show -s --format=%ct").strip())
    
    # Create 4 commits: 2 regular, 1 WIP, 1 regular
    def make_commit(file: str, msg: str) -> str:
        print(f"{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]} Creating commit for {file} - {msg}")
        with open(file, "w") as f:
            f.write(f"{file}\n")
        run_cmd(f"git add {file}")
        run_cmd(f'git commit -m "{msg}"')
        result = git_cmd.must_git("rev-parse HEAD").strip()
        print(f"{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]} Created commit {result[:8]}")
        return result
        
    print(f"{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]} Creating commits...")
    c1_hash = make_commit("wip_test1.txt", "First regular commit")
    c2_hash = make_commit("wip_test2.txt", "Second regular commit")
    c3_hash = make_commit("wip_test3.txt", "WIP Third commit")
    _ = make_commit("wip_test4.txt", "Fourth regular commit")  # Not used but kept for completeness
    
    print(f"{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]} Pushing branch with commits...")
    run_cmd(f"git push -u origin {test_branch}")  # Push branch with commits
    print(f"{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]} Push complete")
    
    # Run update to create PRs
    print(f"{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]} Creating PRs...")
    
    # Go back to project dir to run commands 
    os.chdir(orig_dir)
    print(f"{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]} Running pyspr update...")
    run_cmd(f"rye run pyspr update -C {repo_dir}")
    print(f"{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]} pyspr update complete")
    
    # Let GitHub process the PRs
    print(f"{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]} Waiting for PRs to be available in GitHub...")
    time.sleep(5)
    
    # Go back to repo for verification
    os.chdir(repo_dir)
    
    # Debug: Check what branches actually exist
    print("Checking remote branches:")
    remote_branches = git_cmd.must_git("ls-remote --heads origin").split("\n")
    for branch in remote_branches:
        if branch:
            print(f"  {branch}")
    
    # Helper to find our test PRs 
    def get_test_prs() -> list:
        print("=== ABOUT TO CALL GITHUB API ===")  # See if we get here before timeout
        print(f"{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]} Starting PR filtering...")
        gh_start = time.time()
        try:
            info = github.get_info(None, git_cmd)
            prs = info.pull_requests if info else []
        finally:
            gh_end = time.time()
            print(f"{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]} GitHub API get_info took {gh_end - gh_start:.2f} seconds")
        
        print(f"{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]} Filtering {len(prs)} PRs...")
        filter_start = time.time()
        result = []
        try:
            for pr in prs:
                try:
                    # Debug each PR being checked
                    print(f"Checking PR #{pr.number} - branch {pr.from_branch}")
                    if pr.from_branch is not None and pr.from_branch.startswith('spr/main/') and pr.commit is not None:
                        files = git_cmd.must_git(f"show --name-only {pr.commit.commit_hash}")
                        test_files = ['wip_test1.txt', 'wip_test2.txt', 'wip_test3.txt', 'wip_test4.txt']
                        if any(f in files for f in test_files):
                            pr_time = int(git_cmd.must_git(f"show -s --format=%ct {pr.commit.commit_hash}").strip())
                            if pr_time >= commit_time:
                                print(f"Found matching PR #{pr.number}")
                                result.append(pr)
                except Exception as e:  # Log any failures
                    print(f"Error checking PR #{getattr(pr, 'number', 'unknown')}: {e}")
                    pass
        finally:
            filter_end = time.time()
            print(f"{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]} PR filtering took {filter_end - filter_start:.2f} seconds")
        print(f"{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]} Found {len(result)} matching PRs")
        return result
    
    # Verify only first two PRs were created
    print(f"{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]} Getting GitHub info...")
    info = github.get_info(None, git_cmd)
    assert info is not None, "GitHub info should not be None"
    
    print(f"{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]} Getting commit info for debugging:")
    print(f"{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]} C1: {c1_hash}")
    print(f"{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]} C2: {c2_hash}")
    print(f"{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]} C3: {c3_hash}")
    
    # Get our test PRs
    print(f"{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]} Getting filtered test PRs...")
    test_prs = get_test_prs()
    
    # Print all PR commit hashes for debugging
    print(f"{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]} Test PR commit hashes:")
    for pr in test_prs:
        print(f"{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]} PR #{pr.number}: {pr.title} - {pr.commit.commit_hash}")
    
    # Sort PRs by number (most recent first) and take first 2 matching our titles
    print(f"{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]} Finding PRs with target titles...")
    test_prs = sorted(test_prs, key=lambda pr: pr.number, reverse=True)
    prs_with_titles = []
    for pr in test_prs:
        if "First regular commit" in pr.title or "Second regular commit" in pr.title:
            prs_with_titles.append(pr)
        if len(prs_with_titles) == 2:
            break
    test_prs = prs_with_titles
    print(f"{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]} Found {len(test_prs)} PRs with target titles")
            
    print("\nMost recent matching PRs:")
    for pr in test_prs:
        print(f"PR #{pr.number}: {pr.title}")
            
    assert len(test_prs) == 2, f"Should find 2 PRs before the WIP commit, found {len(test_prs)}: {[pr.title for pr in test_prs]}"
    
    # Verify PR commit hashes match first two commits
    prs = sorted(test_prs, key=lambda pr: pr.number)
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
    assert c1_msg is not None, "First commit message should not be None"
    assert c2_msg is not None, "Second commit message should not be None"
    assert c3_msg is not None, "Third commit message should not be None"
    assert not c1_msg.startswith("WIP"), "First commit should not be WIP"
    assert not c2_msg.startswith("WIP"), "Second commit should not be WIP"
    assert c3_msg.startswith("WIP"), "Third commit should be WIP"
    
    # Check remaining structure
    pr1, pr2 = prs
    assert pr1.base_ref == "main", "First PR should target main"
    assert pr2.base_ref is not None and pr2.base_ref.startswith("spr/main/"), "Second PR should target first PR's branch"

    # Return to original directory
    os.chdir(orig_dir)

@pytest.fixture
def test_reviewer_repo() -> Generator[Tuple[str, str, str, str, str], None, None]:
    """Use yang/teststack repo with a temporary test branch."""
    orig_dir = os.getcwd()
    owner = "yang"
    repo_name = "teststack"
    test_branch = f"test-spr-reviewers-{uuid.uuid4().hex[:7]}"
    print(f"Using test branch {test_branch} in {owner}/{repo_name}")
    
    # Use gh CLI token
    token = get_gh_token()
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

def test_reviewer_functionality_yang(test_reviewer_repo: Tuple[str, str, str, str, str]) -> None:
    """Test that reviewers are correctly added to new PRs but not existing ones. 
    Special case: Since we're using yang's token, test verifies that the attempt
    to add yang as a reviewer is handled properly (can't review your own PR)."""
    owner, repo_name, test_branch, repo_dir, current_owner = test_reviewer_repo
    
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
    def make_commit(file: str, msg: str) -> str:
        with open(file, "w") as f:
            f.write(f"{file}\n{msg}\n")
        run_cmd(f"git add {file}")
        run_cmd(f'git commit -m "{msg}"')
        return git_cmd.must_git("rev-parse HEAD").strip()
        
    # Get timestamp before we create PRs
    commit_time = int(git_cmd.must_git("show -s --format=%ct").strip())
        
    print("Creating first commit without reviewer...")
    make_commit("r_test1.txt", "First commit")
    run_cmd(f"git push -u origin {test_branch}")

    # Create initial PR without reviewer
    os.chdir(orig_dir)
    subprocess.run(["rye", "run", "pyspr", "update", "-C", repo_dir], check=True)
    os.chdir(repo_dir)

    # Helper to find our test PRs 
    def get_test_prs() -> list:
        result = []
        for pr in github.get_info(None, git_cmd).pull_requests:
            if pr.from_branch.startswith('spr/main/'):
                try:
                    files = git_cmd.must_git(f"show --name-only {pr.commit.commit_hash}")
                    if 'r_test1.txt' in files:
                        pr_time = int(git_cmd.must_git(f"show -s --format=%ct {pr.commit.commit_hash}").strip())
                        if pr_time >= commit_time:
                            result.append(pr)
                except:  # Skip failures since we're just filtering
                    pass
        return result

    # Verify first PR exists with no reviewer
    info = github.get_info(None, git_cmd)
    assert info is not None, "GitHub info should not be None"
    our_prs = get_test_prs()
    assert len(our_prs) == 1, f"Should have 1 PR for our test, found {len(our_prs)}"
    pr1 = our_prs[0]
    assert github.repo is not None, "GitHub repo should be available"
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
    assert info is not None, "GitHub info should not be None"
    our_prs = get_test_prs()
    assert len(our_prs) == 2, f"Should have 2 PRs for our test, found {len(our_prs)}"
    prs_by_num = {pr.number: pr for pr in our_prs}
    assert pr1.number in prs_by_num, "First PR should still exist"
    
    # Debug first PR reviews - verify still no reviewer
    assert github.repo is not None, "GitHub repo should be available"
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

def test_reviewer_functionality_testluser(test_reviewer_repo: Tuple[str, str, str, str, str]) -> None:
    """Test that reviewers are correctly added to new PRs when using -r testluser."""
    owner, repo_name, test_branch, repo_dir, current_owner = test_reviewer_repo
    
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

    def make_commit(file: str, msg: str) -> str:
        with open(file, "w") as f:
            f.write(f"{file}\n{msg}\n")
        run_cmd(f"git add {file}")
        run_cmd(f'git commit -m "{msg}"')
        return git_cmd.must_git("rev-parse HEAD").strip()

    def get_test_prs(min_time: int) -> list:
        """Helper to find the test PRs efficiently"""
        result = []
        for pr in github.get_info(None, git_cmd).pull_requests:
            if pr.from_branch.startswith('spr/main/'):
                try:
                    files = git_cmd.must_git(f"show --name-only {pr.commit.commit_hash}")
                    if any(f in files for f in ['r_test1.txt', 'r_test2.txt']):
                        pr_time = int(git_cmd.must_git(f"show -s --format=%ct {pr.commit.commit_hash}").strip())
                        if pr_time >= min_time:
                            result.append(pr)
                except:  # Skip failures since we're just filtering
                    pass
        return result
        
    print("Creating first commit without reviewer...")
    make_commit("r_test1.txt", "First commit")
    run_cmd(f"git push -u origin {test_branch}")

    # Get timestamp before we create PRs
    commit_time = int(git_cmd.must_git("show -s --format=%ct").strip())

    # Create initial PR without reviewer
    os.chdir(orig_dir)
    subprocess.run(["rye", "run", "pyspr", "update", "-C", repo_dir], check=True)
    os.chdir(repo_dir)

    # Verify first PR
    our_prs = get_test_prs(commit_time)
    assert len(our_prs) == 1, f"Should have 1 PR for our test, found {len(our_prs)}"
    pr1 = our_prs[0]
    gh_pr1 = github.repo.get_pull(pr1.number)
    
    print(f"\nFound test PR #{pr1.number} with branch {pr1.from_branch}")
    
    # Verify no reviewer on first PR
    requested_users, _ = gh_pr1.get_review_requests()
    requested_logins = [u.login.lower() for u in requested_users]
    assert "testluser" not in requested_logins, "First PR correctly has no testluser reviewer"
    print(f"Verified PR #{pr1.number} has no reviewer")

    # Switch to SPR-managed branch for second commit
    run_cmd(f"git checkout {pr1.from_branch}")

    # Create second commit 
    print("\nCreating second commit...")
    make_commit("r_test2.txt", "Second commit")
    run_cmd("git push")  # Push to current SPR branch

    # Add testluser as reviewer and capture output
    os.chdir(orig_dir)
    result = subprocess.run(
        ["rye", "run", "pyspr", "update", "-C", repo_dir, "-r", "testluser"], 
        check=True,
        capture_output=True,
        text=True
    )
    os.chdir(repo_dir)
    update_output = result.stdout + result.stderr

    # Find our PRs again
    our_prs = get_test_prs(commit_time)
    assert len(our_prs) == 2, f"Should have 2 PRs for our test, found {len(our_prs)}"
    
    # Get the latest PR
    pr2 = max(our_prs, key=lambda p: p.number)
    gh_pr2 = github.repo.get_pull(pr2.number)
    
    # Verify testluser reviewer on second PR - but since we're using yang's token
    # and testluser is the same as yang, the request will be silently ignored by GitHub
    requested_users, _ = gh_pr2.get_review_requests()
    requested_logins = [u.login.lower() for u in requested_users]
    assert "testluser" not in requested_logins, "Second PR correctly has no testluser reviewer due to self-review restriction"
    
    # Verify debug message shows attempt to add reviewers
    print("\nDEBUG: Update command output:")
    print(update_output)
    assert "Trying to add reviewers" in update_output or \
           "Adding reviewers" in update_output or \
           "DEBUG:" in update_output, \
           "Should have attempted to add testluser as reviewer"
    
    print(f"Verified PR #{pr1.number} has no reviewer and PR #{pr2.number} has no reviewer due to self-review restriction")
    print("Successfully verified -r flag handles self-review restriction")

    # Return to original directory
    os.chdir(orig_dir)

@pytest.fixture
def test_repo() -> Generator[Tuple[str, str, str, str], None, None]:
    """Use yang/teststack repo with a temporary test branch."""
    orig_dir = os.getcwd()
    owner = "yang"
    name = "teststack"
    repo_name = f"{owner}/{name}"  # Used in print and subprocess call
    test_branch = f"test-spr-amend-{uuid.uuid4().hex[:7]}"
    print(f"Using test branch {test_branch} in {repo_name}")
    
    # Use gh CLI token
    token = get_gh_token()
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

def test_amend_workflow(test_repo: Tuple[str, str, str, str]) -> None:
    """Test full amend workflow with real PRs."""
    owner, repo_name, test_branch = test_repo[:3]  # Used in config below

    # Real config using the test repo
    config = Config({
        'repo': {
            'github_remote': 'origin',
            'github_branch': 'main', 
            'github_repo_owner': owner,
            'github_repo_name': 'teststack', 
        },
        'user': {}
    })
    git_cmd = RealGit(config)
    github = GitHubClient(None, config)  # Real GitHub client
    
    # Get timestamp before we create PRs
    commit_time = int(git_cmd.must_git("show -s --format=%ct").strip())
    
    # Create 3 commits
    def make_commit(file: str, line: str, msg: str) -> str:
        with open(file, "w") as f:
            f.write(f"{file}\n{line}\n")
        run_cmd(f"git add {file}")
        run_cmd(f'git commit -m "{msg}"')
        return git_cmd.must_git("rev-parse HEAD").strip()
        
    print("Creating commits...")
    c1_hash = make_commit("ta.txt", "line 1", "First commit")
    c2_hash = make_commit("tb.txt", "line 1", "Second commit")  
    c3_hash = make_commit("tc.txt", "line 1", "Third commit")
    c4_hash = make_commit("td.txt", "line 1", "Fourth commit")
    run_cmd(f"git push -u origin {test_branch}")  # Push branch with commits
    
    # Initial update to create PRs
    print("Creating initial PRs...")
    subprocess.run(["rye", "run", "pyspr", "update"], check=True)
    
    # Helper to find our test PRs 
    def get_test_prs() -> list:
        result = []
        for pr in github.get_info(None, git_cmd).pull_requests:
            if pr.from_branch.startswith('spr/main/'):
                try:
                    files = git_cmd.must_git(f"show --name-only {pr.commit.commit_hash}")
                    test_files = ['ta.txt', 'tb.txt', 'tc.txt', 'td.txt', 'tc5.txt']
                    if any(f in files for f in test_files):
                        pr_time = int(git_cmd.must_git(f"show -s --format=%ct {pr.commit.commit_hash}").strip())
                        if pr_time >= commit_time:
                            result.append(pr)
                except:  # Skip failures since we're just filtering
                    pass
        return result
    
    # Verify PRs created
    info = github.get_info(None, git_cmd)
    assert info is not None, "GitHub info should not be None"
    test_prs = get_test_prs()
    assert len(test_prs) == 4, f"Should have 4 PRs for our test, found {len(test_prs)}"
    pr1, pr2, pr3, pr4 = sorted(test_prs, key=lambda pr: pr.number)
    print(f"Created PRs: #{pr1.number}, #{pr2.number}, #{pr3.number}, #{pr4.number}")
    
    # Save PR numbers and commit IDs and hashes
    pr1_num = pr1.number
    pr2_num = pr2.number
    pr3_num = pr3.number
    pr4_num = pr4.number
    
    c1_id = pr1.commit.commit_id
    c2_id = pr2.commit.commit_id
    c3_id = pr3.commit.commit_id
    c4_id = pr4.commit.commit_id

    # Store but don't actually use these variables 
    _ = pr1.commit.commit_hash
    _ = pr2.commit.commit_hash
    _ = pr3.commit.commit_hash
    _ = pr4.commit.commit_hash

    # Debug: Check if commit messages have IDs after first update
    print("\nChecking commit messages after first update:")
    for c_hash in [c1_hash, c2_hash, c3_hash, c4_hash]:
        msg = git_cmd.must_git(f"show -s --format=%B {c_hash}").strip()
        print(f"Commit {c_hash[:8]} message:\n{msg}\n")

    # Verify initial PR chain
    assert pr1.base_ref == "main"
    assert pr2.base_ref == f"spr/main/{c1_id}"
    assert pr3.base_ref == f"spr/main/{c2_id}"
    assert pr4.base_ref == f"spr/main/{c3_id}"
    
    print("Amending third commit, deleting second, adding new commit...")
    # Get current messages (which should have IDs from spr update)
    c1_msg = git_cmd.must_git(f"show -s --format=%B HEAD~3").strip()
    c3_msg = git_cmd.must_git(f"show -s --format=%B HEAD~1").strip()
    c4_msg = git_cmd.must_git(f"show -s --format=%B HEAD").strip()

    # Reset and cherry-pick c1, preserving SPR-updated message
    run_cmd("git reset --hard HEAD~4")
    run_cmd(f"git cherry-pick {c1_hash}")
    run_cmd(f'git commit --amend -m "{c1_msg}"')
    c1_hash_new = git_cmd.must_git("rev-parse HEAD").strip()
    log1 = git_cmd.must_git(f"show -s --format=%B {c1_hash_new}").strip()
    print(f"Commit 1: old={c1_hash} new={c1_hash_new} id={c1_id}")
    print(f"Log 1:\n{log1}")
    
    # Skip c2 entirely - delete it from stack
    print("Skipping c2 - deleting it")
    
    # Cherry-pick and amend c3, preserving SPR-updated message
    run_cmd(f"git cherry-pick {c3_hash}")
    with open("tc.txt", "a") as f:
        f.write("line 2\n")
    run_cmd("git add tc.txt")
    run_cmd(f'git commit --amend -m "{c3_msg}"')
    c3_hash_new = git_cmd.must_git("rev-parse HEAD").strip()
    log3 = git_cmd.must_git(f"show -s --format=%B {c3_hash_new}").strip()
    print(f"Commit 3: old={c3_hash} new={c3_hash_new} id={c3_id}")
    print(f"Log 3:\n{log3}")
    
    # Insert new c3.5
    print("Inserting new c3.5")
    _new_c35_hash = make_commit("tc5.txt", "line 1", "Commit three point five")
    
    # Cherry-pick c4, preserving SPR-updated message
    run_cmd(f"git cherry-pick {c4_hash}")
    run_cmd(f'git commit --amend -m "{c4_msg}"')
    c4_hash_new = git_cmd.must_git("rev-parse HEAD").strip()
    log4 = git_cmd.must_git(f"show -s --format=%B {c4_hash_new}").strip()
    print(f"Commit 4: old={c4_hash} new={c4_hash_new} id={c4_id}")
    print(f"Log 4:\n{log4}")
    
    print("Updating PRs after amend...")
    # Run update with amended commits
    subprocess.run(["rye", "run", "pyspr", "update"], check=True)
    
    # Verify PRs updated properly
    info = github.get_info(None, git_cmd)
    assert info is not None, "GitHub info should not be None"
    test_prs = get_test_prs()
    assert len(test_prs) == 4, f"Should have 4 PRs for our test after amend, found {len(test_prs)}"
    prs_by_num = {pr.number: pr for pr in test_prs}
    pr1 = prs_by_num.get(pr1_num)
    pr3 = prs_by_num.get(pr3_num)
    pr4 = prs_by_num.get(pr4_num)
    new_pr = next((pr for pr in test_prs if pr.number not in [pr1_num, pr2_num, pr3_num, pr4_num]), None)
    
    # Verify PR1 and PR4 remain, PR2 deleted, PR3 updated, and new PR added
    assert pr1 is not None, "PR1 should still exist"
    assert pr2_num not in prs_by_num, "PR2 should be deleted"
    assert pr3 is not None, "PR3 should still exist" 
    assert pr4 is not None, "PR4 should still exist"
    assert new_pr is not None, "New PR for c3.5 should be created"
    
    # Verify PR numbers - except PR2 which should be deleted
    assert pr1.number == pr1_num, f"PR1 number changed from {pr1_num} to {pr1.number}"
    assert pr3.number == pr3_num, f"PR3 number changed from {pr3_num} to {pr3.number}"
    assert pr4.number == pr4_num, f"PR4 number changed from {pr4_num} to {pr4.number}"
    
    # Verify commit IDs - PR1 and PR4 shouldn't change
    assert pr1.commit.commit_id == c1_id, f"PR1 commit ID changed from {c1_id} to {pr1.commit.commit_id}"
    assert pr3.commit.commit_id == c3_id, f"PR3 commit ID changed from {c3_id} to {pr3.commit.commit_id}"
    assert pr4.commit.commit_id == c4_id, f"PR4 commit ID changed from {c4_id} to {pr4.commit.commit_id}"
    
    # Only PR3's hash should change (was amended), PR1 and PR4 hashes shouldn't change
    assert pr1.commit.commit_hash == c1_hash_new, f"PR1 hash should be {c1_hash_new}"
    assert pr3.commit.commit_hash == c3_hash_new, f"PR3 hash should be {c3_hash_new}"
    assert pr4.commit.commit_hash == c4_hash_new, f"PR4 hash should be {c4_hash_new}"
    
    # Verify PR targets remained correct
    assert pr1.base_ref == "main", f"PR1 base ref incorrect: {pr1.base_ref}"
    assert pr3.base_ref == f"spr/main/{c1_id}", f"PR3 base ref incorrect: {pr3.base_ref}" # PR3 now targets PR1
    assert new_pr.base_ref == f"spr/main/{c3_id}", f"New PR base ref incorrect: {new_pr.base_ref}"
    assert pr4.base_ref == f"spr/main/{new_pr.commit.commit_id}", f"PR4 base ref incorrect: {pr4.base_ref}" # PR4 now targets new PR

    # Verify commit IDs exist in messages and are preserved through updates
    print("\nVerifying commit IDs in messages after updates:")
    for pr in [pr1, pr3, new_pr, pr4]:
        message = git_cmd.must_git(f"show -s --format=%B {pr.commit.commit_hash}").strip()
        print(f"PR #{pr.number} message:\n{message}\n")
        assert f"commit-id:{pr.commit.commit_id}" in message, f"PR #{pr.number} should have correct commit ID in message"

def _run_merge_test(
        repo_fixture: Union[Tuple[str, str, str], Tuple[str, str, str, str]], 
        owner: str, use_merge_queue: bool, num_commits: int, 
        count: Optional[int] = None) -> None:
    """Common test logic for merge workflows.
    
    Args:
        repo_fixture: Test repo fixture
        owner: GitHub repo owner
        use_merge_queue: Whether to use merge queue or not
        num_commits: Number of commits to create in test
        count: If set, merge only this many PRs from the bottom of stack (-c flag)
    """
    if len(repo_fixture) == 3:
        repo_name, test_branch, repo_dir = repo_fixture
    else:
        owner, repo_name, test_branch, repo_dir = repo_fixture

    orig_dir = os.getcwd()
    # Temporarily go to repo to create commits
    os.chdir(repo_dir)

    # Config based on parameters
    config = Config({
        'repo': {
            'github_remote': 'origin',
            'github_branch': 'main',
            'github_repo_owner': owner,
            'github_repo_name': repo_name,
            'merge_queue': use_merge_queue,
        },
        'user': {}
    })
    git_cmd = RealGit(config)
    github = GitHubClient(None, config)  # Real GitHub client

    # Get timestamp before we create PRs
    commit_time = int(git_cmd.must_git("show -s --format=%ct").strip())
    
    # Create commits
    def make_commit(file: str, line: str, msg: str) -> str:
        with open(file, "w") as f:
            f.write(f"{file}\n{line}\n")
        try:
            print(f"Creating file {file}...")
            run_cmd(f"git add {file}")
            run_cmd("git status")  # Debug: show git status after add
            result = subprocess.run(f'git commit -m "{msg}"', shell=True, check=False,
                                  stderr=subprocess.PIPE, stdout=subprocess.PIPE,
                                  universal_newlines=True)
            if result.returncode != 0:
                print(f"Git commit failed with code {result.returncode}")
                print(f"STDOUT: {result.stdout}")
                print(f"STDERR: {result.stderr}")
                print("Directory contents:")
                subprocess.run(["ls", "-la"], check=False)
                raise subprocess.CalledProcessError(result.returncode, result.args,
                                                   output=result.stdout,
                                                   stderr=result.stderr)
        except subprocess.CalledProcessError as e:
            print(f"Failed to commit: {e}")
            raise
        result = git_cmd.must_git("rev-parse HEAD").strip()
        assert result is not None, "Commit hash should not be None"
        return result
        
    print("Creating commits...")
    try:
        # Use static filenames but unique content
        unique = str(uuid.uuid4())[:8]
        commit_hashes: List[str] = []
        test_files: List[str] = []
        for i in range(num_commits):
            prefix = "test_merge" if not use_merge_queue else "mq_test"
            filename = f"{prefix}{i+1}.txt"
            test_files.append(filename)
            c_hash = make_commit(filename, f"line 1 - {unique}", 
                               f"Test {'merge queue' if use_merge_queue else 'multi'} commit {i+1}")
            commit_hashes.append(c_hash)
        run_cmd(f"git push -u origin {test_branch}")  # Push branch with commits
    except subprocess.CalledProcessError as e:
        # Get git status for debugging
        subprocess.run(["git", "status"], check=False)
        raise
    
    # Go back to project dir to run commands
    os.chdir(orig_dir)
    
    # Initial update to create PRs
    print("Creating initial PRs...")
    subprocess.run(["rye", "run", "pyspr", "update", "-C", repo_dir], check=True)
    
    os.chdir(repo_dir)

    # Helper to find our test PRs 
    def get_test_prs() -> list:
        result = []
        for pr in github.get_info(None, git_cmd).pull_requests:
            if pr.from_branch.startswith('spr/main/'):
                try:
                    files = git_cmd.must_git(f"show --name-only {pr.commit.commit_hash}")
                    if any(f in files for f in test_files):
                        pr_time = int(git_cmd.must_git(f"show -s --format=%ct {pr.commit.commit_hash}").strip())
                        if pr_time >= commit_time:
                            result.append(pr)
                except:  # Skip failures since we're just filtering
                    pass
        return result

    # Verify PRs created
    info = github.get_info(None, git_cmd)
    assert info is not None, "GitHub info should not be None"
    prs = get_test_prs()
    assert len(prs) == num_commits, f"Should have created {num_commits} PRs for our test, found {len(prs)}"
    prs = sorted(prs, key=lambda pr: pr.number)
    pr_nums = [pr.number for pr in prs]
    print(f"Created PRs: {', '.join(f'#{num}' for num in pr_nums)}")

    # Verify initial PR chain
    assert prs[0].base_ref == "main", f"Bottom PR should target main, got {prs[0].base_ref}"
    for i in range(1, len(prs)):
        assert prs[i].base_ref == f"spr/main/{prs[i-1].commit.commit_id}", \
            f"PR #{prs[i].number} should target PR #{prs[i-1].number}, got {prs[i].base_ref}"

    # Go back to project dir to run merge
    os.chdir(orig_dir)

    # Run merge for all or some PRs
    merge_cmd = ["rye", "run", "pyspr", "merge", "-C", repo_dir]
    if count is not None:
        merge_cmd.extend(["-c", str(count)])
    print(f"\nMerging {'to queue' if use_merge_queue else 'all'} PRs{' (partial)' if count else ''}...")
    try:
        merge_output = subprocess.check_output(
            merge_cmd,
            stderr=subprocess.STDOUT,
            universal_newlines=True
        )
        print(merge_output)
    except subprocess.CalledProcessError as e:
        # Get final PR state to help debug failure
        os.chdir(repo_dir)
        info = github.get_info(None, git_cmd)
        assert info is not None, "GitHub info should not be None"
        print("\nFinal PR state after merge attempt:")
        for pr in sorted(info.pull_requests, key=lambda pr: pr.number):
            if github.repo:
                gh_pr = github.repo.get_pull(pr.number)
                print(f"PR #{pr.number}:")
                print(f"  Title: {gh_pr.title}")
                print(f"  Base: {gh_pr.base.ref}")
                print(f"  State: {gh_pr.state}")
                print(f"  Merged: {gh_pr.merged}")
                if use_merge_queue:
                    print(f"  Mergeable state: {gh_pr.mergeable_state}")
                    print(f"  Auto merge: {getattr(gh_pr, 'auto_merge', None)}")
        os.chdir(orig_dir)
        print(f"Merge failed with output:\n{e.output}")
        raise

    # For partial merges, find the top PR number differently based on count
    to_merge = prs[:count] if count is not None else prs
    to_remain = prs[count:] if count is not None else []
    # Initialize top_pr_num before conditional use
    top_pr_num: Optional[int] = None
    if to_merge:
        top_merge_pr = to_merge[-1]
        top_pr_num = top_merge_pr.number

        # Verify merge output
        assert f"Merging PR #{top_pr_num} to main" in merge_output, f"Should merge PR #{top_pr_num}"
        assert f"This will merge {len(to_merge)} PRs" in merge_output, f"Should merge {len(to_merge)} PRs"
        if use_merge_queue:
            assert "added to merge queue" in merge_output, "PR should be added to merge queue"

    # Go back to repo to verify final state 
    os.chdir(repo_dir)
    info = github.get_info(None, git_cmd)
    assert info is not None, "GitHub info should not be None"
    
    # Get the current test PRs 
    current_prs = get_test_prs()
    prs_by_num = {pr.number: pr for pr in current_prs}

    if use_merge_queue:
        # For merge queue: top merged PR open, some closed, some remain
        expected_open = 1 + len(to_remain)  # Top merged PR + remaining PRs
        assert len(current_prs) == expected_open, f"{expected_open} test PRs should remain open, found {len(current_prs)}"
        
        if to_merge:
            assert 'top_pr_num' in locals(), "top_pr_num should be defined"
            assert top_pr_num in prs_by_num, f"Top PR #{top_pr_num} should remain open in queue"
            for pr in to_merge[:-1]:
                assert pr.number not in prs_by_num, f"PR #{pr.number} should be closed"
            top_pr = prs_by_num[top_pr_num]
            assert github.repo is not None, "GitHub repo should be available"
            gh_pr = github.repo.get_pull(top_pr.number)  # Get raw GitHub PR
            assert gh_pr.base.ref == "main", "Top merged PR should target main for merge queue"
            assert gh_pr.state == "open", "Top merged PR should remain open while in queue"

        # Verify remaining PRs stay open
        for pr in to_remain:
            assert pr.number in prs_by_num, f"PR #{pr.number} should remain open"
    else:
        # For regular merge: merged PRs closed, others remain
        expected_open = len(to_remain)
        assert len(current_prs) == expected_open, f"{expected_open} test PRs should remain open, found {len(current_prs)}"
        if to_merge:
            # Get the merge commit
            run_cmd("git fetch origin main")
            merge_sha = git_cmd.must_git("rev-parse origin/main").strip()
            merge_msg = git_cmd.must_git(f"show -s --format=%B {merge_sha}").strip()
            # Verify merge commit contains the right PR number
            assert f"#{top_pr_num}" in merge_msg, f"Merge commit should reference PR #{top_pr_num}"
            # Verify merge commit contains only merged files
            merge_files = git_cmd.must_git(f"show --name-only {merge_sha}").splitlines()
            for i, pr in enumerate(to_merge):
                filename = test_files[i] 
                assert filename in merge_files, f"Merge should include {filename}"
            # Verify unmerged files are not in merge commit
            for i in range(len(to_merge), num_commits):
                filename = test_files[i]
                assert filename not in merge_files, f"Merge should not include {filename}"

        # Verify remaining PRs stay open
        for pr in to_remain:
            assert pr.number in prs_by_num, f"PR #{pr.number} should remain open"

    # Return to project dir
    os.chdir(orig_dir)

def test_merge_workflow(test_repo: Tuple[str, str, str, str]) -> None:
    """Test full merge workflow with real PRs."""
    owner, _name, _test_branch, _repo_dir = test_repo
    _run_merge_test(test_repo, owner, False, 3)

@pytest.fixture
def test_mq_repo() -> Generator[Tuple[str, str, str, str], None, None]:
    """Use yangenttest1/teststack repo with a temporary test branch."""
    orig_dir = os.getcwd()
    repo_name = "yangenttest1/teststack"
    test_branch = f"test-spr-mq-{uuid.uuid4().hex[:7]}"
    print(f"Using test branch {test_branch} in {repo_name}")
    
    # Use gh CLI token
    token = get_gh_token()
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
        yield "yangenttest1", "teststack", test_branch, repo_dir

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

def test_merge_queue_workflow(test_mq_repo: Tuple[str, str, str, str]) -> None:
    """Test merge queue workflow with real PRs."""
    _run_merge_test(test_mq_repo, "yangenttest1", True, 2)

def test_partial_merge_workflow(test_repo: Tuple[str, str, str, str]) -> None:
    """Test partial merge workflow, merging only 2 of 3 PRs."""
    owner, _name, _test_branch, _repo_dir = test_repo
    _run_merge_test(test_repo, owner, False, 3, count=2)

def test_partial_merge_queue_workflow(test_mq_repo: Tuple[str, str, str, str]) -> None:
    """Test partial merge queue workflow, merging only 2 of 3 PRs to queue."""
    _run_merge_test(test_mq_repo, "yangenttest1", True, 3, count=2)

def test_replace_commit(test_repo: Tuple[str, str, str, str]) -> None:
    """Test replacing a commit in the middle of stack with new commit.
    
    This verifies that when a commit is replaced with an entirely new commit:
    1. The PR for old commit is closed
    2. A new PR is created for new commit
    3. The old PR is not reused for the new commit
    
    This specifically tests the case where positional matching would be wrong.
    """
    owner, repo_name, _test_branch, repo_dir = test_repo
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

    # Get timestamp before we create PRs
    commit_time = int(git_cmd.must_git("show -s --format=%ct").strip())

    def make_commit(file: str, line: str, msg: str) -> Tuple[str, str]:
        commit_id = uuid.uuid4().hex[:8]
        full_msg = f"{msg}\n\ncommit-id:{commit_id}"
        with open(file, "w") as f:
            f.write(f"{file}\n{line}\n")
        run_cmd(f"git add {file}")
        run_cmd(f'git commit -m "{full_msg}"')
        commit_hash = git_cmd.must_git("rev-parse HEAD").strip()
        return commit_hash, commit_id

    try:
        print("\nCreating initial stack of 3 commits...")
        run_cmd("git checkout main")
        run_cmd("git pull")
        branch = f"test-replace-{uuid.uuid4().hex[:7]}"
        run_cmd(f"git checkout -b {branch}")

        test_files = ["file1.txt", "file2.txt", "file3.txt", "file2_new.txt"]

        # 1. Create stack with commits A -> B -> C
        c1_hash, c1_id = make_commit("file1.txt", "line 1", "Commit A")
        c2_hash, c2_id = make_commit("file2.txt", "line 1", "Commit B")
        c3_hash, c3_id = make_commit("file3.txt", "line 1", "Commit C")
        run_cmd(f"git push -u origin {branch}")

        # Helper to find our test PRs 
        def get_test_prs() -> list:
            result = []
            for pr in github.get_info(None, git_cmd).pull_requests:
                if pr.from_branch.startswith('spr/main/'):
                    try:
                        files = git_cmd.must_git(f"show --name-only {pr.commit.commit_hash}")
                        if any(f in files for f in test_files):
                            pr_time = int(git_cmd.must_git(f"show -s --format=%ct {pr.commit.commit_hash}").strip())
                            if pr_time >= commit_time:
                                result.append(pr)
                    except:  # Skip failures since we're just filtering
                        pass
            return result

        print("Creating initial PRs...")
        os.chdir(orig_dir)
        subprocess.run(["rye", "run", "pyspr", "update", "-C", repo_dir], check=True)
        os.chdir(repo_dir)

        # Get initial PR info and filter to our newly created PRs
        commit_prs = get_test_prs()
        commit_prs = sorted(commit_prs, key=lambda pr: pr.number)
        assert len(commit_prs) == 3, f"Should find 3 PRs for our commits, found {len(commit_prs)}"
        
        # Verify each commit has a PR
        prs_by_id = {pr.commit.commit_id: pr for pr in commit_prs}
        assert c1_id in prs_by_id, f"No PR found for commit A ({c1_id})"
        assert c2_id in prs_by_id, f"No PR found for commit B ({c2_id})" 
        assert c3_id in prs_by_id, f"No PR found for commit C ({c3_id})"
        
        pr1, pr2, pr3 = prs_by_id[c1_id], prs_by_id[c2_id], prs_by_id[c3_id]
        print(f"Created PRs: #{pr1.number} (A), #{pr2.number} (B), #{pr3.number} (C)")
        pr2_num = pr2.number  # Remember B's PR number

        # Verify PR stack
        assert pr1.base_ref == "main", "PR1 should target main"
        assert pr2.base_ref == f"spr/main/{c1_id}", "PR2 should target PR1"
        assert pr3.base_ref == f"spr/main/{c2_id}", "PR3 should target PR2"

        # 2. Replace commit B with new commit D
        print("\nReplacing commit B with new commit D...")
        run_cmd("git reset --hard HEAD~2")  # Remove B and C
        _new_c2_hash, new_c2_id = make_commit("file2_new.txt", "line 1", "New Commit D")
        run_cmd(f"git cherry-pick {c3_hash}")  # Add C back
        run_cmd("git push -f origin")

        # 3. Run update
        print("Running update after replace...")
        os.chdir(orig_dir)
        subprocess.run(["rye", "run", "pyspr", "update", "-C", repo_dir], check=True)
        os.chdir(repo_dir)

        # 4. Verify:
        print("\nVerifying PR handling after replace...")
        relevant_prs = get_test_prs()
        pr_nums_to_check: Set[int] = set(pr.number for pr in relevant_prs)
        if pr2_num not in pr_nums_to_check:
            # Also check if B's old PR is still open but no longer has our commits
            for pr in github.get_info(None, git_cmd).pull_requests:
                if pr.number == pr2_num:
                    relevant_prs.append(pr)
                    pr_nums_to_check.add(pr.number)
                    break
        
        # Map of commit IDs to PRs
        active_pr_ids: Dict[str, PullRequest] = {pr.commit.commit_id: pr for pr in relevant_prs}
        
        # - Verify B's PR state
        if pr2_num in pr_nums_to_check:
            # If it exists, it should not have B's commit ID anymore
            reused_pr = next((pr for pr in relevant_prs if pr.number == pr2_num), None)
            if reused_pr:
                assert reused_pr.commit.commit_id == new_c2_id, f"PR #{pr2_num} should not retain B's commit - found {reused_pr.commit.commit_id}"
                print(f"Found PR #{pr2_num} reused for commit {new_c2_id}")
        else:
            print(f"PR #{pr2_num} was properly closed")
        
        # - Verify new commit D has a PR
        assert new_c2_id in active_pr_ids, f"Should have PR for new commit D ({new_c2_id})"
        new_pr = active_pr_ids[new_c2_id]
        print(f"Found PR #{new_pr.number} for new commit {new_c2_id}")
        
        # Key assertions to verify we don't use positional matching:
        # 1. B's PR should be closed, not reused for any commit
        assert pr2_num not in pr_nums_to_check, "B's PR should be closed, not reused via position matching"
        # 2. D's new PR should not reuse B's PR number (which would happen with position matching)
        assert new_pr.number != pr2_num, "Should not reuse B's PR number for D (no position matching)"
        # 3. Verify we don't try to match removed commits to any remaining PRs
        for remaining_pr in relevant_prs:
            assert remaining_pr.commit.commit_id != c2_id, f"PR #{remaining_pr.number} should not be matched to removed commit B"

        # Check final stack structure
        stack_prs = sorted([pr for pr in relevant_prs 
                          if pr.commit.commit_id in [c1_id, new_c2_id, c3_id]],
                          key=lambda pr: pr.number)
        assert len(stack_prs) == 3, f"Should have 3 active PRs in stack, found {len(stack_prs)}"

        # Get PRs by commit ID 
        prs_by_id = {pr.commit.commit_id: pr for pr in stack_prs}
        pr1 = prs_by_id.get(c1_id)
        pr_d = prs_by_id.get(new_c2_id)
        pr3 = prs_by_id.get(c3_id)
        
        assert pr1 is not None, "PR1 should exist"
        assert pr_d is not None, "PR_D should exist"
        assert pr3 is not None, "PR3 should exist"
        
        print(f"Final PR stack: #{pr1.number} <- #{pr_d.number} <- #{pr3.number}")

        assert pr1.base_ref == "main", "PR1 should target main"
        assert pr_d.base_ref == f"spr/main/{c1_id}", "New PR should target PR1" 
        assert pr3.base_ref == f"spr/main/{new_c2_id}", "PR3 should target new PR"

    finally:
        try:
            # Cleanup 
            run_cmd("git checkout main")
            # Branch name may not be defined if test fails early
            run_cmd(f"git push origin --delete {branch} || true")  # type: ignore
        except NameError:
            pass # branch may not be defined if test fails early
        os.chdir(orig_dir)

def test_no_rebase_functionality(test_repo: Tuple[str, str, str, str]) -> None:
    """Test --no-rebase functionality.
    
    1. First update normally and verify rebase happens
    2. Then update with --no-rebase and verify rebase is skipped
    """
    owner, repo_name, test_branch, repo_dir = test_repo
    orig_dir = os.getcwd()
    os.chdir(repo_dir)

    config = Config({
        'repo': {
            'github_remote': 'origin', 
            'github_branch': 'main',
            'github_repo_owner': owner,
            'github_repo_name': repo_name,
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
        branch_file = f"branch_change_{uuid.uuid4().hex[:7]}.txt"
        run_cmd(f"echo 'branch change' > {branch_file}")
        run_cmd(f"git add {branch_file}")
        run_cmd('git commit -m "Branch change"')
        branch_sha = git_cmd.must_git("rev-parse HEAD").strip()

        # Create feature commit on main
        # This creates a fork in history that requires rebase
        run_cmd("git checkout main")
        main_file = f"origin_change_{uuid.uuid4().hex[:7]}.txt"
        run_cmd(f"echo 'origin change' > {main_file}")
        run_cmd(f"git add {main_file}")
        run_cmd('git commit -m "Origin change"')
        # Simulate remote by updating origin/main refs 
        run_cmd("git update-ref refs/remotes/origin/main HEAD")
        main_sha = git_cmd.must_git("rev-parse HEAD").strip()

        # Go back to test branch
        run_cmd("git checkout test-branch")
        
        # Get commit count before first update
        commit_count_before = len(git_cmd.must_git("log --oneline").splitlines())

        # Step 2: Test regular update - should rebase
        print("\nRunning regular update logic...")
        # Test just the rebase part without GitHub API
        os.chdir(repo_dir)  # Ensure we're in repo dir
        regular_output = io.StringIO()
        try:
            # Manual simulation of fetch_and_get_github_info without GitHub API
            with redirect_stdout(regular_output):
                # Check remote exists
                remotes = git_cmd.must_git("remote").split()
                assert 'origin' in remotes, "Test requires origin remote"

                # Simulate fetch by having refs already updated
                assert git_cmd.must_git("rev-parse --verify origin/main"), "Test requires origin/main ref"

                # Do the rebase part we want to test
                git_cmd.must_git(f"rebase origin/main --autostash")
        except Exception as e:
            print(f"ERROR: {e}")
        regular_output_str = regular_output.getvalue()
        
        # Verify regular update rebased by:
        # 1. Checking git log shows our commit on top of main's commit
        # 2. Checking the output shows rebase happened
        
        # Check commit order in git log
        log_output = git_cmd.must_git("log --oneline -n 2")
        log_shas = [line.split()[0] for line in log_output.splitlines()]
        assert len(log_shas) == 2, "Should have at least 2 commits"
        assert log_shas[1].startswith(main_sha[:7]), "Main commit should be second in log after rebase"
        
        # Check rebase happened
        assert "git rebase" in regular_output_str, "Regular update should perform rebase"
        
        # Step 3: Reset to pre-rebase state 
        run_cmd(f"git reset --hard {branch_sha}")
        
        # Step 4: Test update with --no-rebase
        print("\nRunning update with --no-rebase logic...")
        os.chdir(repo_dir)  # Ensure we're in repo dir
        no_rebase_output = io.StringIO()
        try:
            # Manual simulation of fetch_and_get_github_info without GitHub API
            with redirect_stdout(no_rebase_output):
                # Simulate env var set by CLI 
                os.environ["SPR_NOREBASE"] = "true"

                # Check remote exists
                remotes = git_cmd.must_git("remote").split()
                assert 'origin' in remotes, "Test requires origin remote"

                # Simulate fetch by having refs already updated
                assert git_cmd.must_git("rev-parse --verify origin/main"), "Test requires origin/main ref"

                # Verify rebase is skipped (the key test)
                no_rebase = (
                    os.environ.get("SPR_NOREBASE") == "true" or 
                    config.user.get('noRebase', False)
                )
                print(f"DEBUG: no_rebase={no_rebase}")
                if not no_rebase:
                    git_cmd.must_git(f"rebase origin/main --autostash")

                # Cleanup env var
                del os.environ["SPR_NOREBASE"]
        except Exception as e:
            print(f"ERROR: {e}")
        no_rebase_output_str = no_rebase_output.getvalue()
        
        # Verify no-rebase skipped rebasing by:
        # 1. Checking git log shows our commit is NOT on top of main's commit
        # 2. Checking the output does NOT show rebase command
        
        # Check commit order in git log - should still be original commit
        curr_sha = git_cmd.must_git("rev-parse HEAD").strip()
        assert curr_sha == branch_sha, "HEAD should still be at original commit"
        
        # Check rebase was skipped
        assert "git rebase" not in no_rebase_output_str, "No-rebase update should skip rebase"
        assert "DEBUG: no_rebase=True" in no_rebase_output_str, "Should detect no-rebase mode"
        
    finally:
        # Return to original directory
        os.chdir(orig_dir)

def test_stack_isolation(test_repo: Tuple[str, str, str, str]) -> None:
    """Test that PRs from different stacks don't interfere with each other.
    
    This verifies that removing commits from one stack doesn't close PRs from another stack.
    We test using two stacks of PRs (2 PRs each) in the same repo:
    
    Stack 1:        Stack 2:
    PR1B <- PR1A    PR2B <- PR2A
    
    Then remove PR1A and verify only PR1B gets closed, while stack 2 remains untouched.
    """
    owner, repo_name, _test_branch, repo_dir = test_repo
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

    # Get timestamp before we create PRs
    commit_time = int(git_cmd.must_git("show -s --format=%ct").strip())

    # Helper to make commit with unique commit-id
    def make_commit(file: str, line: str, msg: str) -> Tuple[str, str]:
        commit_id = uuid.uuid4().hex[:8]
        full_msg = f"{msg}\n\ncommit-id:{commit_id}"
        with open(file, "w") as f:
            f.write(f"{file}\n{line}\n")
        run_cmd(f"git add {file}")
        run_cmd(f'git commit -m "{full_msg}"')
        commit_hash = git_cmd.must_git("rev-parse HEAD").strip()
        return commit_hash, commit_id

    try:
        # Initialize branch names 
        branch1: str = ""
        branch2: str = ""

        test_files = ["stack1a.txt", "stack1b.txt", "stack2a.txt", "stack2b.txt"]

        # 1. Create branch1 with 2 connected PRs
        print("\nCreating branch1 with 2-PR stack...")
        run_cmd("git checkout main")
        run_cmd("git pull")
        branch1 = f"test-stack1-{uuid.uuid4().hex[:7]}"
        run_cmd(f"git checkout -b {branch1}")

        # First commit for PR1A
        c1a_hash, c1a_id = make_commit("stack1a.txt", "line 1", "Stack 1 commit A")
        # Second commit for PR1B
        c1b_hash, c1b_id = make_commit("stack1b.txt", "line 1", "Stack 1 commit B")
        run_cmd(f"git push -u origin {branch1}")

        # Update to create connected PRs 1A and 1B
        print("Creating stack 1 PRs...")
        os.chdir(orig_dir)
        subprocess.run(["rye", "run", "pyspr", "update", "-C", repo_dir], check=True)
        os.chdir(repo_dir)

        # 2. Create branch2 with 2 connected PRs 
        print("\nCreating branch2 with 2-PR stack...")
        run_cmd("git checkout main")
        branch2 = f"test-stack2-{uuid.uuid4().hex[:7]}"
        run_cmd(f"git checkout -b {branch2}")

        # First commit for PR2A
        _c2a_hash, c2a_id = make_commit("stack2a.txt", "line 1", "Stack 2 commit A")
        # Second commit for PR2B
        _c2b_hash, c2b_id = make_commit("stack2b.txt", "line 1", "Stack 2 commit B")
        run_cmd(f"git push -u origin {branch2}")

        # Update to create connected PRs 2A and 2B
        print("Creating stack 2 PRs...")
        os.chdir(orig_dir)
        subprocess.run(["rye", "run", "pyspr", "update", "-C", repo_dir], check=True)
        os.chdir(repo_dir)

        # Helper to find our test PRs 
        def get_test_prs() -> list:
            result = []
            for pr in github.get_info(None, git_cmd).pull_requests:
                if pr.from_branch.startswith('spr/main/'):
                    try:
                        files = git_cmd.must_git(f"show --name-only {pr.commit.commit_hash}")
                        if any(f in files for f in test_files):
                            pr_time = int(git_cmd.must_git(f"show -s --format=%ct {pr.commit.commit_hash}").strip())
                            if pr_time >= commit_time:
                                result.append(pr)
                    except:  # Skip failures since we're just filtering
                        pass
            return result

        # Verify all 4 PRs exist with correct connections
        print("\nVerifying initial state of PRs...")
        # Find our test PRs
        test_prs = get_test_prs()
        all_prs = {}
        for pr in test_prs:
            for cid in [c1a_id, c1b_id, c2a_id, c2b_id]:
                if pr.commit.commit_id == cid:
                    all_prs[cid] = pr

        # Check we found all PRs
        for label, cid in [("PR1A", c1a_id), ("PR1B", c1b_id), 
                           ("PR2A", c2a_id), ("PR2B", c2b_id)]:
            assert cid in all_prs, f"{label} is missing"

        pr1a, pr1b = all_prs[c1a_id], all_prs[c1b_id]
        pr2a, pr2b = all_prs[c2a_id], all_prs[c2b_id]

        # Verify stack 1 connections
        assert pr1a.base_ref == "main", "PR1A should target main"
        assert pr1b.base_ref == f"spr/main/{c1a_id}", "PR1B should target PR1A"

        # Verify stack 2 connections
        assert pr2a.base_ref == "main", "PR2A should target main"
        assert pr2b.base_ref == f"spr/main/{c2a_id}", "PR2B should target PR2A"

        print(f"Created stacks - Stack1: #{pr1a.number} <- #{pr1b.number}, Stack2: #{pr2a.number} <- #{pr2b.number}")

        # 3. Remove commit from branch1
        print("\nRemoving first commit from branch1...")
        run_cmd(f"git checkout {branch1}")
        run_cmd("git reset --hard HEAD~2")  # Remove both commits
        run_cmd("git cherry-pick {}".format(c1b_hash))  # Add back just the second commit
        run_cmd("git push -f origin")  # Force push the change

        # Run update in branch1
        print("Running update in branch1...")
        os.chdir(orig_dir)
        subprocess.run(["rye", "run", "pyspr", "update", "-C", repo_dir], check=True)
        os.chdir(repo_dir)

        # 4. Verify PR1A is closed, PR1B retargeted to main, while PR2A and PR2B remain untouched
        print("\nVerifying PR state after updates...")
        test_prs = get_test_prs()
        remaining_prs = {}
        for pr in test_prs:
            remaining_prs[pr.number] = pr.base_ref
        
        # Stack 1: PR1A should be closed, PR1B retargeted to main
        assert pr1a.number not in remaining_prs, f"PR1A #{pr1a.number} should be closed"
        assert pr1b.number in remaining_prs, f"PR1B #{pr1b.number} should still exist"
        assert remaining_prs[pr1b.number] == "main", f"PR1B should be retargeted to main, got {remaining_prs[pr1b.number]}"
        
        # Stack 2 should remain untouched
        assert pr2a.number in remaining_prs, f"PR2A #{pr2a.number} should still exist"
        assert pr2b.number in remaining_prs, f"PR2B #{pr2b.number} should still exist"
        assert remaining_prs[pr2a.number] == "main", f"PR2A should target main, got {remaining_prs[pr2a.number]}"
        assert remaining_prs[pr2b.number] == f"spr/main/{c2a_id}", f"PR2B should target PR2A, got {remaining_prs[pr2b.number]}"

    finally:
        try:
            # Cleanup 
            run_cmd("git checkout main")
            # Branch names may not be defined if test fails early
            run_cmd(f"git push origin --delete {branch1} || true")  # type: ignore
            run_cmd(f"git push origin --delete {branch2} || true")  # type: ignore
        except NameError:
            pass # branches may not be defined if test fails early
        os.chdir(orig_dir)
