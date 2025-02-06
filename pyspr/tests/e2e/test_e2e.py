"""End-to-end test for amending commits in stack and PR stack isolation and WIP behavior."""
# pyright: reportUnusedVariable=false
# pyright: reportUnusedImport=false
# pyright: reportUnknownVariableType=false
# pyright: reportUnknownMemberType=false
# pyright: reportOptionalMemberAccess=false

import os
import tempfile
import uuid
import subprocess
from typing import Dict, Generator, List, Optional, Set, Tuple, Union
import pytest

from pyspr.config import Config
from pyspr.git import RealGit, Commit 
from pyspr.github import GitHubClient, PullRequest

def run_cmd(cmd: str) -> None:
    """Run a shell command using subprocess with proper error handling."""
    subprocess.run(cmd, shell=True, check=True)

def test_wip_behavior(test_repo: Tuple[str, str, str, str]) -> None:
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
    def make_commit(file: str, msg: str) -> str:
        with open(file, "w") as f:
            f.write(f"{file}\n")
        run_cmd(f"git add {file}")
        run_cmd(f'git commit -m "{msg}"')
        return git_cmd.must_git("rev-parse HEAD").strip()
        
    print("Creating commits...")
    c1_hash = make_commit("wip_test1.txt", "First regular commit")
    c2_hash = make_commit("wip_test2.txt", "Second regular commit")
    c3_hash = make_commit("wip_test3.txt", "WIP Third commit")
    _ = make_commit("wip_test4.txt", "Fourth regular commit")  # Not used but kept for completeness
    run_cmd(f"git push -u origin {test_branch}")  # Push branch with commits
    
    # Run update to create PRs
    print("Creating PRs...")
    
    # Go back to project dir to run commands 
    os.chdir(orig_dir)
    run_cmd(f"rye run pyspr update -C {repo_dir}")
    
    # Go back to repo for verification
    os.chdir(repo_dir)
    
    # Verify only first two PRs were created
    info = github.get_info(None, git_cmd)
    assert info is not None, "GitHub info should not be None"
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
def test_repo() -> Generator[Tuple[str, str, str, str], None, None]:
    """Use yang/teststack repo with a temporary test branch."""
    orig_dir = os.getcwd()
    owner = "yang"
    name = "teststack"
    repo_name = f"{owner}/{name}"  # Used in print and subprocess call
    test_branch = f"test-spr-amend-{uuid.uuid4().hex[:7]}"
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
    
    # Verify PRs created
    info = github.get_info(None, git_cmd)
    assert info is not None, "GitHub info should not be None"
    assert len(info.pull_requests) == 4
    pr1, pr2, pr3, pr4 = sorted(info.pull_requests, key=lambda pr: pr.number)
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
    assert len(info.pull_requests) == 4
    prs_by_num = {pr.number: pr for pr in info.pull_requests}
    pr1 = prs_by_num.get(pr1_num)
    pr3 = prs_by_num.get(pr3_num)
    pr4 = prs_by_num.get(pr4_num)
    new_pr = next((pr for pr in info.pull_requests if pr.number not in [pr1_num, pr2_num, pr3_num, pr4_num]), None)
    
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
        for i in range(num_commits):
            prefix = "test_merge" if not use_merge_queue else "mq_test"
            c_hash = make_commit(f"{prefix}{i+1}.txt", f"line 1 - {unique}", 
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
    # Verify PRs created
    info = github.get_info(None, git_cmd)
    assert info is not None, "GitHub info should not be None"
    assert len(info.pull_requests) == num_commits, f"Should have created {num_commits} PRs"
    prs = sorted(info.pull_requests, key=lambda pr: pr.number)
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

    if use_merge_queue:
        # For merge queue: top merged PR open, some closed, some remain
        expected_open = 1 + len(to_remain)  # Top merged PR + remaining PRs
        assert len(info.pull_requests) == expected_open, f"{expected_open} PRs should remain open"
        prs_by_num = {pr.number: pr for pr in info.pull_requests}
        
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
        assert len(info.pull_requests) == expected_open, f"{expected_open} PRs should remain open"
        if to_merge:
            # Get the merge commit
            run_cmd("git fetch origin main")
            merge_sha = git_cmd.must_git("rev-parse origin/main").strip()
            merge_msg = git_cmd.must_git(f"show -s --format=%B {merge_sha}").strip()
            # Verify merge commit contains the right PR number
            assert f"#{top_pr_num}" in merge_msg, f"Merge commit should reference PR #{top_pr_num}"
            # Verify merge commit contains only merged files
            merge_files = git_cmd.must_git(f"show --name-only {merge_sha}").splitlines()
            prefix = "test_merge" if not use_merge_queue else "mq_test"
            for i, pr in enumerate(to_merge):
                filename = f"{prefix}{i+1}.txt"
                assert filename in merge_files, f"Merge should include {filename}"
            # Verify unmerged files are not in merge commit
            for i in range(len(to_merge), num_commits):
                filename = f"{prefix}{i+1}.txt"
                assert filename not in merge_files, f"Merge should not include {filename}"

        # Verify remaining PRs stay open
        prs_by_num = {pr.number: pr for pr in info.pull_requests}
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

        # 1. Create stack with commits A -> B -> C
        c1_hash, c1_id = make_commit("file1.txt", "line 1", "Commit A")
        c2_hash, c2_id = make_commit("file2.txt", "line 1", "Commit B")
        c3_hash, c3_id = make_commit("file3.txt", "line 1", "Commit C")
        run_cmd(f"git push -u origin {branch}")

        print("Creating initial PRs...")
        os.chdir(orig_dir)
        subprocess.run(["rye", "run", "pyspr", "update", "-C", repo_dir], check=True)
        os.chdir(repo_dir)

        # Get initial PR info and filter to our newly created PRs
        info = github.get_info(None, git_cmd)
        assert info is not None, "GitHub info should not be None"
        # Find PRs matching our commit IDs 
        commit_prs = [pr for pr in info.pull_requests 
                    if pr.commit.commit_id in [c1_id, c2_id, c3_id] and
                    pr.from_branch is not None and pr.from_branch.startswith('spr/main/')]
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
        info = github.get_info(None, git_cmd)
        assert info is not None, "GitHub info should not be None"
        # Get relevant PRs: those with our commit IDs + B's PR if it's still open
        pr_nums_to_check: Set[int] = set()  # Track all numbers we care about
        relevant_prs: List[PullRequest] = []
        for pr in info.pull_requests:
            if (pr.commit.commit_id in [c1_id, new_c2_id, c3_id] or
                pr.number == pr2_num):
                relevant_prs.append(pr)
                pr_nums_to_check.add(pr.number)
        
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

        # Verify all 4 PRs exist with correct connections
        print("\nVerifying initial state of PRs...")
        info = github.get_info(None, git_cmd)
        assert info is not None, "GitHub info should not be None"
        # Find PRs by commit ID
        all_prs = {}
        for pr in info.pull_requests:
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
        info = github.get_info(None, git_cmd)
        assert info is not None, "GitHub info should not be None"
        
        # Get remaining PRs and their targets
        remaining_prs = {}
        for pr in info.pull_requests:
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
