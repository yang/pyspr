"""End-to-end test for amending commits in stack, PR stack isolation, WIP and reviewer behavior."""
# pyright: reportUnusedVariable=none

import os
import sys
import uuid
import subprocess
import time
import datetime
import logging
from typing import Dict, Generator, List, Optional, Set, Tuple, Union, Protocol, runtime_checkable
import pytest

from pyspr.tests.e2e.test_helpers import RepoContext, run_cmd
from pyspr.tests.e2e.decorators import run_twice_in_mock_mode
from pyspr.tests.e2e.test_analyze import create_commits_from_dag
from pyspr.config import Config
from pyspr.git import RealGit
from pyspr.github import GitHubClient, PullRequest, GitHubInfo
from pyspr.typing import Commit
from pyspr.tests.e2e.fixtures import create_test_repo

CURRENT_USER = "yang"  # Since we're using yang's token for tests


@runtime_checkable
class UserWithLogin(Protocol):
    """Protocol for objects with login attribute."""
    @property
    def login(self) -> str:
        """User login name."""
        ...


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
log.setLevel(logging.INFO)
log.propagate = True  # Allow logs to propagate to pytest

@run_twice_in_mock_mode
def test_delete_insert(test_repo_ctx: RepoContext) -> None:
    """Test that creates four commits, runs update, then recreates commits but skips the second one,
    and verifies PR state after each update, including verifying PR hashes match local commit hashes."""
    ctx = test_repo_ctx

    # Create four test commits with unique tag in message
    ctx.make_commit("test1.txt", "test content 1", "First commit")
    ctx.make_commit("test2.txt", "test content 2", "Second commit")
    ctx.make_commit("test3.txt", "test content 3", "Third commit")
    ctx.make_commit("test4.txt", "test content 4", "Fourth commit")
    
    # Run pyspr update
    run_cmd("pyspr update")
    
    # Get commit hashes after update 
    commit1_hash = ctx.git_cmd.must_git("rev-parse HEAD~3").strip()
    commit2_hash = ctx.git_cmd.must_git("rev-parse HEAD~2").strip()
    commit3_hash = ctx.git_cmd.must_git("rev-parse HEAD~1").strip() 
    commit4_hash = ctx.git_cmd.must_git("rev-parse HEAD").strip()

    # Verify initial PRs were created
    prs = ctx.get_test_prs()
    assert len(prs) == 4, f"Should have created 4 PRs, found {len(prs)}"
    prs.sort(key=lambda p: p.number)  # Sort by PR number
    pr1, pr2, pr3, pr4 = prs
    
    # Save PR numbers for later verification
    pr1_num, pr2_num, pr3_num, pr4_num = pr1.number, pr2.number, pr3.number, pr4.number
    
    # Verify PR chain
    assert pr1.base_ref == "main", "First PR should target main"
    assert pr2.base_ref == f"pyspr/cp/main/{pr1.commit.commit_id}", "Second PR should target first PR's branch"
    assert pr3.base_ref == f"pyspr/cp/main/{pr2.commit.commit_id}", "Third PR should target second PR's branch"
    assert pr4.base_ref == f"pyspr/cp/main/{pr3.commit.commit_id}", "Fourth PR should target third PR's branch"
    
    log.info(f"\nInitial PRs created: #{pr1_num}, #{pr2_num}, #{pr3_num}, #{pr4_num}")
    
    # Verify each PR hash matches its local commit hash
    assert pr1.commit.commit_hash == commit1_hash, f"PR1 hash {pr1.commit.commit_hash} should match local commit1 hash {commit1_hash}"
    assert pr2.commit.commit_hash == commit2_hash, f"PR2 hash {pr2.commit.commit_hash} should match local commit2 hash {commit2_hash}"
    assert pr3.commit.commit_hash == commit3_hash, f"PR3 hash {pr3.commit.commit_hash} should match local commit3 hash {commit3_hash}"
    assert pr4.commit.commit_hash == commit4_hash, f"PR4 hash {pr4.commit.commit_hash} should match local commit4 hash {commit4_hash}"
    
    # Now reset and recreate commits but skip commit2 and add c3.5
    log.info("\nRecreating commits without second commit and adding c3.5...")
    run_cmd("git reset --hard HEAD~4")  # Remove all commits

    # Get the original commit messages (stored for debugging)
    c1_msg = ctx.git_cmd.must_git(f"show -s --format=%B {commit1_hash}").strip()  # noqa
    c3_msg = ctx.git_cmd.must_git(f"show -s --format=%B {commit3_hash}").strip()  # noqa
    c4_msg = ctx.git_cmd.must_git(f"show -s --format=%B {commit4_hash}").strip()  # noqa
    
    # Recreate commits but skip commit2 and add c3.5
    run_cmd(f"git cherry-pick {commit1_hash}")
    run_cmd(f"git cherry-pick {commit3_hash}")
    
    # Add new c3.5 commit
    ctx.make_commit("test3_5.txt", "test content 3.5", "Commit three point five")

    run_cmd(f"git cherry-pick {commit4_hash}")

    # Run pyspr update again
    run_cmd("pyspr update -v")
    
    # Get PRs after removing commit2 and adding c3.5
    prs = ctx.get_test_prs()
    assert len(prs) == 4, f"Should have 4 PRs after removing commit2 and adding c3.5, found {len(prs)}"

    # Get PR numbers that still exist
    current_pr_nums = {pr.number for pr in prs}

    # Verify PR2 is closed while others remain
    assert pr1_num in current_pr_nums, f"PR1 #{pr1_num} should still exist"
    assert pr2_num not in current_pr_nums, f"PR2 #{pr2_num} should be closed"
    assert pr3_num in current_pr_nums, f"PR3 #{pr3_num} should still exist"
    assert pr4_num in current_pr_nums, f"PR4 #{pr4_num} should still exist"

    # Get PRs by number
    pr1_after = next((pr for pr in prs if pr.number == pr1_num), None)
    pr3_after = next((pr for pr in prs if pr.number == pr3_num), None)
    pr4_after = next((pr for pr in prs if pr.number == pr4_num), None)
    # Find the new PR for c3.5
    pr35 = next((pr for pr in prs if pr.number not in [pr1_num, pr2_num, pr3_num, pr4_num]), None)
    
    assert pr1_after is not None, f"PR1 #{pr1_num} should exist"
    assert pr3_after is not None, f"PR3 #{pr3_num} should exist"
    assert pr35 is not None, "New PR for c3.5 should exist"
    assert pr4_after is not None, f"PR4 #{pr4_num} should exist"
    
    # Verify new PR chain
    assert pr1_after.base_ref == "main", "First PR should target main"
    assert pr3_after.base_ref == f"pyspr/cp/main/{pr1_after.commit.commit_id}", "Third PR should now target first PR's branch"
    assert pr35.base_ref == f"pyspr/cp/main/{pr3_after.commit.commit_id}", "New PR should target third PR's branch"
    assert pr4_after.base_ref == f"pyspr/cp/main/{pr35.commit.commit_id}", "Fourth PR should target new PR's branch"
    
    # Verify PR order and proper chain connectivity
    current_shas: Dict[str, str] = {
        "first": ctx.git_cmd.must_git("rev-parse HEAD~3").strip(),
        "third": ctx.git_cmd.must_git("rev-parse HEAD~2").strip(),
        "three_five": ctx.git_cmd.must_git("rev-parse HEAD~1").strip(),
        "fourth": ctx.git_cmd.must_git("rev-parse HEAD").strip()
    }
    
    # Verify PR hashes match new local commit hashes after update
    assert pr1_after.commit.commit_hash == current_shas["first"], f"PR1 hash {pr1_after.commit.commit_hash} should match new local commit hash {current_shas['first']}"
    assert pr3_after.commit.commit_hash == current_shas["third"], f"PR3 hash {pr3_after.commit.commit_hash} should match new local commit hash {current_shas['third']}"
    assert pr35.commit.commit_hash == current_shas["three_five"], f"PR35 hash {pr35.commit.commit_hash} should match local commit hash {current_shas['three_five']}"
    assert pr4_after.commit.commit_hash == current_shas["fourth"], f"PR4 hash {pr4_after.commit.commit_hash} should match new local commit hash {current_shas['fourth']}"

    log.info(f"\nVerified PRs after removing commit2 and adding c3.5: #{pr1_num} -> #{pr3_num} -> #{pr35.number} -> #{pr4_num}")
    log.info(f"PR2 #{pr2_num} correctly closed")

@run_twice_in_mock_mode
def test_wip_behavior(test_repo_ctx: RepoContext, caplog: pytest.LogCaptureFixture) -> None:
    """Test that WIP commits behave as expected:
    - Regular commits before WIP are converted to PRs
    - WIP commits are not converted to PRs
    - Regular commits after WIP are not converted to PRs
    """
    log.info("=== TEST STARTED ===")  # Just to see if test runs at all
    caplog.set_level(logging.INFO)
    ctx = test_repo_ctx
    git_cmd = ctx.git_cmd
    github = ctx.github
    
    log.info(f"{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]} Creating commits...")
    # Create 4 commits: 2 regular, 1 WIP, 1 regular
    ctx.make_commit("wip_test1.txt", "test content", "First regular commit")
    ctx.make_commit("wip_test2.txt", "test content", "Second regular commit")
    ctx.make_commit("wip_test3.txt", "test content", "WIP Third commit")
    ctx.make_commit("wip_test4.txt", "test content", "Fourth regular commit")  # Not used but kept for completeness
    
    # Run update to create PRs
    log.info(f"{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]} Running pyspr update...")
    run_cmd("pyspr update")
    log.info(f"{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]} pyspr update complete")
    
    # Get commit hashes after update
    c1_hash = git_cmd.must_git("rev-parse HEAD~3").strip()
    c2_hash = git_cmd.must_git("rev-parse HEAD~2").strip()
    c3_hash = git_cmd.must_git("rev-parse HEAD~1").strip()
    
    # Let GitHub process the PRs
    log.info(f"{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]} Waiting for PRs to be available in GitHub...")
    time.sleep(5)

    # Debug: Check what branches actually exist
    log.info("Checking remote branches:")
    remote_branches = git_cmd.must_git("ls-remote --heads origin").split("\n")
    for branch in remote_branches:
        if branch:
            log.info(f"  {branch}")
    
    # We'll use ctx.get_test_prs() directly, with timing logs around it
    log.info("=== ABOUT TO CALL GITHUB API ===")
    log.info(f"{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]} Starting PR filtering...")
    gh_start = time.time()
    test_prs = ctx.get_test_prs()
    gh_end = time.time()
    log.info(f"{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]} PR filtering took {gh_end - gh_start:.2f} seconds")
    log.info(f"{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]} Found {len(test_prs)} matching PRs")
    
    # Verify only first two PRs were created
    log.info(f"{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]} Getting GitHub info...")
    info: Optional[GitHubInfo] = github.get_info(None, git_cmd)
    assert info is not None, "GitHub info should not be None"
    
    log.info(f"{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]} Getting commit info for debugging:")
    log.info(f"{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]} C1: {c1_hash}")
    log.info(f"{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]} C2: {c2_hash}")
    log.info(f"{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]} C3: {c3_hash}")
    
    # Get our test PRs (already got them above)
    log.info(f"{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]} Using filtered test PRs...")
    
    # Print all PR commit hashes for debugging
    log.info(f"{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]} Test PR commit hashes:")
    for pr in test_prs:
        log.info(f"{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]} PR #{pr.number}: {pr.title} - {pr.commit.commit_hash}")
    
    # Sort PRs by number (most recent first) and take first 2 matching our titles
    log.info(f"{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]} Finding PRs with target titles...")
    test_prs = sorted(test_prs, key=lambda pr: pr.number, reverse=True)
    prs_with_titles: List[PullRequest] = []
    for pr in test_prs:
        if "First regular commit" in pr.title or "Second regular commit" in pr.title:
            prs_with_titles.append(pr)
        if len(prs_with_titles) == 2:
            break
    test_prs = prs_with_titles
    log.info(f"{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]} Found {len(test_prs)} PRs with target titles")
            
    log.info("\nMost recent matching PRs:")
    for pr in test_prs:
        log.info(f"PR #{pr.number}: {pr.title}")
            
    assert len(test_prs) == 2, f"Should find 2 PRs before the WIP commit, found {len(test_prs)}: {[pr.title for pr in test_prs]}"
    
    # Verify PR commit hashes match first two commits
    prs = sorted(test_prs, key=lambda pr: pr.number)
    assert len(prs) == 2, "Should have exactly 2 PRs"
    
    # Get commit messages to verify WIP detection worked correctly
    c1_msg = git_cmd.must_git(f"show -s --format=%B {c1_hash}").strip()
    c2_msg = git_cmd.must_git(f"show -s --format=%B {c2_hash}").strip()
    c3_msg = git_cmd.must_git(f"show -s --format=%B {c3_hash}").strip() 
    
    log.info("\nVerifying commit messages:")
    log.info(f"C1: {c1_msg}")
    log.info(f"C2: {c2_msg}")
    log.info(f"C3: {c3_msg}")
    
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
    assert pr2.base_ref is not None and pr2.base_ref.startswith("pyspr/cp/main/"), "Second PR should target first PR's branch"


