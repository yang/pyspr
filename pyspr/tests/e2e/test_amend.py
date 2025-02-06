"""End-to-end test for amending commits in stack."""

import os
import tempfile
import uuid
import subprocess
import time
from pathlib import Path
import pytest
import shutil

from pyspr.config import Config
from pyspr.git import RealGit, Commit
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

def test_amend_workflow(test_repo):
    """Test full amend workflow with real PRs."""
    owner, repo_name, test_branch = test_repo[:3]

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
    def make_commit(file, line, msg):
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

    c1_hash_post = pr1.commit.commit_hash
    c2_hash_post = pr2.commit.commit_hash
    c3_hash_post = pr3.commit.commit_hash
    c4_hash_post = pr4.commit.commit_hash

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
    new_c35_hash = make_commit("tc5.txt", "line 1", "Commit three point five")
    
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

def _run_merge_test(repo_fixture, owner: str, use_merge_queue: bool, num_commits: int, count: int = None):
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
    def make_commit(file, line, msg):
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
        return git_cmd.must_git("rev-parse HEAD").strip()
        
    print("Creating commits...")
    try:
        # Use static filenames but unique content
        unique = str(uuid.uuid4())[:8]
        commit_hashes = []
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
        print("\nFinal PR state after merge attempt:")
        for pr in sorted(info.pull_requests, key=lambda pr: pr.number):
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

    if use_merge_queue:
        # For merge queue: top merged PR open, some closed, some remain
        expected_open = 1 + len(to_remain)  # Top merged PR + remaining PRs
        assert len(info.pull_requests) == expected_open, f"{expected_open} PRs should remain open"
        prs_by_num = {pr.number: pr for pr in info.pull_requests}
        
        if to_merge:
            assert top_pr_num in prs_by_num, f"Top PR #{top_pr_num} should remain open in queue"
            for pr in to_merge[:-1]:
                assert pr.number not in prs_by_num, f"PR #{pr.number} should be closed"
            top_pr = prs_by_num[top_pr_num]
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

def test_merge_workflow(test_repo):
    """Test full merge workflow with real PRs."""
    owner, name, test_branch, repo_dir = test_repo
    _run_merge_test(test_repo, owner, False, 3)

@pytest.fixture
def test_mq_repo():
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

def test_merge_queue_workflow(test_mq_repo):
    """Test merge queue workflow with real PRs."""
    _run_merge_test(test_mq_repo, "yangenttest1", True, 2)

def test_partial_merge_workflow(test_repo):
    """Test partial merge workflow, merging only 2 of 3 PRs."""
    owner, name, test_branch, repo_dir = test_repo
    _run_merge_test(test_repo, owner, False, 3, count=2)

def test_partial_merge_queue_workflow(test_mq_repo):
    """Test partial merge queue workflow, merging only 2 of 3 PRs to queue."""
    _run_merge_test(test_mq_repo, "yangenttest1", True, 3, count=2)