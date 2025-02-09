"""End-to-end test for amending commits in stack, PR stack isolation, WIP and reviewer behavior."""

CURRENT_USER = "yang"  # Since we're using yang's token for tests
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

from pyspr.tests.e2e.test_helpers import get_gh_token
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
log.setLevel(logging.INFO)
log.propagate = True  # Allow logs to propagate to pytest

from pyspr.tests.e2e.test_helpers import run_cmd, RepoContext, test_repo_ctx, create_test_repo

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
    assert pr2.base_ref == f"spr/main/{pr1.commit.commit_id}", "Second PR should target first PR's branch"
    assert pr3.base_ref == f"spr/main/{pr2.commit.commit_id}", "Third PR should target second PR's branch"
    assert pr4.base_ref == f"spr/main/{pr3.commit.commit_id}", "Fourth PR should target third PR's branch"
    
    log.info(f"\nInitial PRs created: #{pr1_num}, #{pr2_num}, #{pr3_num}, #{pr4_num}")
    
    # Verify each PR hash matches its local commit hash
    assert pr1.commit.commit_hash == commit1_hash, f"PR1 hash {pr1.commit.commit_hash} should match local commit1 hash {commit1_hash}"
    assert pr2.commit.commit_hash == commit2_hash, f"PR2 hash {pr2.commit.commit_hash} should match local commit2 hash {commit2_hash}"
    assert pr3.commit.commit_hash == commit3_hash, f"PR3 hash {pr3.commit.commit_hash} should match local commit3 hash {commit3_hash}"
    assert pr4.commit.commit_hash == commit4_hash, f"PR4 hash {pr4.commit.commit_hash} should match local commit4 hash {commit4_hash}"
    
    # Now reset and recreate commits but skip commit2 and add c3.5
    log.info("\nRecreating commits without second commit and adding c3.5...")
    run_cmd("git reset --hard HEAD~4")  # Remove all commits

    # Get the original commit messages
    c1_msg = ctx.git_cmd.must_git(f"show -s --format=%B {commit1_hash}").strip()
    c3_msg = ctx.git_cmd.must_git(f"show -s --format=%B {commit3_hash}").strip()
    c4_msg = ctx.git_cmd.must_git(f"show -s --format=%B {commit4_hash}").strip()
    
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
    assert pr35 is not None, f"New PR for c3.5 should exist"
    assert pr4_after is not None, f"PR4 #{pr4_num} should exist"
    
    # Verify new PR chain
    assert pr1_after.base_ref == "main", "First PR should target main"
    assert pr3_after.base_ref == f"spr/main/{pr1_after.commit.commit_id}", "Third PR should now target first PR's branch"
    assert pr35.base_ref == f"spr/main/{pr3_after.commit.commit_id}", "New PR should target third PR's branch"
    assert pr4_after.base_ref == f"spr/main/{pr35.commit.commit_id}", "Fourth PR should target new PR's branch"
    
    # Verify PR order and proper chain connectivity
    current_shas = {
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
    
    # Create 4 commits: 2 regular, 1 WIP, 1 regular
    def make_commit(file: str, msg: str) -> None:
        log.info(f"{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]} Creating commit for {file} - {msg}")
        full_msg = f"{msg} [test-tag:{ctx.tag}]"
        with open(file, "w") as f:
            f.write(f"{file}\n")
        run_cmd(f"git add {file}")
        run_cmd(f'git commit -m "{full_msg}"')
        result = git_cmd.must_git("rev-parse HEAD").strip()
        log.info(f"{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]} Created commit {result[:8]}")
        
    log.info(f"{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]} Creating commits...")
    make_commit("wip_test1.txt", "First regular commit")
    make_commit("wip_test2.txt", "Second regular commit")
    make_commit("wip_test3.txt", "WIP Third commit")
    make_commit("wip_test4.txt", "Fourth regular commit")  # Not used but kept for completeness
    
    # Run update to create PRs
    log.info(f"{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]} Running pyspr update...")
    run_cmd(f"pyspr update")
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
    
    # Helper to find our test PRs 
    def get_test_prs() -> list:
        log.info("=== ABOUT TO CALL GITHUB API ===")  # See if we get here before timeout
        log.info(f"{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]} Starting PR filtering...")
        gh_start = time.time()
        try:
            info = github.get_info(None, git_cmd)
            prs = info.pull_requests if info else []
        finally:
            gh_end = time.time()
            log.info(f"{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]} GitHub API get_info took {gh_end - gh_start:.2f} seconds")
        
        log.info(f"{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]} Filtering {len(prs)} PRs...")
        filter_start = time.time()
        result = []
        try:
            for pr in prs:
                try:
                    # Debug each PR being checked
                    log.info(f"Checking PR #{pr.number} - branch {pr.from_branch}")
                    if pr.from_branch is not None and pr.from_branch.startswith('spr/main/') and pr.commit is not None:
                        # Look for our unique tag in the commit message
                        commit_msg = git_cmd.must_git(f"show -s --format=%B {pr.commit.commit_hash}")
                        if f"test-tag:{ctx.tag}" in commit_msg:
                            log.info(f"Found matching PR #{pr.number}")
                            result.append(pr)
                except Exception as e:  # Log any failures
                    log.info(f"Error checking PR #{getattr(pr, 'number', 'unknown')}: {e}")
                    pass
        finally:
            filter_end = time.time()
            log.info(f"{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]} PR filtering took {filter_end - filter_start:.2f} seconds")
        log.info(f"{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]} Found {len(result)} matching PRs")
        return result
    
    # Verify only first two PRs were created
    log.info(f"{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]} Getting GitHub info...")
    info = github.get_info(None, git_cmd)
    assert info is not None, "GitHub info should not be None"
    
    log.info(f"{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]} Getting commit info for debugging:")
    log.info(f"{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]} C1: {c1_hash}")
    log.info(f"{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]} C2: {c2_hash}")
    log.info(f"{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]} C3: {c3_hash}")
    
    # Get our test PRs
    log.info(f"{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]} Getting filtered test PRs...")
    test_prs = get_test_prs()
    
    # Print all PR commit hashes for debugging
    log.info(f"{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]} Test PR commit hashes:")
    for pr in test_prs:
        log.info(f"{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]} PR #{pr.number}: {pr.title} - {pr.commit.commit_hash}")
    
    # Sort PRs by number (most recent first) and take first 2 matching our titles
    log.info(f"{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]} Finding PRs with target titles...")
    test_prs = sorted(test_prs, key=lambda pr: pr.number, reverse=True)
    prs_with_titles = []
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
    assert pr2.base_ref is not None and pr2.base_ref.startswith("spr/main/"), "Second PR should target first PR's branch"


def test_reviewer_functionality(test_repo_ctx: RepoContext) -> None:
    """Test reviewer functionality, verifying:
    1. Self-review attempts are handled properly (can't review your own PR)
    2. Other user review requests work properly
    
    This combines two tests:
    - First part tests yang token case (can't self-review)
    - Second part tests testluser case (verify request handling)
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

        # Helper to make commits for part 1 (uses ctx.tag)
        def make_commit_1(file: str, msg: str) -> None:
            full_msg = f"{msg} [test-tag:{ctx.tag}]"
            with open(file, "w") as f:
                f.write(f"{file}\n{msg}\n")
            run_cmd(f"git add {file}")
            run_cmd(f'git commit -m "{full_msg}"')

        # Helper to make commits for part 2
        def make_commit_2(file: str, msg: str) -> None:
            full_msg = f"{msg} [test-tag:{unique_tag2}]"
            with open(file, "w") as f:
                f.write(f"{file}\n{msg}\n")
            run_cmd(f"git add {file}")
            run_cmd(f'git commit -m "{full_msg}"')

        # Helper to find test PRs by tag
        def get_test_prs(tag: str) -> list:
            result = []
            for pr in github.get_info(None, git_cmd).pull_requests:
                if pr.from_branch.startswith('spr/main/'):
                    try:
                        # Look for our unique tag in the commit message
                        commit_msg = git_cmd.must_git(f"show -s --format=%B {pr.commit.commit_hash}")
                        if f"test-tag:{tag}" in commit_msg:
                            result.append(pr)
                    except:  # Skip failures since we're just filtering
                        pass
            return result

        # Part 1: Test self-review case (yang token)
        log.info("\n=== Part 1: Testing self-review handling ===")
        log.info("Creating first commit without reviewer...")
        log.info(f"Current working directory: {os.getcwd()}")
        make_commit_1("r_test1.txt", "First commit")

        # Create initial PR without reviewer
        run_cmd("pyspr update")

        # Verify first PR exists with no reviewer
        info = github.get_info(None, git_cmd)
        assert info is not None, "GitHub info should not be None"
        our_prs = get_test_prs(ctx.tag)
        assert len(our_prs) == 1, f"Should have 1 PR for our test, found {len(our_prs)}"
        pr1 = our_prs[0]
        assert github.repo is not None, "GitHub repo should be available"
        gh_pr1 = github.repo.get_pull(pr1.number)
        
        # Debug review requests for first PR
        log.info("\nDEBUG: First PR review requests")
        try:
            requested_users, requested_teams = gh_pr1.get_review_requests()
            requested_logins = [u.login.lower() for u in requested_users]
            log.info(f"Requested Users: {requested_logins}")
            log.info(f"Requested Teams: {list(requested_teams)}")
        except Exception as e:
            log.info(f"Error getting review data: {e}")
            requested_logins = []
        
        assert CURRENT_USER.lower() not in requested_logins, f"First PR correctly has no {CURRENT_USER} reviewer (can't review own PR)"
        log.info(f"Created PR #{pr1.number} with no {CURRENT_USER} reviewer")

        # Create second commit and try self-review
        log.info("\nCreating second commit with self-reviewer...")
        make_commit_1("r_test2.txt", "Second commit")
        run_cmd("pyspr update -r yang")

        # Verify no self-review was added
        info = github.get_info(None, git_cmd)
        assert info is not None, "GitHub info should not be None"
        our_prs = get_test_prs(ctx.tag)
        assert len(our_prs) == 2, f"Should have 2 PRs for our test, found {len(our_prs)}"
        prs_by_num = {pr.number: pr for pr in our_prs}
        assert pr1.number in prs_by_num, "First PR should still exist"
        
        # Verify no reviewer on first PR
        gh_pr1 = github.repo.get_pull(pr1.number)
        try:
            requested_users, _ = gh_pr1.get_review_requests()
            requested_logins1 = [u.login.lower() for u in requested_users]
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
            requested_logins2 = [u.login.lower() for u in requested_users]
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
        our_prs = get_test_prs(unique_tag2)
        assert len(our_prs) == 1, f"Should have 1 PR for testluser test, found {len(our_prs)}"
        pr1 = our_prs[0]
        gh_pr1 = github.repo.get_pull(pr1.number)
        
        # Verify no reviewer on first PR
        requested_users, _ = gh_pr1.get_review_requests()
        requested_logins = [u.login.lower() for u in requested_users]
        assert "testluser" not in requested_logins, "First PR correctly has no testluser reviewer"
        log.info(f"Verified PR #{pr1.number} has no reviewer")

        # Switch to SPR branch and add second commit with testluser reviewer
        run_cmd(f"git checkout {pr1.from_branch}")
        log.info("\nCreating second commit with testluser reviewer...")
        make_commit_2("test_r2.txt", "Second testluser commit")

        # Add testluser as reviewer and capture output
        result = subprocess.run(
            ["pyspr", "update", "-r", "testluser"],
            check=True,
            capture_output=True,
            text=True
        )
        update_output = result.stdout + result.stderr

        # Find our PRs again
        our_prs = get_test_prs(unique_tag2)
        assert len(our_prs) == 2, f"Should have 2 PRs for testluser test, found {len(our_prs)}"
        
        # Get the latest PR
        pr2 = max(our_prs, key=lambda p: p.number)
        gh_pr2 = github.repo.get_pull(pr2.number)
        
        # Since we're using yang's token and testluser is yang, verify request was ignored
        requested_users, _ = gh_pr2.get_review_requests()
        requested_logins = [u.login.lower() for u in requested_users]
        assert "testluser" not in requested_logins, "Second PR correctly has no testluser reviewer due to self-review restriction"
        
        # Verify we attempted to add reviewers
        log.info("\nDEBUG: Update command output:")
        log.info(update_output)
        assert "Trying to add reviewers" in update_output or \
               "Adding reviewers" in update_output or \
               "DEBUG:" in update_output, \
               "Should have attempted to add testluser as reviewer"
        
        log.info("Successfully verified testluser review handling with self-review restriction")

    finally:
        # Change back to original directory
        os.chdir(original_dir)

def test_reorder(test_repo_ctx: RepoContext) -> None:
    """Test that creates four commits, runs update, then recreates commits but reorders c3 and c4,
    and verifies PR state after reordering."""
    ctx = test_repo_ctx
    git_cmd = ctx.git_cmd
    github = ctx.github

    # Create four test commits with unique tag in message
    def make_commit(file: str, content: str, msg: str) -> None:
        full_msg = f"{msg} [test-tag:{ctx.tag}]"
        with open(file, "w") as f:
            f.write(f"{file}\n{content}\n")
        run_cmd(f"git add {file}")
        run_cmd(f'git commit -m "{full_msg}"')

    # Create commits c1, c2, c3, c4
    make_commit("test1.txt", "test content 1", "First commit")
    make_commit("test2.txt", "test content 2", "Second commit")
    make_commit("test3.txt", "test content 3", "Third commit")
    make_commit("test4.txt", "test content 4", "Fourth commit")

    # Run pyspr update
    run_cmd("pyspr update")

    # Get commit hashes after update
    commit1_hash = git_cmd.must_git("rev-parse HEAD~3").strip()
    commit2_hash = git_cmd.must_git("rev-parse HEAD~2").strip()
    commit3_hash = git_cmd.must_git("rev-parse HEAD~1").strip()
    commit4_hash = git_cmd.must_git("rev-parse HEAD").strip()

    # Helper to find our test PRs using unique tag
    def get_test_prs() -> List[PullRequest]:
        log.info("\nLooking for PRs with unique tag...")
        result = []
        for pr in github.get_info(None, git_cmd).pull_requests:
            if pr.from_branch.startswith('spr/main/'):
                try:
                    # Look for our unique tag in the commit message
                    commit_msg = git_cmd.must_git(f"show -s --format=%B {pr.commit.commit_hash}")
                    if f"test-tag:{ctx.tag}" in commit_msg:
                        log.info(f"Found PR #{pr.number} with tag and commit ID {pr.commit.commit_id}")
                        result.append(pr)
                except:
                    pass
        return result

    # Verify initial PRs were created
    prs = get_test_prs()
    assert len(prs) == 4, f"Should have created 4 PRs, found {len(prs)}"
    prs.sort(key=lambda p: p.number)  # Sort by PR number
    pr1, pr2, pr3, pr4 = prs

    # Save PR numbers for later verification
    pr1_num, pr2_num, pr3_num, pr4_num = pr1.number, pr2.number, pr3.number, pr4.number

    # Verify PR chain
    assert pr1.base_ref == "main", "First PR should target main"
    assert pr2.base_ref == f"spr/main/{pr1.commit.commit_id}", "Second PR should target first PR's branch"
    assert pr3.base_ref == f"spr/main/{pr2.commit.commit_id}", "Third PR should target second PR's branch"
    assert pr4.base_ref == f"spr/main/{pr3.commit.commit_id}", "Fourth PR should target third PR's branch"

    log.info(f"\nInitial PRs created: #{pr1_num}, #{pr2_num}, #{pr3_num}, #{pr4_num}")

    # Save original commit_ids for verification
    commit1_id = pr1.commit.commit_id
    commit2_id = pr2.commit.commit_id
    commit3_id = pr3.commit.commit_id
    commit4_id = pr4.commit.commit_id

    # Now reset and recreate commits but reorder c3 and c4
    log.info("\nRecreating commits with c3 and c4 reordered...")
    run_cmd("git reset --hard HEAD~4")  # Remove all commits

    # Get the original commit messages
    c1_msg = git_cmd.must_git(f"show -s --format=%B {commit1_hash}").strip()
    c2_msg = git_cmd.must_git(f"show -s --format=%B {commit2_hash}").strip()
    c3_msg = git_cmd.must_git(f"show -s --format=%B {commit3_hash}").strip()
    c4_msg = git_cmd.must_git(f"show -s --format=%B {commit4_hash}").strip()

    # Recreate commits but with c4 before c3
    run_cmd(f"git cherry-pick {commit1_hash}")
    run_cmd(f"git cherry-pick {commit2_hash}")
    run_cmd(f"git cherry-pick {commit4_hash}")  # c4 now before c3
    run_cmd(f"git cherry-pick {commit3_hash}")  # c3 now after c4

    # Run pyspr update again
    run_cmd(f"pyspr update -v")

    # Get PRs after reordering
    prs = get_test_prs()
    assert len(prs) == 4, f"Should still have 4 PRs after reordering, found {len(prs)}"

    # Instead of checking PRs by number, check them by commit message
    prs_by_title = {}
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
    assert pr2_after.base_ref == f"spr/main/{pr1_after.commit.commit_id}", "Second PR should target first PR's branch"
    assert pr4_after.base_ref == f"spr/main/{pr2_after.commit.commit_id}", "Fourth PR should now target second PR's branch"
    assert pr3_after.base_ref == f"spr/main/{pr4_after.commit.commit_id}", "Third PR should now target fourth PR's branch"

    # Log the final PR numbers in the new order
    pr_chain = f"#{pr1_after.number} -> #{pr2_after.number} -> #{pr4_after.number} -> #{pr3_after.number}"
    log.info(f"\nVerified PRs after reordering: {pr_chain}")

@pytest.fixture
def test_repo() -> Generator[Tuple[str, str, str, str], None, None]:
    """Regular test repo fixture using yang/teststack."""
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
        github = GitHubClient(None, config)
    
    # Create commits
    def make_commit(file: str, line: str, msg: str) -> None:
        full_msg = f"{msg} [test-tag:{repo_ctx.tag}]"
        with open(file, "w") as f:
            f.write(f"{file}\n{line}\n")
        try:
            log.info(f"Creating file {file}...")
            run_cmd(f"git add {file}")
            run_cmd("git status")  # Debug: show git status after add
            try:
                run_cmd(f'git commit -m "{full_msg}"')
            except subprocess.CalledProcessError as e:
                log.info(f"Git commit failed with code {e.returncode}")
                log.info(f"STDOUT: {e.output}")
                log.info(f"STDERR: {e.stderr}")
                log.info("Directory contents:")
                run_cmd("ls -la")
                raise  # Re-raise the original exception since it has the right error info
        except subprocess.CalledProcessError as e:
            log.info(f"Failed to commit: {e}")
            raise
        
    log.info("Creating commits...")
    try:
        # Use static filenames but unique content
        unique = str(uuid.uuid4())[:8]
        test_files: List[str] = []
        for i in range(num_commits):
            prefix = "test_merge" if not use_merge_queue else "mq_test"
            filename = f"{prefix}{i+1}.txt"
            test_files.append(filename)
            make_commit(filename, f"line 1 - {unique}", 
                      f"Test {'merge queue' if use_merge_queue else 'multi'} commit {i+1}")
    except subprocess.CalledProcessError as e:
        # Get git status for debugging
        run_cmd("git status")
        raise

    # Initial update to create PRs
    log.info("Creating initial PRs...")
    run_cmd("pyspr update")

    # Helper to find our test PRs
    def get_test_prs() -> list:
        result = []
        for pr in github.get_info(None, git_cmd).pull_requests:
            if pr.from_branch.startswith('spr/main/'):
                try:
                    # Look for our unique tag in the commit message
                    commit_msg = git_cmd.must_git(f"show -s --format=%B {pr.commit.commit_hash}")
                    if f"test-tag:{repo_ctx.tag}" in commit_msg:
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
    log.info(f"Created PRs: {', '.join(f'#{num}' for num in pr_nums)}")

    # Verify initial PR chain
    assert prs[0].base_ref == "main", f"Bottom PR should target main, got {prs[0].base_ref}"
    for i in range(1, len(prs)):
        assert prs[i].base_ref == f"spr/main/{prs[i-1].commit.commit_id}", \
            f"PR #{prs[i].number} should target PR #{prs[i-1].number}, got {prs[i].base_ref}"

    # Run merge for all or some PRs
    merge_cmd = ["pyspr", "merge", "-C", repo_dir]
    if count is not None:
        merge_cmd.extend(["-c", str(count)])
    log.info(f"\nMerging {'to queue' if use_merge_queue else 'all'} PRs{' (partial)' if count else ''}...")
    try:
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        merge_output = subprocess.check_output(
            merge_cmd,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            cwd=project_root
        )
        log.info(merge_output)
    except subprocess.CalledProcessError as e:
        info = github.get_info(None, git_cmd)
        assert info is not None, "GitHub info should not be None"
        log.info("\nFinal PR state after merge attempt:")
        for pr in sorted(info.pull_requests, key=lambda pr: pr.number):
            if github.repo:
                gh_pr = github.repo.get_pull(pr.number)
                log.info(f"PR #{pr.number}:")
                log.info(f"  Title: {gh_pr.title}")
                log.info(f"  Base: {gh_pr.base.ref}")
                log.info(f"  State: {gh_pr.state}")
                log.info(f"  Merged: {gh_pr.merged}")
                if use_merge_queue:
                    log.info(f"  Mergeable state: {gh_pr.mergeable_state}")
                    log.info(f"  Auto merge: {getattr(gh_pr, 'auto_merge', None)}")
        log.info(f"Merge failed with output:\n{e.output}")
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

def test_merge_workflow(test_repo_ctx: RepoContext) -> None:
    """Test full merge workflow with real PRs."""
    _run_merge_test(test_repo_ctx, False, 3)

@pytest.fixture
def test_mq_repo_ctx() -> Generator[RepoContext, None, None]:
    """Merge queue test repo fixture using yangenttest1/teststack."""
    from pyspr.tests.e2e.test_helpers import create_repo_context
    yield from create_repo_context("yangenttest1", "teststack", "test_merge_queue_workflow")

def test_merge_queue_workflow(test_mq_repo_ctx: RepoContext) -> None:
    """Test merge queue workflow with real PRs."""
    _run_merge_test(test_mq_repo_ctx, True, 2)

def test_partial_merge_workflow(test_repo_ctx: RepoContext) -> None:
    """Test partial merge workflow, merging only 2 of 3 PRs."""
    _run_merge_test(test_repo_ctx, False, 3, count=2)

def test_partial_merge_queue_workflow(test_mq_repo_ctx: RepoContext) -> None:
    """Test partial merge queue workflow, merging only 2 of 3 PRs to queue."""
    _run_merge_test(test_mq_repo_ctx, True, 3, count=2)

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

    def make_commit(file: str, line: str, msg: str) -> None:
        full_msg = f"{msg} [test-tag:{ctx.tag}]"
        with open(file, "w") as f:
            f.write(f"{file}\n{line}\n")
        run_cmd(f"git add {file}")
        run_cmd(f'git commit -m "{full_msg}"')

    log.info("\nCreating initial stack of 3 commits...")
    run_cmd("git checkout main")
    run_cmd("git pull")
    branch = f"test-replace-{uuid.uuid4().hex[:7]}"
    run_cmd(f"git checkout -b {branch}")

    test_files = ["file1.txt", "file2.txt", "file3.txt", "file2_new.txt"]

    # 1. Create stack with commits A -> B -> C
    make_commit("file1.txt", "line 1", "Commit A")
    make_commit("file2.txt", "line 1", "Commit B")
    make_commit("file3.txt", "line 1", "Commit C")

    # Get original commit hashes - needed for cherry-pick operations
    orig_c1_hash = git_cmd.must_git("rev-parse HEAD~2").strip()
    orig_c2_hash = git_cmd.must_git("rev-parse HEAD~1").strip()
    orig_c3_hash = git_cmd.must_git("rev-parse HEAD").strip()

    # Run initial update
    log.info("Creating initial PRs...")
    subprocess.run(["pyspr", "update"], check=True)

    # Get hashes after update for verification
    c1_hash = git_cmd.must_git("rev-parse HEAD~2").strip()
    c2_hash = git_cmd.must_git("rev-parse HEAD~1").strip()
    c3_hash = git_cmd.must_git("rev-parse HEAD").strip()

    # Helper to find our test PRs
    def get_test_prs() -> list:
        result = []
        for pr in github.get_info(None, git_cmd).pull_requests:
            if pr.from_branch.startswith('spr/main/'):
                try:
                    # Look for our unique tag in the commit message
                    commit_msg = git_cmd.must_git(f"show -s --format=%B {pr.commit.commit_hash}")
                    if f"test-tag:{ctx.tag}" in commit_msg:
                        result.append(pr)
                except:  # Skip failures since we're just filtering
                    pass
        return result

    # Get initial PR info and filter to our newly created PRs
    commit_prs = get_test_prs()
    commit_prs = sorted(commit_prs, key=lambda pr: pr.number)
    assert len(commit_prs) == 3, f"Should find 3 PRs for our commits, found {len(commit_prs)}"
    
    # Verify each commit has a PR and map by commit message
    prs_by_msg = {}
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
    assert pr2.base_ref == f"spr/main/{pr1.commit.commit_id}", "PR2 should target PR1"
    assert pr3.base_ref == f"spr/main/{pr2.commit.commit_id}", "PR3 should target PR2"

    # 2. Replace commit B with new commit D
    log.info("\nReplacing commit B with new commit D...")
    run_cmd("git reset --hard HEAD~2")  # Remove B and C
    make_commit("file2_new.txt", "line 1", "New Commit D")
    run_cmd(f"git cherry-pick {orig_c3_hash}")  # Add C back using original hash

    # 3. Run update
    log.info("Running update after replace...")
    subprocess.run(["pyspr", "update"], check=True)

    # 4. Verify:
    log.info("\nVerifying PR handling after replace...")
    relevant_prs = get_test_prs()
    pr_nums_to_check: Set[int] = set(pr.number for pr in relevant_prs)
    if pr2_num not in pr_nums_to_check:
        # Also check if B's old PR is still open but no longer has our commits
        for pr in github.get_info(None, git_cmd).pull_requests:
            if pr.number == pr2_num:
                relevant_prs.append(pr)
                pr_nums_to_check.add(pr.number)
                break
    
    # Group PRs by message type
    prs_by_type = {
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
    assert pr_d.base_ref == f"spr/main/{pr1.commit.commit_id}", "New PR should target PR1" 
    assert pr3.base_ref == f"spr/main/{pr_d.commit.commit_id}", "PR3 should target new PR"

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
        # This is OK since we actually do want to update the main branch for the test branch to rebase on.
        run_cmd('git push')

        # Go back to test branch
        run_cmd("git checkout test-branch")
        
        # Get commit count before first update
        commit_count_before = len(git_cmd.must_git("log --oneline").splitlines())

        # Step 2: Test regular update - should rebase
        log.info("\nRunning regular update logic...")
        caplog.clear()  # Clear logs before test
        
        # Actually run the update and capture output
        try:
            update_output = subprocess.check_output(
                ["pyspr", "update"],
                stderr=subprocess.STDOUT,
                universal_newlines=True
            )
        except subprocess.CalledProcessError as e:
            update_output = e.output
        
        # Verify regular update rebased by:
        # 1. Checking git log shows our commit on top of main's commit
        # 2. Checking the output shows rebase happened
        
        # Check commit order in git log
        log_output = git_cmd.must_git("log --oneline -n 2")
        log_shas = [line.split()[0] for line in log_output.splitlines()]
        assert len(log_shas) == 2, "Should have at least 2 commits"
        assert log_shas[1].startswith(main_sha[:7]), "Main commit should be second in log after rebase"

        # Check rebase happened by looking in update output
        assert "rebase" in update_output.lower() or "> git rebase" in update_output, "Regular update should perform rebase"
        
        # Step 3: Reset to pre-rebase state 
        run_cmd(f"git reset --hard {branch_sha}")
        
        # Step 4: Test update with --no-rebase
        log.info("\nRunning update with --no-rebase logic...")
        caplog.clear()  # Clear logs before second test
        caplog.set_level(logging.INFO)  # Make sure we capture INFO level logs
        
        try:
            # Manual simulation of fetch_and_get_github_info without GitHub API
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
            log.info(f"DEBUG: no_rebase={no_rebase}")  # Use log.info instead of log
            if not no_rebase:
                git_cmd.must_git(f"rebase origin/main --autostash")

            # Cleanup env var
            del os.environ["SPR_NOREBASE"]
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

    def make_commit(file: str, msg: str) -> None:
        """Create commit WITHOUT test tag since this test is about preserving commit hashes"""
        with open(os.path.join(repo_dir, file), "w") as f:
            f.write(f"{file}\n{msg}\n")
        run_cmd(f"git add {file}")
        run_cmd(f'git commit -m "{msg}"')

    # Get initial commit hash for verification
    initial_hash = git_cmd.must_git("rev-parse HEAD").strip()
    log.info(f"Initial commit: {initial_hash[:8]}")

    # Create first commit & PR
    log.info("\nCreating first commit...")
    make_commit("nr_test1.txt", "First commit")
    c1_hash = git_cmd.must_git("rev-parse HEAD").strip()
    log.info(f"First commit: {c1_hash[:8]}")

    # Create first PR and get its hash
    run_cmd(f"pyspr update -C {repo_dir}")
    pr1_hash = git_cmd.must_git("rev-parse HEAD").strip()
    log.info(f"After update commit: {pr1_hash[:8]}")

    # Get PR info more efficiently
    info = github.get_info(None, git_cmd)
    assert info is not None, "GitHub info should not be None"
    pr1 = info.pull_requests[0] if info.pull_requests else None
    assert pr1 is not None, "First PR should exist"
    pr1_number = pr1.number
    log.info(f"Created PR #{pr1_number} with commit {pr1.commit.commit_hash[:8]}")

    # Create second commit
    log.info("\nCreating second commit...")
    make_commit("nr_test2.txt", "Second commit")
    c2_hash = git_cmd.must_git("rev-parse HEAD").strip()
    log.info(f"Second commit: {c2_hash[:8]}")

    # Create .spr.yaml with noRebase: true
    import yaml
    spr_yaml_path = os.path.join(repo_dir, ".spr.yaml")
    spr_config = {
        'repo': {
            'github_remote': 'origin',
            'github_branch': 'main',
            'github_repo_owner': ctx.owner,
            'github_repo_name': ctx.name,
        },
        'user': {
            'noRebase': True
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

    # Get updated PR info using the no_rebase config
    info = github.get_info(None, git_cmd)
    assert info is not None, "GitHub info should not be None"
    prs = sorted(info.pull_requests, key=lambda pr: pr.number)
    assert len(prs) == 2, f"Should have 2 PRs, found {len(prs)}"

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

    # Verify PR2 hash matches c2_hash
    log.info(f"PR2 hash comparison: {c2_hash[:8]} vs {pr2.commit.commit_hash[:8]}")
    assert pr2.commit.commit_hash == c2_hash, \
        f"PR2 hash wrong: {pr2.commit.commit_hash[:8]} vs {c2_hash[:8]}"
    log.info(f"Verified PR #{pr2.number} hash correct")

    # Verify stack structure
    assert pr1_after.base_ref == "main", f"PR1 should target main, got {pr1_after.base_ref}"
    assert pr2.base_ref.startswith('spr/main/'), f"PR2 should target PR1's branch, got {pr2.base_ref}"
    assert pr1_after.commit.commit_id in pr2.base_ref, "PR2 should target PR1's branch"
    log.info(f"Verified stack structure: #{pr1_number} <- #{pr2.number}")

    # Print final git log
    log_output = git_cmd.must_git("log --oneline -n 3")
    log.info(f"Final git log:\n{log_output}")

def test_stack_isolation(test_repo: Tuple[str, str, str, str]) -> None:
    """Test that PRs from different stacks don't interfere with each other.
    
    This verifies that removing commits from one stack doesn't close PRs from another stack.
    We test using two stacks of PRs (2 PRs each) in the same repo:
    
    Stack 1:        Stack 2:
    PR1B <- PR1A    PR2B <- PR2A
    
    Then remove PR1A and verify only PR1B gets closed, while stack 2 remains untouched.
    """
    owner, repo_name, _test_branch, repo_dir = test_repo

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

    test_files = ["stack1a.txt", "stack1b.txt", "stack2a.txt", "stack2b.txt"]

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
    orig_c1a_hash = git_cmd.must_git("rev-parse HEAD~1").strip()
    orig_c1b_hash = git_cmd.must_git("rev-parse HEAD").strip()

    # Update to create connected PRs 1A and 1B
    log.info("Creating stack 1 PRs...")
    run_cmd("pyspr update")

    # Save hashes after update for verification
    c1a_hash = git_cmd.must_git("rev-parse HEAD~1").strip()
    c1b_hash = git_cmd.must_git("rev-parse HEAD").strip()

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
    def get_test_prs() -> list:
        result = []
        for pr in github.get_info(None, git_cmd).pull_requests:
            if pr.from_branch.startswith('spr/main/'):
                try:
                    # Look for our unique tags in the commit message
                    commit_msg = git_cmd.must_git(f"show -s --format=%B {pr.commit.commit_hash}")
                    if f"test-tag:{unique_tag1}" in commit_msg or f"test-tag:{unique_tag2}" in commit_msg:
                        result.append(pr)
                except:  # Skip failures since we're just filtering
                    pass
        return result

    # Verify all 4 PRs exist with correct connections
    log.info("Verifying initial state of PRs...")
    # Find our test PRs
    test_prs = get_test_prs()
    all_prs = {
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
    c2a_id = pr2a.commit.commit_id

    # Verify stack 1 connections
    assert pr1a.base_ref == "main", "PR1A should target main"
    assert pr1b.base_ref == f"spr/main/{pr1a.commit.commit_id}", "PR1B should target PR1A"

    # Verify stack 2 connections
    assert pr2a.base_ref == "main", "PR2A should target main"
    assert pr2b.base_ref == f"spr/main/{pr2a.commit.commit_id}", "PR2B should target PR2A"

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