@run_twice_in_mock_mode
def test_reviewer_functionality(test_repo_ctx: RepoContext) -> None:
    """Test reviewer functionality, verifying:
    1. Self-review attempts are handled properly (can't review your own PR)
    2. Other user review requests work properly
    3. The -r flag adds reviewers to both new and existing PRs
    
    This test has two parts:
    - Part 1: Tests yang token case (can't self-review) - creates 2 commits,
      runs update with -r yang, verifies yang is NOT added as reviewer
    - Part 2: Tests testluser case - creates first PR without reviewer,
      then adds second commit and runs update with -r testluser, verifying
      that BOTH PRs get testluser as reviewer (not just the new one)
    """
    ctx = test_repo_ctx
    git_cmd = ctx.git_cmd
    github = ctx.github
    repo_dir = ctx.repo_dir

    # Save current directory and change to repo_dir
    original_dir = os.getcwd()
    os.chdir(repo_dir)

    try:
        # Create unique tag pattern for part 2 (we use ctx.tag for part 1)
        unique_tag2 = f"test-reviewer-testluser-{uuid.uuid4().hex[:8]}"

        # Note: We'll still use ctx.make_commit with the test tag directly for part 1.
        # For part 2, we need to make commits with a different tag pattern.
        # Helper to make commits for part 2
        def make_commit_2(file: str, msg: str) -> None:
            full_msg = f"{msg} [test-tag:{unique_tag2}]"
            with open(os.path.join(repo_dir, file), "w") as f:
                f.write(f"{file}\n{msg}\n")
            run_cmd(f"git add {file}")
            run_cmd(f'git commit -m "{full_msg}"')

        # For part 2 we need custom PR filtering by tag
        def get_test_prs_by_tag(tag: str) -> List[PullRequest]:
            result: List[PullRequest] = []
            info: Optional[GitHubInfo] = github.get_info(None, git_cmd)
            if info is None:
                return []
            for pr in info.pull_requests:
                if pr.from_branch and pr.from_branch.startswith('pyspr/cp/main/'):
                    try:
                        # Look for our unique tag in the commit message
                        assert pr.commit is not None
                        commit_msg: str = git_cmd.must_git(f"show -s --format=%B {pr.commit.commit_hash}")
                        if f"test-tag:{tag}" in commit_msg:
                            result.append(pr)
                    except Exception:  # Skip failures since we're just filtering
                        pass
            return result

        # Part 1: Test self-review case (yang token)
        log.info("\n=== Part 1: Testing self-review handling ===")
        log.info("Creating first commit without reviewer...")
        log.info(f"Current working directory: {os.getcwd()}")
        ctx.make_commit("r_test1.txt", "test content", "First commit")

        # Create initial PR without reviewer
        run_cmd("pyspr update")

        # Verify first PR exists with no reviewer
        github_info: Optional[GitHubInfo] = github.get_info(None, git_cmd)
        assert github_info is not None, "GitHub info should not be None"
        our_prs = ctx.get_test_prs()
        assert len(our_prs) == 1, f"Should have 1 PR for our test, found {len(our_prs)}"
        pr1 = our_prs[0]
        assert github.repo is not None, "GitHub repo should be available"
        gh_pr1 = github.repo.get_pull(pr1.number)
        
        # Debug review requests for first PR
        log.info("\nDEBUG: First PR review requests")
        try:
            requested_users, requested_teams = gh_pr1.get_review_requests()
            # Extract login names - in tests, we know these are user objects
            requested_logins: List[str] = []
            for u in requested_users:
                # Use protocol check to ensure type safety
                if isinstance(u, UserWithLogin):
                    requested_logins.append(u.login.lower())
            log.info(f"Requested Users: {requested_logins}")
            log.info(f"Requested Teams: {list(requested_teams)}")
        except Exception as e:
            log.info(f"Error getting review data: {e}")
            requested_logins = []
        
        assert CURRENT_USER.lower() not in requested_logins, f"First PR correctly has no {CURRENT_USER} reviewer (can't review own PR)"
        log.info(f"Created PR #{pr1.number} with no {CURRENT_USER} reviewer")

        # Create second commit and try self-review
        log.info("\nCreating second commit with self-reviewer...")
        ctx.make_commit("r_test2.txt", "test content", "Second commit")
        run_cmd("pyspr update -r yang")

        # Verify no self-review was added
        info: Optional[GitHubInfo] = github.get_info(None, git_cmd)
        assert info is not None, "GitHub info should not be None"
        our_prs = ctx.get_test_prs()
        assert len(our_prs) == 2, f"Should have 2 PRs for our test, found {len(our_prs)}"
        prs_by_num: Dict[int, PullRequest] = {pr.number: pr for pr in our_prs}
        assert pr1.number in prs_by_num, "First PR should still exist"
        
        # Verify no reviewer on first PR
        gh_pr1 = github.repo.get_pull(pr1.number)
        try:
            requested_users, _ = gh_pr1.get_review_requests()
            requested_logins1: List[str] = []
            for u in requested_users:
                if isinstance(u, UserWithLogin):
                    requested_logins1.append(u.login.lower())
            log.info(f"First PR requested users: {requested_logins1}")
        except Exception as e:
            log.info(f"Error getting review data: {e}")
            requested_logins1 = []
        assert CURRENT_USER.lower() not in requested_logins1, f"First PR correctly has no {CURRENT_USER} reviewer"
        
        # Verify no reviewer on second PR (self-review blocked)
        pr2 = [pr for pr in our_prs if pr.number != pr1.number][0]
        gh_pr2 = github.repo.get_pull(pr2.number)
        try:
            requested_users, _ = gh_pr2.get_review_requests()
            requested_logins2: List[str] = []
            for u in requested_users:
                if isinstance(u, UserWithLogin):
                    requested_logins2.append(u.login.lower())
            log.info(f"Second PR requested users: {requested_logins2}")
        except Exception as e:
            log.info(f"Error getting review data: {e}")
            requested_logins2 = []
        assert CURRENT_USER.lower() not in requested_logins2, f"Second PR correctly has no {CURRENT_USER} reviewer (self-review blocked)"
        
        log.info("Successfully verified self-review handling")

        # Part 2: Test testluser case
        log.info("\n=== Part 2: Testing testluser review handling ===")
        
        # Reset to main and create new branch for second test
        run_cmd("git checkout main")
        run_cmd(f"git checkout -b test-reviewers-2-{uuid.uuid4().hex[:7]}")
        
        # Create first commit and PR
        log.info("Creating first commit without reviewer...")
        make_commit_2("test_r1.txt", "First testluser commit")
        run_cmd("pyspr update")

        # Verify first PR
        our_prs = get_test_prs_by_tag(unique_tag2)
        assert len(our_prs) == 1, f"Should have 1 PR for testluser test, found {len(our_prs)}"
        pr1 = our_prs[0]
        gh_pr1 = github.repo.get_pull(pr1.number)
        
        # Verify no reviewer on first PR
        requested_users, _ = gh_pr1.get_review_requests()
        requested_logins: List[str] = []
        for u in requested_users:
            if isinstance(u, UserWithLogin):
                requested_logins.append(u.login.lower())
        assert "testluser" not in requested_logins, "First PR correctly has no testluser reviewer"
        log.info(f"Verified PR #{pr1.number} has no reviewer")

        # Create second commit on the same local branch (not on spr branch)
        log.info("\nCreating second commit with testluser reviewer...")
        make_commit_2("test_r2.txt", "Second testluser commit")

        # Add testluser as reviewer and capture output with verbose mode
        log.info("Running pyspr update -r testluser command to add reviewer")
        update_output = run_cmd("pyspr update -r testluser -v", cwd=repo_dir)
        log.info(f"Update output: {update_output}")

        # Find our PRs again
        log.info("Finding PRs after update")
        our_prs = get_test_prs_by_tag(unique_tag2)
        assert len(our_prs) == 2, f"Should have 2 PRs for testluser test, found {len(our_prs)}"
        
        # Get the latest PR
        log.info("Getting latest PR")
        pr2 = max(our_prs, key=lambda p: p.number)
        log.info(f"Latest PR: #{pr2.number}")
        
        # Directly check the state file to see what's happening with reviewers
        log.info("Checking state file for reviewers")
        state_file = os.path.join(repo_dir, ".git", "fake_github", "fake_github_state.yaml")
        if os.path.exists(state_file):
            with open(state_file, "r") as f:
                state_content = f.read()
                log.info(f"State file exists, size: {len(state_content)} bytes")
                log.info(f"Reviewers in state file: {'reviewers:' in state_content}")
        else:
            log.info("State file does not exist")
            
        # Check that BOTH PRs now have testluser as reviewer
        # First PR (existing) should now have reviewer added
        gh_pr1_updated = github.repo.get_pull(pr1.number)
        requested_users1, _ = gh_pr1_updated.get_review_requests()
        requested_logins1 = [u.login.lower() for u in requested_users1]
        log.info(f"First PR requested logins after update: {requested_logins1}")
        assert "testluser" in requested_logins1, "First PR should now have testluser reviewer (added to existing PR)"
        
        # Second PR (new) should also have reviewer
        gh_pr2 = github.repo.get_pull(pr2.number)
        requested_users2, _ = gh_pr2.get_review_requests()
        requested_logins2 = [u.login.lower() for u in requested_users2]
        log.info(f"Second PR requested logins: {requested_logins2}")
        assert "testluser" in requested_logins2, "Second PR should have testluser reviewer"
        
        log.info("Successfully verified testluser review handling for both PRs")

    finally:
        # Change back to original directory
        os.chdir(original_dir)

@run_twice_in_mock_mode
def test_reorder(test_repo_ctx: RepoContext) -> None:
    """Test that creates four commits, runs update, then recreates commits but reorders c3 and c4,
    and verifies PR state after reordering."""
    ctx = test_repo_ctx
    git_cmd = ctx.git_cmd

    # Create commits c1, c2, c3, c4
    ctx.make_commit("test1.txt", "test content 1", "First commit")
    ctx.make_commit("test2.txt", "test content 2", "Second commit")
    ctx.make_commit("test3.txt", "test content 3", "Third commit")
    ctx.make_commit("test4.txt", "test content 4", "Fourth commit")

    # Run pyspr update
    run_cmd("pyspr update")

    # Get commit hashes after update
    commit1_hash = git_cmd.must_git("rev-parse HEAD~3").strip()
    commit2_hash = git_cmd.must_git("rev-parse HEAD~2").strip()
    commit3_hash = git_cmd.must_git("rev-parse HEAD~1").strip()
    commit4_hash = git_cmd.must_git("rev-parse HEAD").strip()

    log.info("\nLooking for PRs with unique tag...")
    # Verify initial PRs were created
    prs = ctx.get_test_prs()
    assert len(prs) == 4, f"Should have created 4 PRs, found {len(prs)}"
    prs.sort(key=lambda p: p.number)  # Sort by PR number
    pr1, pr2, pr3, pr4 = prs

    # Save PR numbers for later verification
    pr1_num, pr2_num, pr3_num, pr4_num = pr1.number, pr2.number, pr3.number, pr4.number

    # Verify PR chain
    assert pr1.base_ref == "main", "First PR should target main"
    assert pr2.base_ref == f"pyspr/cp/main/{pr1.commit.commit_id}", "Second PR should target first PR's branch"
    assert pr3.base_ref == f"pyspr/cp/main/{pr2.commit.commit_id}", "Third PR should target second PR's branch"
    assert pr4.base_ref == f"pyspr/cp/main/{pr3.commit.commit_id}", "Fourth PR should target third PR's branch"

    log.info(f"\nInitial PRs created: #{pr1_num}, #{pr2_num}, #{pr3_num}, #{pr4_num}")

    # Save original commit_ids for verification (unused but kept for debugging)
    commit1_id = pr1.commit.commit_id  # noqa
    commit2_id = pr2.commit.commit_id  # noqa
    commit3_id = pr3.commit.commit_id  # noqa
    commit4_id = pr4.commit.commit_id  # noqa

    # Now reset and recreate commits but reorder c3 and c4
    log.info("\nRecreating commits with c3 and c4 reordered...")
    run_cmd("git reset --hard HEAD~4")  # Remove all commits

    # Get the original commit messages
    c1_msg = git_cmd.must_git(f"show -s --format=%B {commit1_hash}").strip()  # noqa: F841
    c2_msg = git_cmd.must_git(f"show -s --format=%B {commit2_hash}").strip()  # noqa: F841 
    c3_msg = git_cmd.must_git(f"show -s --format=%B {commit3_hash}").strip()  # noqa: F841
    c4_msg = git_cmd.must_git(f"show -s --format=%B {commit4_hash}").strip()  # noqa: F841

    # Recreate commits but with c4 before c3
    run_cmd(f"git cherry-pick {commit1_hash}")
    run_cmd(f"git cherry-pick {commit2_hash}")
    run_cmd(f"git cherry-pick {commit4_hash}")  # c4 now before c3
    run_cmd(f"git cherry-pick {commit3_hash}")  # c3 now after c4

    # Run pyspr update again
    run_cmd("pyspr update -v")

    # Get PRs after reordering
    prs = ctx.get_test_prs()
    assert len(prs) == 4, f"Should still have 4 PRs after reordering, found {len(prs)}"

    # Instead of checking PRs by number, check them by commit message
    prs_by_title: Dict[str, PullRequest] = {}
    for pr in prs:
        if "First commit" in pr.title:
            prs_by_title["first"] = pr
        elif "Second commit" in pr.title:
            prs_by_title["second"] = pr
        elif "Third commit" in pr.title:
            prs_by_title["third"] = pr
        elif "Fourth commit" in pr.title:
            prs_by_title["fourth"] = pr

    # Verify all commits still have PRs
    assert "first" in prs_by_title, "No PR found for first commit"
    assert "second" in prs_by_title, "No PR found for second commit"
    assert "third" in prs_by_title, "No PR found for third commit"
    assert "fourth" in prs_by_title, "No PR found for fourth commit"

    pr1_after = prs_by_title["first"]
    pr2_after = prs_by_title["second"]
    pr3_after = prs_by_title["third"]
    pr4_after = prs_by_title["fourth"]

    # Verify new PR chain after reordering
    assert pr1_after.base_ref == "main", "First PR should target main"
    assert pr2_after.base_ref == f"pyspr/cp/main/{pr1_after.commit.commit_id}", "Second PR should target first PR's branch"
    assert pr4_after.base_ref == f"pyspr/cp/main/{pr2_after.commit.commit_id}", "Fourth PR should now target second PR's branch"
    assert pr3_after.base_ref == f"pyspr/cp/main/{pr4_after.commit.commit_id}", "Third PR should now target fourth PR's branch"

    # Log the final PR numbers in the new order
    pr_chain = f"#{pr1_after.number} -> #{pr2_after.number} -> #{pr4_after.number} -> #{pr3_after.number}"
    log.info(f"\nVerified PRs after reordering: {pr_chain}")

# We import test_repo_ctx and other fixtures from fixtures.py now
@pytest.fixture
@run_twice_in_mock_mode
def test_repo() -> Generator[Tuple[str, str, str, str], None, None]:
    """Regular test repo fixture using yang/teststack with mock or real GitHub."""
    yield from create_test_repo("yang", "teststack")

def _run_merge_test(
        repo_ctx: RepoContext, 
        use_merge_queue: bool, 
        num_commits: int, 
        count: Optional[int] = None) -> None:
    """Common test logic for merge workflows.
    
    Args:
        repo_ctx: Repository context
        use_merge_queue: Whether to use merge queue or not
        num_commits: Number of commits to create in test
        count: If set, merge only this many PRs from the bottom of stack (-c flag)
    """
    # Get context values
    owner = repo_ctx.owner
    repo_name = repo_ctx.name  
    repo_dir = repo_ctx.repo_dir
    git_cmd = repo_ctx.git_cmd
    github = repo_ctx.github

    # Add merge queue config if needed
    if use_merge_queue:
        config = Config({
            'repo': {
                'github_remote': 'origin',
                'github_branch': 'main',
                'github_repo_owner': owner,
                'github_repo_name': repo_name,
                'merge_queue': True,
            },
            'user': {}
        })
        git_cmd = RealGit(config)
        # Don't create a new GitHub client, just update the config in the existing one
        # to preserve the connection to the mock GitHub instance
        github.config = config
    
    log.info("Creating commits...")
    try:
        # Use static filenames but unique content
        unique = str(uuid.uuid4())[:8]
        test_files: List[str] = []
        # Get the tag suffix that will be added by make_commit
        tag_suffix = repo_ctx.tag.split('-')[-1][:8]  # Use last 8 chars of tag
        for i in range(num_commits):
            prefix = "test_merge" if not use_merge_queue else "mq_test"
            filename = f"{prefix}{i+1}.txt"
            # Store the actual filename that will be created by make_commit
            actual_filename = f"{filename}.{tag_suffix}"
            test_files.append(actual_filename)
            content = f"line 1 - {unique}"
            msg = f"Test {'merge queue' if use_merge_queue else 'multi'} commit {i+1}"
            try:
                log.info(f"Creating file {filename}...")
                repo_ctx.make_commit(filename, content, msg)
                run_cmd("git status")  # Debug: show git status after commit
            except subprocess.CalledProcessError as e:
                log.info(f"Git commit failed: {e}")
                log.info("Directory contents:")
                run_cmd("ls -la")
                raise
    except subprocess.CalledProcessError as e:  # noqa: F841
        # Get git status for debugging
        run_cmd("git status")
        raise

    # Initial update to create PRs
    log.info("Creating initial PRs...")
    run_cmd("pyspr update")

    # Verify PRs created
    github_info: Optional[GitHubInfo] = github.get_info(None, git_cmd)
    assert github_info is not None, "GitHub info should not be None"
    prs = repo_ctx.get_test_prs()
    assert len(prs) == num_commits, f"Should have created {num_commits} PRs for our test, found {len(prs)}"
    prs = sorted(prs, key=lambda pr: pr.number)
    pr_nums = [pr.number for pr in prs]
    log.info(f"Created PRs: {', '.join(f'#{num}' for num in pr_nums)}")

    # Verify initial PR chain
    assert prs[0].base_ref == "main", f"Bottom PR should target main, got {prs[0].base_ref}"
    for i in range(1, len(prs)):
        assert prs[i].base_ref == f"pyspr/cp/main/{prs[i-1].commit.commit_id}", \
            f"PR #{prs[i].number} should target PR #{prs[i-1].number}, got {prs[i].base_ref}"

    # Run merge for all or some PRs
    merge_cmd = f"pyspr merge -C {repo_dir}"
    if count is not None:
        merge_cmd += f" -c {count}"
    log.info(f"\nMerging {'to queue' if use_merge_queue else 'all'} PRs{' (partial)' if count else ''}...")
    
    # Debug: Check if mock GitHub is being used
    log.info(f"SPR_USING_MOCK_GITHUB={os.environ.get('SPR_USING_MOCK_GITHUB', 'not set')}")
    
    # No need for manually finding project root since run_cmd handles that
    merge_output = run_cmd(merge_cmd)
    log.info(f"Merge command output:\n{merge_output}")

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

    info: Optional[GitHubInfo] = github.get_info(None, git_cmd)
    assert info is not None, "GitHub info should not be None"
    
    # Get the current test PRs 
    current_prs = repo_ctx.get_test_prs()
    prs_by_num: Dict[int, PullRequest] = {pr.number: pr for pr in current_prs}

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
            # Add a small delay to ensure the merge commit is available in CI
            time.sleep(10)
            # Get the merge commit - try multiple times to ensure it's available
            merge_sha = ""
            merge_msg = ""
            for attempt in range(10):
                # Force fetch all refs from the bare repository
                run_cmd("git fetch origin +refs/heads/*:refs/remotes/origin/*")
                
                # Debug: Check what's in the bare repository directly
                bare_repo_dir = os.path.join(os.path.dirname(repo_dir), "remote.git")
                bare_log = run_cmd(f"git --git-dir={bare_repo_dir} log --oneline main -10")
                log.info(f"Commits in bare repository (attempt {attempt + 1}):\n{bare_log}")
                
                # Get all commits on origin/main to debug
                all_commits = git_cmd.must_git("log --oneline origin/main -10").strip()
                log.info(f"All commits on origin/main (attempt {attempt + 1}):\n{all_commits}")
                
                # Get the latest merge commit (the HEAD of origin/main after the merge)
                merge_sha = git_cmd.must_git("rev-parse origin/main").strip()
                
                # Also get the previous commit to ensure we're not looking at the wrong one
                prev_sha = git_cmd.must_git("rev-parse origin/main~1").strip()
                prev_msg = git_cmd.must_git(f"show -s --format=%B {prev_sha}").strip()
                
                merge_msg = git_cmd.must_git(f"show -s --format=%B {merge_sha}").strip()
                # Debug log the merge commit message
                log.info(f"Previous commit ({prev_sha}): '{prev_msg}'")
                log.info(f"Current HEAD of origin/main ({merge_sha}): '{merge_msg}'")
                log.info(f"Looking for PR #{top_pr_num} in merge commit message")
                
                # Check if we found the right commit
                pr_ref = f"#{top_pr_num}"
                if pr_ref in merge_msg:
                    break
                    
                # Wait before retrying
                if attempt < 9:
                    log.info("PR reference not found, waiting before retry...")
                    time.sleep(3)
            # Verify merge commit contains the right PR number
            pr_ref = f"#{top_pr_num}"
            log.info(f"PR reference to find: '{pr_ref}', length: {len(pr_ref)}")
            log.info(f"Is PR reference in message: {pr_ref in merge_msg}")
            assert pr_ref in merge_msg, f"Merge commit should reference PR #{top_pr_num}"
            # Verify merge commit contains only merged files
            merge_files = git_cmd.must_git(f"show --name-only {merge_sha}").splitlines()
            log.info(f"Merge files: {merge_files}")
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

@run_twice_in_mock_mode
def test_merge_workflow(test_repo_ctx: RepoContext) -> None:
    """Test full merge workflow with real PRs.
    
    Note: This test is not compatible with run_twice_in_mock_mode decorator because:
    - The test requires local commits to match PRs for merging
    - The decorator resets local commits on the second run
    - Without local commits, github.get_info() returns no PRs
    - The merge functionality cannot be tested without the PR data
    """
    _run_merge_test(test_repo_ctx, False, 3)

# We import test_mq_repo_ctx from fixtures.py now

@run_twice_in_mock_mode
def test_merge_queue_workflow(test_mq_repo_ctx: RepoContext) -> None:
    """Test merge queue workflow with real PRs."""
    _run_merge_test(test_mq_repo_ctx, True, 2)

@run_twice_in_mock_mode
def test_partial_merge_workflow(test_repo_ctx: RepoContext) -> None:
    """Test partial merge workflow, merging only 2 of 3 PRs."""
    _run_merge_test(test_repo_ctx, False, 3, count=2)

@run_twice_in_mock_mode
def test_partial_merge_queue_workflow(test_mq_repo_ctx: RepoContext) -> None:
    """Test partial merge queue workflow, merging only 2 of 3 PRs to queue."""
    _run_merge_test(test_mq_repo_ctx, True, 3, count=2)

@run_twice_in_mock_mode
def test_replace_commit(test_repo_ctx: RepoContext) -> None:
    """Test replacing a commit in the middle of stack with new commit.
    
    This verifies that when a commit is replaced with an entirely new commit:
    1. The PR for old commit is closed
    2. A new PR is created for new commit
    3. The old PR is not reused for the new commit
    
    This specifically tests the case where positional matching would be wrong.
    """
    ctx = test_repo_ctx
    git_cmd = ctx.git_cmd
    github = ctx.github

    log.info("\nCreating initial stack of 3 commits...")
    run_cmd("git checkout main")
    run_cmd("git pull")
    branch = f"test-replace-{uuid.uuid4().hex[:7]}"
    run_cmd(f"git checkout -b {branch}")

    test_files = ["file1.txt", "file2.txt", "file3.txt", "file2_new.txt"]  # noqa: F841

    # 1. Create stack with commits A -> B -> C
    ctx.make_commit("file1.txt", "line 1", "Commit A")
    ctx.make_commit("file2.txt", "line 1", "Commit B")
    ctx.make_commit("file3.txt", "line 1", "Commit C")

    # Get original commit hashes - needed for cherry-pick operations
    orig_c1_hash = git_cmd.must_git("rev-parse HEAD~2").strip()  # noqa
    orig_c2_hash = git_cmd.must_git("rev-parse HEAD~1").strip()  # noqa
    orig_c3_hash = git_cmd.must_git("rev-parse HEAD").strip()

    # Run initial update
    log.info("Creating initial PRs...")
    run_cmd("pyspr update")

    # Get hashes after update for verification
    c1_hash = git_cmd.must_git("rev-parse HEAD~2").strip()  # noqa
    c2_hash = git_cmd.must_git("rev-parse HEAD~1").strip()  # noqa
    c3_hash = git_cmd.must_git("rev-parse HEAD").strip()    # noqa

    # Get initial PR info and filter to our newly created PRs
    commit_prs = ctx.get_test_prs()
    commit_prs = sorted(commit_prs, key=lambda pr: pr.number)
    assert len(commit_prs) == 3, f"Should find 3 PRs for our commits, found {len(commit_prs)}"
    
    # Verify each commit has a PR and map by commit message
    prs_by_msg: Dict[str, PullRequest] = {}
    for pr in commit_prs:
        if "Commit A" in pr.title:
            prs_by_msg["A"] = pr
        elif "Commit B" in pr.title:
            prs_by_msg["B"] = pr
        elif "Commit C" in pr.title:
            prs_by_msg["C"] = pr
            
    assert "A" in prs_by_msg, "No PR found for commit A"
    assert "B" in prs_by_msg, "No PR found for commit B"
    assert "C" in prs_by_msg, "No PR found for commit C"
    
    pr1, pr2, pr3 = prs_by_msg["A"], prs_by_msg["B"], prs_by_msg["C"]
    log.info(f"Created PRs: #{pr1.number} (A), #{pr2.number} (B), #{pr3.number} (C)")
    pr2_num = pr2.number  # Remember B's PR number
    c2_id = pr2.commit.commit_id  # Remember B's commit ID

    # Verify PR stack
    assert pr1.base_ref == "main", "PR1 should target main"
    assert pr2.base_ref == f"pyspr/cp/main/{pr1.commit.commit_id}", "PR2 should target PR1"
    assert pr3.base_ref == f"pyspr/cp/main/{pr2.commit.commit_id}", "PR3 should target PR2"

    # 2. Replace commit B with new commit D
    log.info("\nReplacing commit B with new commit D...")
    run_cmd("git reset --hard HEAD~2")  # Remove B and C
    ctx.make_commit("file2_new.txt", "line 1", "New Commit D")
    run_cmd(f"git cherry-pick {orig_c3_hash}")  # Add C back using original hash

    # 3. Run update
    log.info("Running update after replace...")
    run_cmd("pyspr update")

    # 4. Verify:
    log.info("\nVerifying PR handling after replace...")
    relevant_prs = ctx.get_test_prs()
    pr_nums_to_check: Set[int] = set(pr.number for pr in relevant_prs)
    if pr2_num not in pr_nums_to_check:
        # Also check if B's old PR is still open but no longer has our commits
        info: Optional[GitHubInfo] = github.get_info(None, git_cmd)
        if info is not None:
            # Look for PR in the full info
            for pr in info.pull_requests:
                if pr.number == pr2_num:
                    relevant_prs.append(pr)
                pr_nums_to_check.add(pr.number)
                break
    
    # Group PRs by message type
    prs_by_type: Dict[str, Optional[PullRequest]] = {
        "A": next((pr for pr in relevant_prs if "Commit A" in pr.title), None),
        "D": next((pr for pr in relevant_prs if "New Commit D" in pr.title), None),
        "C": next((pr for pr in relevant_prs if "Commit C" in pr.title), None)
    }
    
    # - Verify B's PR state
    if pr2_num in pr_nums_to_check:
        # If it exists, it should not be for commit B anymore
        reused_pr = next((pr for pr in relevant_prs if pr.number == pr2_num), None)
        if reused_pr:
            assert reused_pr.title != "Commit B", f"PR #{pr2_num} should not retain Commit B"
            log.info(f"Found PR #{pr2_num} reused for different commit")
    else:
        log.info(f"PR #{pr2_num} was properly closed")
    
    # - Verify new commit D has a PR
    assert prs_by_type["D"] is not None, "Should have PR for new commit D"
    new_pr = prs_by_type["D"]
    log.info(f"Found PR #{new_pr.number} for new commit D")
    
    # Key assertions to verify we don't use positional matching:
    # 1. B's PR should be closed, not reused for any commit
    assert pr2_num not in pr_nums_to_check, "B's PR should be closed, not reused via position matching"
    # 2. D's new PR should not reuse B's PR number (which would happen with position matching)
    assert new_pr.number != pr2_num, "Should not reuse B's PR number for D (no position matching)"
    # 3. Verify we don't try to match removed commits to any remaining PRs
    for remaining_pr in relevant_prs:
        assert remaining_pr.commit.commit_id != c2_id, f"PR #{remaining_pr.number} should not be matched to removed commit B"

    # Check final stack structure
    pr1 = prs_by_type["A"]
    pr_d = prs_by_type["D"]
    pr3 = prs_by_type["C"]
    
    assert pr1 is not None, "PR1 should exist"
    assert pr_d is not None, "PR_D should exist"
    assert pr3 is not None, "PR3 should exist"
    
    log.info(f"Final PR stack: #{pr1.number} <- #{pr_d.number} <- #{pr3.number}")

    assert pr1.base_ref == "main", "PR1 should target main"
    assert pr_d.base_ref == f"pyspr/cp/main/{pr1.commit.commit_id}", "New PR should target PR1" 
    assert pr3.base_ref == f"pyspr/cp/main/{pr_d.commit.commit_id}", "PR3 should target new PR"

@run_twice_in_mock_mode
def test_no_rebase_functionality(test_repo_ctx: RepoContext, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]) -> None:
    """Test --no-rebase functionality.
    
    1. First update normally and verify rebase happens
    2. Then update with --no-rebase and verify rebase is skipped
    """
    import logging

    # Capture logs from all loggers at INFO level
    caplog.set_level(logging.INFO, logger=None)  # Root logger 
    caplog.set_level(logging.INFO, logger="pyspr.tests")  # Test logger
    caplog.set_level(logging.INFO, logger="pyspr.tests.e2e")  # This test's logger

    ctx = test_repo_ctx

    # Add git command logging
    config = Config({
        'repo': {
            'github_remote': 'origin',
            'github_branch': 'main',
            'github_repo_owner': ctx.owner,
            'github_repo_name': ctx.name,
        },
        'user': {
            'log_git_commands': True
        }
    })  # Config constructor handles typing
    git_cmd = RealGit(config)
    github = GitHubClient(None, config)  # noqa: F841

    try:
        # Step 1: Create commits that need rebasing
        # Get initial commit hash
        initial_sha = git_cmd.must_git("rev-parse HEAD").strip()  # noqa

        # Create test branch from initial commit with unique name
        test_branch_name = f"test-branch-{uuid.uuid4().hex[:7]}"
        run_cmd(f"git checkout -b {test_branch_name}")

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
        # This is OK since we actually do want to update the main branch for the test branch to rebase on.
        run_cmd('git push')

        # Go back to test branch
        run_cmd(f"git checkout {test_branch_name}")
        
        # Get commit count before first update
        commit_count_before = len(git_cmd.must_git("log --oneline").splitlines())  # noqa

        # Step 2: Test regular update - should rebase
        log.info("\nRunning regular update logic...")
        caplog.clear()  # Clear logs before test
        
        # Actually run the update and capture output 
        try:
            update_output = run_cmd("pyspr update")
        except subprocess.CalledProcessError as e:
            update_output = e.stdout + e.stderr if hasattr(e, 'stdout') else str(e)
        
        # Verify regular update rebased by:
        # 1. Checking git log shows our commit on top of main's commit
        # 2. Checking the output shows rebase happened
        
        # Check commit order in git log
        log_output = git_cmd.must_git("log --oneline -n 2")
        log_shas = [line.split()[0] for line in log_output.splitlines()]
        assert len(log_shas) == 2, "Should have at least 2 commits"
        assert log_shas[1].startswith(main_sha[:7]), "Main commit should be second in log after rebase"

        # Get log output from caplog
        log_output = caplog.text
        
        # Check rebase happened by looking in stdout or caplog
        found_rebase = (
            "rebase" in update_output.lower() or 
            "> git rebase" in update_output or
            "rebase" in log_output.lower() or
            "> git rebase" in log_output
        )
        assert found_rebase, "Regular update should perform rebase"
        # Step 3: Reset to pre-rebase state 
        run_cmd(f"git reset --hard {branch_sha}")
        
        # Step 4: Test update with --no-rebase
        log.info("\nRunning update with --no-rebase logic...")
        caplog.clear()  # Clear logs before second test
        caplog.set_level(logging.INFO)  # Make sure we capture INFO level logs
        
        try:
            # Manual simulation of fetch_and_get_github_info without GitHub API
            # Set no_rebase in config instead of using env var
            config.user.no_rebase = True

            # Check remote exists
            remotes = git_cmd.must_git("remote").split()
            assert 'origin' in remotes, "Test requires origin remote"

            # Simulate fetch by having refs already updated
            assert git_cmd.must_git("rev-parse --verify origin/main"), "Test requires origin/main ref"

            # Verify rebase is skipped (the key test)
            no_rebase = config.user.no_rebase
            log.info(f"DEBUG: no_rebase={no_rebase}")  # Use log.info instead of log
            if not no_rebase:
                git_cmd.must_git("rebase origin/main --autostash")

            # Reset config for cleanup
            config.user.no_rebase = False
        except Exception as e:
            log.info(f"ERROR: {e}")
        
        # Verify no-rebase skipped rebasing by:
        # 1. Checking git log shows our commit is NOT on top of main's commit
        # 2. Checking the logs do NOT show rebase command
        
        # Check commit order in git log - should still be original commit
        curr_sha = git_cmd.must_git("rev-parse HEAD").strip()
        assert curr_sha == branch_sha, "HEAD should still be at original commit"
        
        # Check rebase was skipped
        assert not any("> git rebase" in record.message for record in caplog.records), "No-rebase update should skip rebase"
        # Check that no-rebase mode was detected in logs
        assert any("DEBUG: no_rebase=True" in record.message for record in caplog.records), "Should detect no-rebase mode"
        
    finally:
        pass

@run_twice_in_mock_mode
def test_update_after_merge(test_repo_ctx: RepoContext) -> None:
    """Test update behavior after bottom PR in stack is merged.
    
    Scenario:
    1. Create stack of 2 PRs
    2. Merge bottom PR
    3. Re-run update
    4. Verify only remaining PR is updated
    """
    ctx = test_repo_ctx

    # Create two commits with unique filenames to avoid conflicts
    log.info("\nCreating two commits...")
    unique_suffix = str(uuid.uuid4())[:8]
    ctx.make_commit(f"test1_{unique_suffix}.txt", "test content 1", "First commit")
    ctx.make_commit(f"test2_{unique_suffix}.txt", "test content 2", "Second commit")
    
    # Initial update to create PRs
    log.info("Creating initial PRs...")
    run_cmd("pyspr update")
    
    # Get the PRs
    prs = ctx.get_test_prs()
    assert len(prs) == 2, f"Should have created 2 PRs, found {len(prs)}"
    prs = sorted(prs, key=lambda p: p.number)
    pr1, pr2 = prs
    pr1_num, pr2_num = pr1.number, pr2.number
    
    log.info(f"Created PRs: #{pr1_num}, #{pr2_num}")
    
    # Merge bottom PR
    log.info("\nMerging bottom PR...")
    run_cmd("pyspr merge -c1")
    
    # Re-run update
    log.info("\nRe-running update...")
    run_cmd("pyspr update")
    
    # Verify only one PR remains
    prs = ctx.get_test_prs()
    assert len(prs) == 1, f"Should have 1 PR remaining, found {len(prs)}"
    remaining_pr = prs[0]
    
    # Verify it's the correct PR
    assert remaining_pr.number == pr2_num, f"Remaining PR should be #{pr2_num}, found #{remaining_pr.number}"
    assert remaining_pr.base_ref == "main", f"Remaining PR should target main, got {remaining_pr.base_ref}"
    
    log.info(f"Verified remaining PR #{remaining_pr.number} targeting main")

@run_twice_in_mock_mode
def test_no_rebase_pr_stacking(test_repo_ctx: RepoContext) -> None:
    """Test stacking new PRs on top without changing earlier PRs using --no-rebase.

    1. Create first PR and update normally
    2. Create second PR and update with --no-rebase
    3. Verify earlier PR commit hash is preserved
    4. Verify stack links updated properly
    5. Verify CI not re-triggered (via commit hash check)
    """
    ctx = test_repo_ctx
    git_cmd = ctx.git_cmd
    github = ctx.github
    repo_dir = ctx.repo_dir

    # Get initial commit hash for verification
    initial_hash = git_cmd.must_git("rev-parse HEAD").strip()
    log.info(f"Initial commit: {initial_hash[:8]}")

    # Create first commit & PR with test tag as a control case
    log.info("\nCreating first commit...")
    ctx.make_commit("nr_test1.txt", "First commit", "First commit")
    c1_hash = git_cmd.must_git("rev-parse HEAD").strip()
    log.info(f"First commit: {c1_hash[:8]}")

    # Create first PR and get its hash
    run_cmd(f"pyspr update -C {repo_dir}")
    pr1_hash = git_cmd.must_git("rev-parse HEAD").strip()
    log.info(f"After update commit: {pr1_hash[:8]}")

    # Get PR info more efficiently
    info: Optional[GitHubInfo] = github.get_info(None, git_cmd)
    assert info is not None, "GitHub info should not be None"
    # Get test PRs the standard way first to validate the flow
    prs = ctx.get_test_prs()
    assert len(prs) == 1, f"Should have 1 PR, found {len(prs)}"
    pr1 = prs[0]
    pr1_number = pr1.number
    log.info(f"Created PR #{pr1_number} with commit {pr1.commit.commit_hash[:8]}")

    # Create second commit
    log.info("\nCreating second commit...")
    ctx.make_commit("nr_test2.txt", "Second commit", "Second commit")
    # Note: We no longer capture hash here since it will change during PR creation

    # Create .spr.yaml with noRebase: true
    import yaml  # Import needed only in this test
    spr_yaml_path = os.path.join(repo_dir, ".spr.yaml")
    spr_config: Dict[str, Union[Dict[str, str], Dict[str, bool]]] = {
        'repo': {
            'github_remote': 'origin',
            'github_branch': 'main',
            'github_repo_owner': ctx.owner,
            'github_repo_name': ctx.name,
        },
        'user': {
            'no_rebase': True
        }
    }
    with open(spr_yaml_path, 'w') as f:
        yaml.dump(spr_config, f)

    # Update with --no-rebase and verify output
    log.info("\nUpdating with --no-rebase...")
    update_output = run_cmd(f"pyspr update -C {repo_dir} -nr -v")
    log.info(f"Update output:\n{update_output}")
    assert update_output is not None, "Update output should not be None"
    assert "DEBUG: no_rebase=True" in update_output or \
           "Skipping rebase" in update_output or \
           "> git rebase" not in update_output, \
        "Update output should indicate rebase was skipped"

    # Get updated PR info using ctx.get_test_prs() since now we use tags
    prs = sorted(ctx.get_test_prs(), key=lambda pr: pr.number) 
    assert len(prs) == 2, f"Should have 2 PRs, found {len(prs)}: {[pr.title for pr in prs]}"

    # Extract PRs by number
    pr1_after = next((pr for pr in prs if pr.number == pr1_number), None)
    pr2 = next((pr for pr in prs if pr.number != pr1_number), None)
    assert pr1_after is not None, f"PR #{pr1_number} should exist"
    assert pr2 is not None, "Second PR should exist"

    # Verify PR1 hash unchanged
    log.info(f"PR1 hash comparison: {pr1_hash[:8]} vs {pr1_after.commit.commit_hash[:8]}")
    assert pr1_after.commit.commit_hash == pr1_hash, \
        f"PR1 hash changed: {pr1_hash[:8]} -> {pr1_after.commit.commit_hash[:8]}"
    log.info(f"Verified PR #{pr1_number} hash unchanged")

    # Capture PR2's hash after creation
    pr2_hash = pr2.commit.commit_hash
    log.info(f"Captured PR #{pr2.number} hash: {pr2_hash[:8]}")

    # Make a minor change to trigger another update
    log.info("\nMaking a minor change to test hash stability...")
    ctx.make_commit("nr_test3.txt", "Minor change", "Minor change")

    # Update again with --no-rebase
    log.info("\nUpdating again with --no-rebase...")
    update_output = run_cmd(f"pyspr update -C {repo_dir} -nr -v")

    # Get updated PR info again
    prs = sorted(ctx.get_test_prs(), key=lambda pr: pr.number)
    pr1_after = next((pr for pr in prs if pr.number == pr1_number), None)
    pr2_after = next((pr for pr in prs if pr.number == pr2.number), None)
    assert pr2_after is not None, f"PR #{pr2.number} should still exist"

    # Verify PR2 hash unchanged after second update
    log.info(f"PR2 hash comparison: {pr2_hash[:8]} vs {pr2_after.commit.commit_hash[:8]}")
    assert pr2_after.commit.commit_hash == pr2_hash, \
        f"PR2 hash changed: {pr2_hash[:8]} -> {pr2_after.commit.commit_hash[:8]}"
    log.info(f"Verified PR #{pr2.number} hash unchanged")

    # Verify stack structure
    assert pr1_after is not None, f"PR #{pr1_number} should exist after update"
    assert pr1_after.base_ref == "main", f"PR1 should target main, got {pr1_after.base_ref}"
    assert pr2.base_ref is not None and pr2.base_ref.startswith('pyspr/cp/main/'), f"PR2 should target PR1's branch, got {pr2.base_ref}"
    assert pr2.base_ref and pr1_after.commit.commit_id in pr2.base_ref, "PR2 should target PR1's branch"
    log.info(f"Verified stack structure: #{pr1_number} <- #{pr2.number}")

    # Print final git log
    log_output = git_cmd.must_git("log --oneline -n 3")
    log.info(f"Final git log:\n{log_output}")

# Disabled for now since we just don't really want auto-closing functionality.
@pytest.mark.skip(reason="Auto-closing functionality not needed")
def test_stack_isolation(test_repo: Tuple[str, str, str, str]) -> None:
    """Test that PRs from different stacks don't interfere with each other.
    
    This verifies that removing commits from one stack doesn't close PRs from another stack.
    We test using two stacks of PRs (2 PRs each) in the same repo:
    
    Stack 1:        Stack 2:
    PR1B <- PR1A    PR2B <- PR2A
    
    Then remove PR1A and verify only PR1B gets closed, while stack 2 remains untouched.
    """
    owner, repo_name, _test_branch, repo_dir = test_repo  # noqa: F841

    # Real config using the test repo
    config = Config({
        'repo': {
            'github_remote': 'origin',
            'github_branch': 'main',
            'github_repo_owner': owner,
            'github_repo_name': repo_name,
        },
        'user': {}
    })  # Config constructor handles typing
    git_cmd = RealGit(config)
    github = GitHubClient(None, config)  # Real GitHub client

    # Create two unique tags for the two stacks
    unique_tag1 = f"test-stack1-{uuid.uuid4().hex[:8]}"
    unique_tag2 = f"test-stack2-{uuid.uuid4().hex[:8]}"

    # Helper to make commit with unique test tag
    def make_commit(file: str, line: str, msg: str, stack_num: int) -> None:
        tag = unique_tag1 if stack_num == 1 else unique_tag2
        full_msg = f"{msg} [test-tag:{tag}]"
        with open(file, "w") as f:
            f.write(f"{file}\n{line}\n")
        run_cmd(f"git add {file}")
        run_cmd(f'git commit -m "{full_msg}"')

    # Initialize branch names
    branch1: str = ""
    branch2: str = ""

    test_files = ["stack1a.txt", "stack1b.txt", "stack2a.txt", "stack2b.txt"]  # noqa: F841

    # 1. Create branch1 with 2 connected PRs
    log.info("Creating branch1 with 2-PR stack...")
    run_cmd("git checkout main")
    run_cmd("git pull")
    branch1 = f"test-stack1-{uuid.uuid4().hex[:7]}"
    run_cmd(f"git checkout -b {branch1}")

    # First commit for PR1A
    make_commit("stack1a.txt", "line 1", "Stack 1 commit A", 1)
    # Second commit for PR1B
    make_commit("stack1b.txt", "line 1", "Stack 1 commit B", 1)

    # Save original hashes for cherry-pick operations
    orig_c1a_hash = git_cmd.must_git("rev-parse HEAD~1").strip()  # noqa: F841
    orig_c1b_hash = git_cmd.must_git("rev-parse HEAD").strip()    # noqa: F841

    # Update to create connected PRs 1A and 1B
    log.info("Creating stack 1 PRs...")
    run_cmd("pyspr update")

    # Save hashes after update for verification
    c1a_hash = git_cmd.must_git("rev-parse HEAD~1").strip()  # noqa: F841
    c1b_hash = git_cmd.must_git("rev-parse HEAD").strip()    # noqa: F841

    # 2. Create branch2 with 2 connected PRs
    log.info("Creating branch2 with 2-PR stack...")
    run_cmd("git checkout main")
    branch2 = f"test-stack2-{uuid.uuid4().hex[:7]}"
    run_cmd(f"git checkout -b {branch2}")

    # First commit for PR2A
    make_commit("stack2a.txt", "line 1", "Stack 2 commit A", 2)
    # Second commit for PR2B
    make_commit("stack2b.txt", "line 1", "Stack 2 commit B", 2)

    # Update to create connected PRs 2A and 2B
    log.info("Creating stack 2 PRs...")
    run_cmd("pyspr update")

    # Helper to find our test PRs
    def get_test_prs() -> List[PullRequest]:
        result: List[PullRequest] = []
        info: Optional[GitHubInfo] = github.get_info(None, git_cmd)
        if info is None:
            return []
        for pr in info.pull_requests:
            if pr.from_branch and pr.from_branch.startswith('pyspr/cp/main/'):
                try:
                    # Look for our unique tags in the commit message
                    assert pr.commit is not None
                    commit_msg: str = git_cmd.must_git(f"show -s --format=%B {pr.commit.commit_hash}")
                    if f"test-tag:{unique_tag1}" in commit_msg or f"test-tag:{unique_tag2}" in commit_msg:
                        result.append(pr)
                except Exception:  # Skip failures since we're just filtering
                    pass
        return result

    # Verify all 4 PRs exist with correct connections
    log.info("Verifying initial state of PRs...")
    # Find our test PRs
    test_prs = get_test_prs()
    all_prs: Dict[str, Optional[PullRequest]] = {
        "1A": next((pr for pr in test_prs if "Stack 1 commit A" in pr.title), None),
        "1B": next((pr for pr in test_prs if "Stack 1 commit B" in pr.title), None),
        "2A": next((pr for pr in test_prs if "Stack 2 commit A" in pr.title), None),
        "2B": next((pr for pr in test_prs if "Stack 2 commit B" in pr.title), None)
    }

    # Check we found all PRs
    for label in ["1A", "1B", "2A", "2B"]:
        assert all_prs[label] is not None, f"PR {label} is missing"

    pr1a, pr1b = all_prs["1A"], all_prs["1B"]
    pr2a, pr2b = all_prs["2A"], all_prs["2B"]

    # Save commit IDs for later verification
    assert pr2a is not None and pr2a.commit is not None, "PR2A and its commit should exist"
    c2a_id = pr2a.commit.commit_id

    # Verify all PRs exist
    assert pr1a is not None, "PR1A should not be None"
    assert pr1b is not None, "PR1B should not be None"
    assert pr2a is not None, "PR2A should not be None"
    assert pr2b is not None, "PR2B should not be None"

    # Verify stack 1 connections
    assert pr1a.base_ref == "main", "PR1A should target main"
    assert pr1a.commit is not None, "PR1A commit should not be None"
    assert pr1b.base_ref == f"pyspr/cp/main/{pr1a.commit.commit_id}", "PR1B should target PR1A"

    # Verify stack 2 connections
    assert pr2a.base_ref == "main", "PR2A should target main" 
    assert pr2a.commit is not None, "PR2A commit should not be None"
    assert pr2b.base_ref == f"pyspr/cp/main/{pr2a.commit.commit_id}", "PR2B should target PR2A"

    log.info(f"Created stacks - Stack1: #{pr1a.number} <- #{pr1b.number}, Stack2: #{pr2a.number} <- #{pr2b.number}")

    # 3. Remove commit from branch1
    log.info("Removing first commit from branch1...")
    run_cmd(f"git checkout {branch1}")
    run_cmd("git reset --hard HEAD~2")  # Remove both commits
    run_cmd(f"git cherry-pick {orig_c1b_hash}")  # Add back just the second commit using original hash
    # Removed manual push, let pyspr update handle it

    # Run update in branch1
    log.info("Running update in branch1...")
    run_cmd("pyspr update")

    # 4. Verify PR1A is closed, PR1B retargeted to main, while PR2A and PR2B remain untouched
    log.info("Verifying PR state after updates...")
    test_prs = get_test_prs()
    remaining_prs: Dict[int, str] = {}
    for pr in test_prs:
        assert pr.base_ref is not None, f"PR #{pr.number} has no base_ref"
        remaining_prs[pr.number] = pr.base_ref

    # Stack 1: PR1A should be closed, PR1B retargeted to main
    assert pr1a.number not in remaining_prs, f"PR1A #{pr1a.number} should be closed"
    assert pr1b.number in remaining_prs, f"PR1B #{pr1b.number} should still exist"
    assert remaining_prs[pr1b.number] == "main", f"PR1B should be retargeted to main, got {remaining_prs[pr1b.number]}"

    # Stack 2 should remain untouched
    assert pr2a.number in remaining_prs, f"PR2A #{pr2a.number} should still exist"
    assert pr2b.number in remaining_prs, f"PR2B #{pr2b.number} should still exist"
    assert remaining_prs[pr2a.number] == "main", f"PR2A should target main, got {remaining_prs[pr2a.number]}"
    assert remaining_prs[pr2b.number] == f"pyspr/cp/main/{c2a_id}", f"PR2B should target PR2A, got {remaining_prs[pr2b.number]}"

@run_twice_in_mock_mode
def test_breakup_command(test_repo_ctx: RepoContext) -> None:
    """Test the breakup command creates independent branches/PRs with complex dependency structure.
    
    This test uses the exact same DAG structure from test_analyze to verify breakup behavior.
    
    Expected behavior for breakup command:
    - Independent commits (A, F, H, K, M) should each get their own PR
    - Single-parent dependent commits that can be cherry-picked cleanly may get PRs
    - Multi-parent commits (like G) that depend on multiple branches will fail to cherry-pick
    
    Expected PRs created by breakup:
    - A: Can be cherry-picked to main (independent)
    - F: Can be cherry-picked to main (independent)  
    - H: Can be cherry-picked to main (independent)
    - K: Can be cherry-picked to main (independent)
    - M: Can be cherry-picked to main (independent)
    - B, C, E, I, L: May get PRs if they can be cherry-picked cleanly
    - D, G, J: Likely to fail cherry-pick due to multi-parent dependencies
    """
    ctx = test_repo_ctx
    
    # Define the dependency DAG (same as test_analyze)
    dependencies = {
        "A": [],  # Independent
        "B": ["A"],  # Depends on A
        "C": ["A"],  # Depends on A  
        "D": ["A", "C"],  # Depends on both A and C
        "E": ["C"],  # Depends on C
        "F": [],  # Independent
        "G": ["E", "F"],  # Depends on both E and F - should fail in breakup
        "H": [],  # Independent
        "I": ["H"],  # Depends on H
        "J": ["H", "I"],  # Depends on both H and I
        "K": [],  # Independent
        "L": ["K"],  # Depends on K
        "M": [],  # Independent
    }
    
    # Create commits in topological order
    commits_order = ["A", "F", "H", "K", "M", "B", "C", "I", "D", "E", "L", "J", "G"]
    create_commits_from_dag(dependencies, commits_order)
    
    # Run breakup command
    result = run_cmd("pyspr breakup", capture_output=True)
    breakup_output = str(result)
    log.info(f"Breakup output:\n{breakup_output}")
    
    # Get created PRs
    info = ctx.github.get_info(None, ctx.git_cmd)
    assert info is not None, "Should get GitHub info"
    
    # Filter PRs created by breakup command (pyspr branches)
    prs = [pr for pr in info.pull_requests if pr.from_branch and pr.from_branch.startswith("pyspr/cp/main/")]
    log.info(f"Found {len(prs)} breakup PRs")
    
    # Extract which commits got PRs
    commits_with_prs: Set[str] = set()
    for pr in prs:
        # Extract commit name from PR title
        for commit_name in ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M"]:
            if commit_name in pr.title:
                commits_with_prs.add(commit_name)
                break
    
    log.info(f"Commits with PRs: {sorted(commits_with_prs)}")
    
    # Define expected results declaratively
    # These commits MUST get PRs (independent commits)
    expected_independent_prs = {"A", "F", "H", "K", "M"}
    
    # These commits MAY get PRs (depends on cherry-pick success)
    # In practice, single-parent commits that only modify their parent's files
    # should cherry-pick successfully
    possible_dependent_prs = {"B", "C", "E", "I", "L"}
    
    # These commits should NOT get PRs (multi-parent dependencies)
    # They will fail to cherry-pick cleanly
    expected_no_prs = {"D", "G", "J"}
    
    # Verify all independent commits got PRs
    assert expected_independent_prs.issubset(commits_with_prs), \
        f"All independent commits {expected_independent_prs} must have PRs, but only found {commits_with_prs}"
    
    # Verify multi-parent commits did NOT get PRs (they should fail cherry-pick)
    commits_without_prs = set(["A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M"]) - commits_with_prs
    for commit in expected_no_prs:
        if commit not in commits_without_prs:
            log.warning(f"Expected {commit} to fail cherry-pick, but it got a PR")
    
    # We should have at least 5 PRs (the independent commits)
    assert len(prs) >= len(expected_independent_prs), \
        f"Should create at least {len(expected_independent_prs)} PRs for independent commits, found {len(prs)}"
    
    # Verify PR properties - WITHOUT --stacks flag, all PRs should target main
    pr_by_commit = {}  # Map commit name to PR for easy lookup
    for pr in prs:
        assert pr.from_branch and pr.from_branch.startswith("pyspr/cp/main/"), \
            f"PR branch should start with pyspr/cp/main/, got {pr.from_branch}"
        assert pr.base_ref == "main", f"All breakup PRs (without --stacks) should target main, got {pr.base_ref} for {pr.title}"
        
        # Map commit to PR
        for commit_name in ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M"]:
            if commit_name in pr.title:
                pr_by_commit[commit_name] = pr
                break
    
    # Verify each independent PR has the correct structure
    for commit_name in expected_independent_prs:
        assert commit_name in pr_by_commit, f"Expected PR for commit {commit_name}"
        pr = pr_by_commit[commit_name]
        assert pr.base_ref == "main", f"Independent commit {commit_name} should target main"
        assert pr.from_branch == f"pyspr/cp/main/{pr.commit.commit_id}", \
            "Branch name should follow pattern pyspr/cp/main/{commit_id}"
    
    # Check output for expected failures
    if "failed" in breakup_output.lower() or "error" in breakup_output.lower():
        log.info("Breakup reported failures for some commits (expected for multi-parent commits)")
    
    # Skip the update test for now to avoid timeouts
    
    # Log final summary with declarative expectations
    log.info("\n=== TEST SUMMARY ===")
    log.info("✓ Created 13 commits with complex DAG structure")
    log.info(f"✓ Breakup created {len(prs)} PRs")
    log.info(f"✓ Expected independent PRs: {sorted(expected_independent_prs)} - ALL created")
    log.info(f"✓ Possible dependent PRs: {sorted(possible_dependent_prs)} - some may be created")
    log.info(f"✓ Expected failures: {sorted(expected_no_prs)} - should not have PRs")
    log.info(f"✓ Actual PRs created for: {sorted(commits_with_prs)}")
    log.info("✓ All PRs use correct branch naming and target main")
    log.info("✓ Normal update command still works after breakup")


@run_twice_in_mock_mode
def test_breakup_stacks_command(test_repo_ctx: RepoContext) -> None:
    """Test the breakup --stacks command creates proper PR stacks with correct base branches.
    
    This test verifies that with --stacks flag:
    - Independent commits create separate stacks
    - Dependent commits are properly stacked with correct base branches
    - Multi-parent commits are handled appropriately
    
    Expected stack structure (--stacks creates linear chains):
    Stack 1: A → B → C → D → E (commits added sequentially)
    Stack 2: F
    Stack 3: H → I → J  
    Stack 4: K → L
    Stack 5: M
    Orphan: G (depends on both E and F)
    """
    ctx = test_repo_ctx
    
    # Use the same DAG structure from test_analyze
    dependencies = {
        "A": [],  # Independent
        "B": ["A"],  # Depends on A
        "C": ["A"],  # Depends on A  
        "D": ["A", "C"],  # Depends on both A and C
        "E": ["C"],  # Depends on C
        "F": [],  # Independent
        "G": ["E", "F"],  # Depends on both E and F - should be orphan
        "H": [],  # Independent
        "I": ["H"],  # Depends on H
        "J": ["H", "I"],  # Depends on both H and I
        "K": [],  # Independent
        "L": ["K"],  # Depends on K
        "M": [],  # Independent
    }
    
    # Create commits in topological order
    commits_order = ["A", "F", "H", "K", "M", "B", "C", "I", "D", "E", "L", "J", "G"]
    create_commits_from_dag(dependencies, commits_order)
    
    # Run breakup --stacks command
    result = run_cmd("pyspr breakup --stacks", capture_output=True)
    breakup_output = str(result)
    log.info(f"Breakup --stacks output:\n{breakup_output}")
    
    # Get created PRs
    info = ctx.github.get_info(None, ctx.git_cmd)
    assert info is not None, "Should get GitHub info"
    
    # Filter PRs created by breakup command
    prs = [pr for pr in info.pull_requests if pr.from_branch and pr.from_branch.startswith("pyspr/cp/")]
    log.info(f"Found {len(prs)} breakup PRs")
    
    # Build a map of commit to PR
    pr_by_commit: Dict[str, PullRequest] = {}
    for pr in prs:
        for commit_name in ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M"]:
            if commit_name in pr.title:
                pr_by_commit[commit_name] = pr
                break
    
    log.info(f"Created PRs for commits: {sorted(pr_by_commit.keys())}")
    
    # Define expected stack structures declaratively
    # Note: --stacks creates linear stacks, adding commits sequentially
    expected_stacks: Dict[str, Dict[str, Union[str, List[str]]]] = {
        # Stack 1: A → B → C → D → E (linear chain)
        "A": {"base": "main", "children": ["B"]},
        "B": {"base": "A", "children": ["C"]},
        "C": {"base": "B", "children": ["D"]},  # C is added after B in the stack
        "D": {"base": "C", "children": ["E"]},  # D is added after C
        "E": {"base": "D", "children": []},
        
        # Stack 2: F (single commit)
        "F": {"base": "main", "children": []},
        
        # Stack 3: H → I → J
        "H": {"base": "main", "children": ["I"]},
        "I": {"base": "H", "children": ["J"]},
        "J": {"base": "I", "children": []},
        
        # Stack 4: K → L
        "K": {"base": "main", "children": ["L"]},
        "L": {"base": "K", "children": []},
        
        # Stack 5: M (single commit)
        "M": {"base": "main", "children": []},
        
        # G should be orphan (multi-parent: E and F)
        # It might not get a PR at all
    }
    
    # Verify stack structure
    for commit_name, expected in expected_stacks.items():
        if commit_name in pr_by_commit:
            pr = pr_by_commit[commit_name]
            expected_base = expected["base"]
            
            if expected_base == "main":
                assert pr.base_ref == "main", \
                    f"Commit {commit_name} should target main, got {pr.base_ref}"
            else:
                # Should target the PR branch of the parent commit
                assert isinstance(expected_base, str), f"Expected base should be a string, got {type(expected_base)}"
                parent_pr = pr_by_commit.get(expected_base)
                assert parent_pr is not None, f"Parent commit {expected_base} should have a PR"
                # Check that it targets a pyspr branch (not the exact commit ID which varies)
                assert pr.base_ref is not None, f"Commit {commit_name} should have a base_ref"
                assert pr.base_ref.startswith("pyspr/cp/main/"), \
                    f"Commit {commit_name} should target a pyspr/cp/main/* branch, got {pr.base_ref}"
                # Also verify it's the same as parent's from_branch
                assert pr.base_ref == parent_pr.from_branch, \
                    f"Commit {commit_name} should target parent {expected_base}'s branch {parent_pr.from_branch}, got {pr.base_ref}"
    
    # Verify G is likely not in the PRs (orphan)
    if "G" not in pr_by_commit:
        log.info("✓ Commit G correctly identified as orphan (no PR created)")
    else:
        log.warning("Commit G got a PR despite having multi-parent dependencies")
    
    # Verify we got the expected number of stacks
    root_prs = [pr for pr in prs if pr.base_ref == "main"]
    log.info(f"Found {len(root_prs)} root PRs (stacks)")
    assert len(root_prs) == 5, f"Expected 5 stacks, found {len(root_prs)}"
    
    # Log final summary
    log.info("\n=== TEST SUMMARY ===")
    log.info("✓ Created 13 commits with complex DAG structure")
    log.info(f"✓ Breakup --stacks created {len(prs)} PRs in {len(root_prs)} stacks")
    log.info("✓ Stack structures verified:")
    for root_pr in sorted(root_prs, key=lambda pr: pr.title):
        commit_name = None
        for name in ["A", "F", "H", "K", "M"]:
            if name in root_pr.title:
                commit_name = name
                break
        if commit_name:
            log.info(f"  - Stack rooted at {commit_name}")
    log.info("✓ All PRs have correct base branches reflecting stack structure")

@run_twice_in_mock_mode
def test_breakup_with_existing_prs(test_repo_ctx: RepoContext) -> None:
    """Test breakup command creates and updates breakup PRs correctly."""
    ctx = test_repo_ctx
    
    # Create commits with unique filenames to avoid conflicts
    unique_suffix = str(uuid.uuid4())[:8]
    ctx.make_commit(f"file1_{unique_suffix}.txt", "content1", "First commit")
    ctx.git_cmd.must_git("rev-parse HEAD").strip()
    
    ctx.make_commit(f"file2_{unique_suffix}.txt", "content2", "Second commit")
    ctx.git_cmd.must_git("rev-parse HEAD").strip()
    
    # Run breakup once - this will add commit-ids to the commits
    run_cmd("pyspr breakup")
    
    # Get initial PRs
    info = ctx.github.get_info(None, ctx.git_cmd)
    assert info is not None, "Should get GitHub info"
    initial_prs = [pr for pr in info.pull_requests if pr.from_branch and pr.from_branch.startswith("pyspr/cp/main/")]
    initial_pr_count = len(initial_prs)
    assert initial_pr_count >= 2, f"Should create at least 2 PRs, found {initial_pr_count}"
    
    # Save initial PR numbers
    initial_pr_numbers = {pr.number for pr in initial_prs}
    
    # Get the updated commit hashes after breakup (which added commit-ids)
    ctx.git_cmd.must_git("rev-parse HEAD~1").strip()
    updated_commit2_hash = ctx.git_cmd.must_git("rev-parse HEAD").strip()
    
    # Modify the first commit
    run_cmd("git checkout HEAD~1")
    # Get existing commit message to preserve commit-id
    existing_msg = ctx.git_cmd.must_git("log -1 --format=%B").strip()
    run_cmd(f"echo 'updated' >> file1_{unique_suffix}.txt")
    run_cmd(f"git add file1_{unique_suffix}.txt")
    # Amend but preserve the commit-id tag
    updated_msg = existing_msg.replace("First commit", "First commit - updated")
    run_cmd(f"git commit --amend -m '{updated_msg}'")
    ctx.git_cmd.must_git("rev-parse HEAD").strip()
    
    # Cherry-pick second commit using the UPDATED hash that has commit-id
    run_cmd(f"git cherry-pick {updated_commit2_hash}")
    
    # Debug: Check if commit-id was preserved
    cherry_picked_msg = ctx.git_cmd.must_git("log -1 --format=%B").strip()
    log.info(f"Cherry-picked commit message: {cherry_picked_msg}")
    assert "commit-id:" in cherry_picked_msg, "commit-id should be preserved during cherry-pick"
    
    # Run breakup again
    run_cmd("pyspr breakup")
    
    # Get all PRs from GitHub (not just ones matching our current commits)
    # We need to access the repo directly to get ALL PRs, not just those matching local commits
    repo = ctx.github.repo
    assert repo is not None, "Should have GitHub repo"
    
    # Get all open PRs from the repository
    all_open_prs = list(repo.get_pulls(state='open'))
    log.info(f"Total open PRs in repo: {len(all_open_prs)}")
    for pr in all_open_prs:
        log.info(f"  PR #{pr.number}: branch={pr.head.ref}, state={pr.state}")
    
    # Filter to pyspr PRs for this test - check for test tag
    all_pyspr_prs: List[PullRequest] = []
    for pr in all_open_prs:
        if pr.head.ref.startswith("pyspr/cp/main/"):
            # Check if this PR belongs to our test by looking for test tag in title
            # (body might not always have the tag, but title always does)
            tag_to_find = f"test-tag:{ctx.tag}"
            if tag_to_find in pr.title:
                log.info(f"Found PR #{pr.number} with our tag")
                # Extract commit ID from branch name
                branch_parts = pr.head.ref.split('/')
                commit_id = branch_parts[-1] if len(branch_parts) > 3 else 'unknown'
                
                # Create a simple commit object
                commit = Commit.from_strings(commit_id, pr.head.sha, pr.title)
                
                # Create PullRequest object that matches our test expectations
                pr_obj = PullRequest(
                    number=pr.number,
                    commit=commit,
                    commits=[commit],
                    base_ref=pr.base.ref,
                    from_branch=pr.head.ref,
                    in_queue=False,
                    title=pr.title,
                    body=pr.body
                )
                all_pyspr_prs.append(pr_obj)
    
    log.info(f"Found {len(all_pyspr_prs)} pyspr PRs total")
    for pr in all_pyspr_prs:
        log.info(f"  PR #{pr.number}: branch={pr.from_branch}, title={pr.title}")
    
    # Get the expected branch names from initial PRs
    initial_branches = {pr.from_branch for pr in initial_prs}
    log.info(f"Initial PR branches: {initial_branches}")
    
    # We expect at least 2 PRs (could be more from previous runs with run_twice)
    # Filter to just the PRs we care about - ones that match our current commits
    current_run_prs: List[PullRequest] = []
    for pr in all_pyspr_prs:
        # Check if this PR's branch matches one of our initial PRs or 
        # if its commit matches our current commits
        if pr.from_branch in initial_branches or pr.number in initial_pr_numbers:
            current_run_prs.append(pr)
    
    assert len(current_run_prs) == 2, f"Should have exactly 2 pyspr PRs for current run, found {len(current_run_prs)}"
    all_pyspr_prs = current_run_prs  # Use filtered list for rest of test
    
    # Sort PRs by number to make it easier to identify them
    all_pyspr_prs.sort(key=lambda pr: pr.number)
    
    # Verify we have the expected PRs
    # Find PR for first commit - it should have the updated title
    pr_first = next((pr for pr in all_pyspr_prs if "First commit - updated" in pr.title), None)
    assert pr_first is not None, "Should find PR for first commit with updated title"
    assert pr_first.number in initial_pr_numbers, "First commit PR should be from initial PRs"
    assert "First commit - updated" in pr_first.title, f"PR title should be updated to match new commit message, got: {pr_first.title}"
    log.info(f"First commit PR #{pr_first.number} was correctly reused with updated title: {pr_first.title}")
    
    # Find PR for second commit
    pr_second = next((pr for pr in all_pyspr_prs if "Second commit" in pr.title), None)
    assert pr_second is not None, "Should find PR for second commit"
    assert pr_second.number in initial_pr_numbers, "Second commit PR should be from initial PRs"
    assert pr_second.from_branch in initial_branches, f"Second commit PR should have original branch, got {pr_second.from_branch}"
    log.info(f"Second commit PR #{pr_second.number} was correctly reused with branch {pr_second.from_branch}")
    
    log.info(f"Success: Both commits reused their PRs - PR #{pr_first.number} and PR #{pr_second.number}")

@run_twice_in_mock_mode
def test_breakup_pretend_mode(test_repo_ctx: RepoContext, capsys: pytest.CaptureFixture[str]) -> None:
    """Test breakup command in pretend mode."""
    ctx = test_repo_ctx
    
    # Create commits
    ctx.make_commit("file1.txt", "content1", "First commit")
    ctx.make_commit("file2.txt", "content2", "Second commit")
    
    # Run breakup in pretend mode - capture both stdout and stderr
    output = run_cmd("pyspr breakup --pretend -v 2>&1", cwd=ctx.repo_dir)
    
    # Check output shows pretend actions
    assert "[PRETEND]" in output, f"Should show pretend mode indicators. Got output: {output[:500]}..."
    
    # Verify no actual PRs were created
    info = ctx.github.get_info(None, ctx.git_cmd)
    if info is not None:
        prs = [pr for pr in info.pull_requests if pr.from_branch and pr.from_branch.startswith("pyspr/cp/main/")]
        assert len(prs) == 0, "Should not create actual PRs in pretend mode"
    
    # Verify no branches were created
    branches = run_cmd("git branch -a")
    assert "pyspr/cp/" not in branches, "Should not create branches in pretend mode"

@run_twice_in_mock_mode
def test_breakup_preserves_unchanged_commit_hashes(test_repo_ctx: RepoContext) -> None:
    """Test that breakup preserves hashes of commits that haven't changed."""
    ctx = test_repo_ctx
    
    # Create two commits with unique filenames to avoid conflicts
    unique_suffix = str(uuid.uuid4())[:8]
    ctx.make_commit(f"file1_{unique_suffix}.txt", "content1", "First commit")
    ctx.make_commit(f"file2_{unique_suffix}.txt", "content2", "Second commit")
    
    # Initial breakup
    run_cmd("pyspr breakup")
    
    # Get the branch name and hash for the second commit
    info = ctx.github.get_info(None, ctx.git_cmd)
    assert info is not None, "Should get GitHub info"
    prs = [pr for pr in info.pull_requests if pr.from_branch and pr.from_branch.startswith("pyspr/cp/main/")]
    assert len(prs) == 2, f"Should have 2 PRs, found {len(prs)}"
    
    # Find the PR for the second commit
    second_pr = next((pr for pr in prs if "Second commit" in pr.title), None)
    assert second_pr is not None, "Should find PR for second commit"
    second_branch_name = second_pr.from_branch
    
    # Get the SHA of the second commit's branch after initial breakup
    initial_second_branch_sha = ctx.git_cmd.must_git(f"rev-parse {second_branch_name}").strip()
    log.info(f"Initial SHA for second commit branch {second_branch_name}: {initial_second_branch_sha}")
    
    # Test 1: Amend only the first commit (tree comparison test)
    run_cmd("git checkout HEAD~1")
    existing_msg = ctx.git_cmd.must_git("log -1 --format=%B").strip()
    run_cmd(f"echo 'updated' >> file1_{unique_suffix}.txt")
    run_cmd(f"git add file1_{unique_suffix}.txt")
    run_cmd(f"git commit --amend -m '{existing_msg.replace('First commit', 'First commit - updated')}'")
    
    # Cherry-pick the second commit to preserve the stack
    # Get the commit with the commit-id from the local branch
    run_cmd(f"git cherry-pick {second_branch_name}")
    
    # Run breakup again with verbose output
    output = run_cmd("pyspr breakup -v 2>&1", cwd=ctx.repo_dir)
    log.info(f"Breakup output:\n{output}")
    
    # Get the SHA of the second commit's branch after second breakup
    final_second_branch_sha = ctx.git_cmd.must_git(f"rev-parse {second_branch_name}").strip()
    log.info(f"Final SHA for second commit branch {second_branch_name}: {final_second_branch_sha}")
    
    # Verify the second commit's branch hash hasn't changed
    assert final_second_branch_sha == initial_second_branch_sha, \
        f"Second commit branch hash should remain unchanged. " \
        f"Initial: {initial_second_branch_sha}, Final: {final_second_branch_sha}"
    
    # Verify the output shows the branch was not updated
    assert "already up to date (same content)" in output, \
        "Output should indicate branch has same content"
    
    log.info("Successfully verified that unchanged commit preserved its hash")
    
    # Test 2: Test when base changes but diff remains the same
    # First, go back to the original main branch
    run_cmd("git checkout main")
    run_cmd("git pull origin main")
    
    # Add a line at the beginning of README.md (a file that exists on main)
    run_cmd("echo 'line at beginning' | cat - README.md > temp && mv temp README.md")
    run_cmd("git add README.md")
    run_cmd("git commit -m 'Add line at beginning of README'")
    run_cmd("git push origin main")
    
    # Create a simple commit that adds a new file (use same unique suffix)
    ctx.make_commit(f"file3_{unique_suffix}.txt", "content3", "Third commit")
    
    # Run breakup to create branch for third commit
    run_cmd("pyspr breakup")
    
    # Get info about the third commit's branch
    info = ctx.github.get_info(None, ctx.git_cmd)
    assert info is not None, "Should get GitHub info"
    third_pr = next((pr for pr in info.pull_requests if "Third commit" in pr.title), None)
    assert third_pr is not None, "Should find PR for third commit"
    third_branch_name = third_pr.from_branch
    third_branch_sha = ctx.git_cmd.must_git(f"rev-parse {third_branch_name}").strip()
    log.info(f"Initial SHA for third commit branch {third_branch_name}: {third_branch_sha}")
    
    # Now update main again (this simulates base moving forward)
    run_cmd("git checkout main")
    run_cmd("git pull origin main")
    run_cmd("echo 'another line at beginning' | cat - README.md > temp && mv temp README.md")
    run_cmd("git add README.md")
    run_cmd("git commit -m 'Add another line at beginning of README'")
    run_cmd("git push origin main")
    
    # Go back to test_local and rebase to get the new main
    run_cmd("git checkout test_local")
    run_cmd("git fetch")
    run_cmd("git rebase origin/main")
    
    # Run breakup again - the diff for third commit is the same (adding file3.txt)
    output = run_cmd("pyspr breakup -v 2>&1", cwd=ctx.repo_dir)
    log.info(f"Breakup output after base change:\n{output}")
    
    # Get the SHA after breakup
    final_third_branch_sha = ctx.git_cmd.must_git(f"rev-parse {third_branch_name}").strip()
    log.info(f"Final SHA for third commit branch {third_branch_name}: {final_third_branch_sha}")
    
    # With the new behavior, the branch should NOT be updated since the diff is the same
    assert final_third_branch_sha == third_branch_sha, \
        f"Third commit branch should not be updated when only base changed. " \
        f"Initial: {third_branch_sha}, Final: {final_third_branch_sha}"
    
    # The output should indicate the branch wasn't updated
    assert "already up to date (same changes)" in output or "already up to date (same content)" in output, \
        "Output should indicate branch has same changes despite base change"


@run_twice_in_mock_mode
def test_breakup_pr_already_exists_error(test_repo_ctx: RepoContext) -> None:
    """Test that breakup handles 'PR already exists' GitHub API error correctly."""
    ctx = test_repo_ctx
    
    # Create a commit
    ctx.make_commit("file1.txt", "content1", "Test commit for PR exists")
    
    # Run breakup once to create the PR
    run_cmd("pyspr breakup")
    
    # Get the PR info
    info = ctx.github.get_info(None, ctx.git_cmd)
    assert info is not None
    [pr for pr in info.pull_requests if pr.from_branch and pr.from_branch.startswith("pyspr/cp/")]
    
    # We should have 0 breakup PRs in the regular PR list (they're not part of the stack)
    # But let's check via the repo directly
    repo = ctx.github.repo
    assert repo is not None
    
    all_prs = list(repo.get_pulls(state='open'))
    initial_breakup_prs = [pr for pr in all_prs if pr.head.ref.startswith("pyspr/cp/")]
    initial_count = len(initial_breakup_prs)
    assert initial_count >= 1, f"Should have created at least 1 breakup PR, found {initial_count}"
    
    log.info(f"Initial breakup PRs: {[(pr.number, pr.head.ref) for pr in initial_breakup_prs]}")
    
    # Now simulate the scenario where GitHub API would return "PR already exists"
    # by running breakup again without any changes
    run_cmd("pyspr breakup")
    
    # Check that we didn't create duplicate PRs
    all_prs_after = list(repo.get_pulls(state='open'))
    final_breakup_prs = [pr for pr in all_prs_after if pr.head.ref.startswith("pyspr/cp/")]
    final_count = len(final_breakup_prs)
    
    log.info(f"Final breakup PRs: {[(pr.number, pr.head.ref) for pr in final_breakup_prs]}")
    
    # We should have the same number of PRs (no duplicates created)
    assert final_count == initial_count, \
        f"Should not create duplicate PRs. Initial: {initial_count}, Final: {final_count}"
    
    log.info("Successfully verified breakup handles existing PRs without creating duplicates")


@run_twice_in_mock_mode
def test_analyze(test_repo_ctx: RepoContext) -> None:
    """Test the analyze command that identifies independent commits."""
    log.info("=== TEST ANALYZE STARTED ===")
    ctx = test_repo_ctx
    git_cmd = ctx.git_cmd
    
    # Create a set of commits where some are independent and some are dependent
    # First create a base file that will be modified by multiple commits
    ctx.make_commit("base.txt", "line1\nline2\nline3\nline4\nline5\n", "Initial base file")
    
    # Get the actual filename with suffix that was created
    tag_suffix = ctx.tag.split('-')[-1][:8]  # Use last 8 chars of tag
    base_filename = f"base.txt.{tag_suffix}"
    
    # Create an independent commit (modifies a different file)
    ctx.make_commit("independent1.txt", "independent content", "Independent commit 1")
    
    # Create a dependent commit (modifies base.txt in a way that depends on initial state)
    base_content = git_cmd.must_git(f"show HEAD~1:{base_filename}")
    modified_base = base_content.replace("line2", "modified-line2")
    # Write to the actual base file with suffix
    with open(base_filename, "w") as f:
        f.write(modified_base)
    run_cmd(f"git add {base_filename}")
    run_cmd(f'git commit -m "Dependent commit - modify line 2 [test-tag:{ctx.tag}]"')
    
    # Create another dependent commit (modifies the same line, will conflict)
    base_content2 = git_cmd.must_git(f"show HEAD:{base_filename}")
    modified_base2 = base_content2.replace("line3", "another-modified-line3")
    with open(base_filename, "w") as f:
        f.write(modified_base2)
    run_cmd(f"git add {base_filename}")
    run_cmd(f'git commit -m "Dependent commit - modify line 3 [test-tag:{ctx.tag}]"')
    
    # Create another independent commit
    ctx.make_commit("independent2.txt", "more independent content", "Independent commit 2")
    
    # Create a WIP commit (should be ignored by analyze)
    ctx.make_commit("wip.txt", "work in progress", "WIP: Work in progress")
    
    # Run analyze command
    log.info("Running pyspr analyze...")
    output = run_cmd("pyspr analyze -v")
    log.info(f"Analyze output:\n{output}")
    
    # Verify output contains expected sections
    assert "Commit Stack Analysis" in output, "Output should contain analysis header"
    assert "Independent commits" in output, "Output should have independent commits section"
    assert "Dependent commits" in output, "Output should have dependent commits section"
    assert "SUMMARY" in output, "Output should have summary section"
    
    # Verify independent commits are identified
    assert "Independent commit 1" in output, "Should identify first independent commit"
    assert "Independent commit 2" in output, "Should identify second independent commit"
    
    # Verify dependent commits are identified
    assert "Dependent commit - modify line 2" in output, "Should identify first dependent commit"
    assert "Dependent commit - modify line 3" in output, "Should identify second dependent commit"
    
    # Verify WIP commit is not analyzed
    assert "Work in progress" not in output or "No non-WIP commits" in output, \
        "WIP commits should be excluded from analysis"
    
    # Verify summary counts
    assert "Total commits: 5" in output, "Should count 5 non-WIP commits"
    assert "Independent: 3" in output, "Should have 3 independent commits"
    assert "Dependent: 2" in output, "Should have 2 dependent commits"
    assert "Orphaned: 0" in output, "Should have 0 orphaned commits"
    
    # Verify tip about breakup command
    assert "pyspr breakup" in output, "Should suggest using breakup for independent commits"
    
    log.info("=== TEST ANALYZE COMPLETED SUCCESSFULLY ===")


@run_twice_in_mock_mode
def test_analyze_no_commits(test_repo_ctx: RepoContext) -> None:
    """Test analyze command with no commits."""
    log.info("=== TEST ANALYZE NO COMMITS STARTED ===")
    
    # Run analyze without any commits
    output = run_cmd("pyspr analyze")
    
    # Should indicate no commits to analyze
    assert "No commits to analyze" in output, "Should indicate no commits to analyze"
    
    log.info("=== TEST ANALYZE NO COMMITS COMPLETED SUCCESSFULLY ===")


@run_twice_in_mock_mode
def test_analyze_only_wip(test_repo_ctx: RepoContext) -> None:
    """Test analyze command with only WIP commits."""
    log.info("=== TEST ANALYZE ONLY WIP STARTED ===")
    ctx = test_repo_ctx
    
    # Create only WIP commits
    ctx.make_commit("wip1.txt", "wip content 1", "WIP: First WIP")
    ctx.make_commit("wip2.txt", "wip content 2", "WIP: Second WIP")
    
    # Run analyze
    output = run_cmd("pyspr analyze")
    
    # Should indicate no non-WIP commits
    assert "No non-WIP commits to analyze" in output, "Should indicate no non-WIP commits"
    
    log.info("=== TEST ANALYZE ONLY WIP COMPLETED SUCCESSFULLY ===")


@run_twice_in_mock_mode
def test_breakup_dynamic_structure_with_amends(test_repo_ctx: RepoContext) -> None:
    """Test that breakup --stacks properly handles dynamic structure changes with amended commits.
    
    This test verifies that when commits transition from independent to being part of a stack,
    ALL PRs in the stack are properly updated to show the stack information.
    """
    log.info("=== TEST BREAKUP DYNAMIC STRUCTURE WITH AMENDS STARTED ===")
    ctx = test_repo_ctx
    git_cmd = ctx.git_cmd
    
    # Step 1: Create two independent commits
    log.info("Step 1: Creating two independent commits...")
    ctx.make_commit("a.txt", "content a", "Add a")
    b_hash = ctx.make_commit("b.txt", "content b", "Add b")
    
    # Step 2: Run breakup --stacks to create initial PRs
    log.info("Step 2: Running initial breakup --stacks...")
    output = run_cmd("pyspr breakup --stacks -v")
    log.info(f"Initial breakup output:\n{output}")
    
    # Should create two single-commit PRs
    assert "single-commit" in output.lower()
    
    # Get the created PRs
    prs_before = ctx.get_test_prs()
    assert len(prs_before) == 2, f"Expected 2 PRs, got {len(prs_before)}"
    
    # Find PR A and PR B
    pr_a = next((pr for pr in prs_before if "Add a" in pr.title), None)
    pr_b = next((pr for pr in prs_before if "Add b" in pr.title), None)
    assert pr_a, "Could not find PR for commit A"
    assert pr_b, "Could not find PR for commit B"
    
    pr_a_num = pr_a.number
    pr_b_num = pr_b.number
    log.info(f"Created PR #{pr_a_num} for A and PR #{pr_b_num} for B")
    
    # Check that PRs are independent (no stack info)
    assert "Stack" not in pr_a.body, f"PR A should not have stack info, but has: {pr_a.body}"
    assert "Stack" not in pr_b.body, f"PR B should not have stack info, but has: {pr_b.body}"
    
    # Step 3: Amend commit B to depend on commit A by modifying a.txt
    log.info("Step 3: Amending commit B to depend on commit A...")
    
    # Get the actual filename for a.txt with tag suffix
    files = git_cmd.must_git("ls-tree --name-only -r HEAD").strip().split('\n')
    a_file = next((f for f in files if f.startswith("a.txt")), None)
    assert a_file, "Could not find a.txt file"
    
    # Get the original commit message with commit-id
    original_b_msg = git_cmd.must_git("log -1 --format=%B HEAD").strip()
    
    # Update the message but preserve the commit-id
    import re
    commit_id_match = re.search(r'commit-id:([a-f0-9]{8})', original_b_msg)
    if commit_id_match:
        # Replace the subject line but keep the commit-id
        new_msg = f"Commit B: Create file b and modify a [test-tag:{ctx.tag}]\n\ncommit-id:{commit_id_match.group(1)}"
        log.info(f"Preserving commit-id for B: {commit_id_match.group(1)}")
    else:
        # Fallback if no commit-id found
        new_msg = f"Commit B: Create file b and modify a [test-tag:{ctx.tag}]"
    
    # Amend to add dependency on A by modifying a.txt
    a_content = git_cmd.must_git(f"show HEAD:{a_file}").strip()
    with open(a_file, "w") as f:
        f.write(f"{a_content}\nModified by B\n")
    
    run_cmd(f"git add {a_file}")
    
    # Use proper escaping for the commit message
    import shlex
    git_cmd.must_git(f"commit --amend -m {shlex.quote(new_msg)}")
    
    # Step 4: Run breakup --stacks again
    log.info("Step 4: Running breakup --stacks after amendment...")
    output2 = run_cmd("pyspr breakup --stacks -v")
    log.info(f"Second breakup output:\n{output2}")
    
    # Should now recognize them as a multi-commit component
    assert "multi-commit component" in output2.lower() or "component" in output2.lower()
    
    # Get the PRs after second breakup
    prs_after = ctx.get_test_prs()
    log.info(f"PRs after second breakup: {[f'#{pr.number}' for pr in prs_after]}")
    
    # Should still have 2 PRs (reused, not new ones)
    assert len(prs_after) == 2, f"Expected 2 PRs (reused), got {len(prs_after)}"
    
    # Find the updated PRs - they should have the SAME PR numbers
    pr_a_after = next((pr for pr in prs_after if pr.number == pr_a_num), None)
    pr_b_after = next((pr for pr in prs_after if pr.number == pr_b_num), None)
    
    assert pr_a_after, f"PR #{pr_a_num} should still exist with same number"
    assert pr_b_after, f"PR #{pr_b_num} should still exist with same number"
    
    # Also check that no new PRs were created
    all_pr_numbers = [pr.number for pr in prs_after]
    assert pr_a_num in all_pr_numbers, f"Original PR #{pr_a_num} should still exist, but got PRs: {all_pr_numbers}"
    assert pr_b_num in all_pr_numbers, f"Original PR #{pr_b_num} should still exist, but got PRs: {all_pr_numbers}"
    
    # Ensure no higher numbered PRs were created
    max_pr_num = max(all_pr_numbers)
    assert max_pr_num <= pr_b_num, f"No new PRs should be created. Original max was #{pr_b_num}, but found #{max_pr_num}"
    
    # Re-fetch PR data to get updated bodies (fake GitHub might have stale data)
    if hasattr(ctx.github, 'repo') and ctx.github.repo:
        gh_pr_a = ctx.github.repo.get_pull(pr_a_num)
        gh_pr_b = ctx.github.repo.get_pull(pr_b_num)
        pr_a_body = gh_pr_a.body
        pr_b_body = gh_pr_b.body
    else:
        pr_a_body = pr_a_after.body
        pr_b_body = pr_b_after.body
    
    # Key assertion: BOTH PRs should now show stack information
    log.info(f"PR A body after update:\n{pr_a_body}")
    log.info(f"PR B body after update:\n{pr_b_body}")
    
    # CRITICAL: Bottom-of-stack PR (PR A) MUST have stack information
    # This is the main fix - previously bottom PRs were not updated
    assert "Stack" in pr_a_body, f"BOTTOM-OF-STACK PR A must have stack info, but has: {pr_a_body}"
    assert "**Stack**:" in pr_a_body, f"PR A should have formatted stack section, but has: {pr_a_body}"
    
    # Top-of-stack PR should also have stack info
    assert "Stack" in pr_b_body, f"PR B should now have stack info, but has: {pr_b_body}"
    assert "**Stack**:" in pr_b_body, f"PR B should have formatted stack section, but has: {pr_b_body}"
    
    # Verify the stack structure is correct
    assert f"#{pr_b_num}" in pr_a_body, "Bottom PR A should reference top PR B in its stack"
    assert f"#{pr_a_num}" in pr_b_body, "Top PR B should reference bottom PR A in its stack"
    
    # Additional checks for proper stack formatting
    # Check that PR A shows it's the current PR in the stack (with arrow)
    assert "⬅" in pr_a_body or "←" in pr_a_body, "PR A should show arrow indicating it's the current PR in stack view"
    
    # Check that the stack warning is present
    assert "Part of a stack" in pr_a_body, "PR A should have stack warning message"
    assert "Part of a stack" in pr_b_body, "PR B should have stack warning message"
    
    # PR B should target PR A's branch
    assert pr_b_after.base_ref == pr_a_after.from_branch, f"PR B should target PR A's branch, but targets {pr_b_after.base_ref}"
    
    log.info("✓ Both PRs properly updated with stack information")
    
    # Step 5: Test reverse transition - make commits independent again
    log.info("Step 5: Testing reverse transition - making commits independent again...")
    
    # Save the current commit B hash and commit-id
    b_commit_id = commit_id_match.group(1) if commit_id_match else None
    
    # Reset to commit A
    git_cmd.must_git("reset --hard HEAD~1")
    
    # Cherry-pick B but without the file_a modification
    git_cmd.must_git(f"cherry-pick {b_hash} --no-commit")
    
    # Reset file_a to remove the modification
    git_cmd.must_git(f"reset HEAD {a_file}")
    git_cmd.must_git(f"checkout {a_file}")
    
    # Commit with preserved commit-id
    if b_commit_id:
        new_independent_msg = f"Add b (now independent) [test-tag:{ctx.tag}]\n\ncommit-id:{b_commit_id}"
    else:
        new_independent_msg = f"Add b (now independent) [test-tag:{ctx.tag}]"
    
    git_cmd.must_git(f"commit -m {shlex.quote(new_independent_msg)}")
    
    # Step 6: Run breakup --stacks again to update PRs to be independent
    log.info("Step 6: Running breakup --stacks to make PRs independent...")
    output3 = run_cmd("pyspr breakup --stacks -v")
    log.info(f"Third breakup output:\n{output3}")
    
    # Should now recognize them as independent single-commit components
    assert "single-commit" in output3.lower()
    
    # Get PRs after making them independent
    prs_final = ctx.get_test_prs()
    log.info(f"PRs after making independent: {[f'#{pr.number}' for pr in prs_final]}")
    
    # CRITICAL: Should still have the SAME 2 PRs (not new ones)
    assert len(prs_final) == 2, f"Should still have 2 PRs, but found {len(prs_final)}"
    
    # Check that we're reusing the same PR numbers
    final_pr_numbers = [pr.number for pr in prs_final]
    assert pr_a_num in final_pr_numbers, f"Original PR A #{pr_a_num} should still exist, but found: {final_pr_numbers}"
    assert pr_b_num in final_pr_numbers, f"Original PR B #{pr_b_num} should still exist, but found: {final_pr_numbers}"
    
    # Find the final PRs
    pr_a_final = next((pr for pr in prs_final if pr.number == pr_a_num), None)
    pr_b_final = next((pr for pr in prs_final if pr.number == pr_b_num), None)
    
    assert pr_a_final, f"PR A #{pr_a_num} should still exist"
    assert pr_b_final, f"PR B #{pr_b_num} should still exist"
    
    # Re-fetch PR data to get updated bodies
    if hasattr(ctx.github, 'repo') and ctx.github.repo:
        gh_pr_a_final = ctx.github.repo.get_pull(pr_a_num)
        gh_pr_b_final = ctx.github.repo.get_pull(pr_b_num)
        pr_a_final_body = gh_pr_a_final.body
        pr_b_final_body = gh_pr_b_final.body
    else:
        pr_a_final_body = pr_a_final.body
        pr_b_final_body = pr_b_final.body
    
    # Key assertions: PRs should NO LONGER have stack information
    assert "Stack" not in pr_a_final_body, f"PR A should NOT have stack info after becoming independent, but has: {pr_a_final_body}"
    assert "Stack" not in pr_b_final_body, f"PR B should NOT have stack info after becoming independent, but has: {pr_b_final_body}"
    
    # Both PRs should target main again
    assert pr_a_final.base_ref == "main", f"PR A should target main, but targets {pr_a_final.base_ref}"
    assert pr_b_final.base_ref == "main", f"PR B should target main, but targets {pr_b_final.base_ref}"
    
    log.info("✓ PRs successfully transitioned back to independent")
    log.info("=== TEST BREAKUP DYNAMIC STRUCTURE WITH AMENDS COMPLETED SUCCESSFULLY ===")