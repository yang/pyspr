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
from pyspr.spr import StackedPR

@pytest.fixture
def test_repo():
    """Use yang/teststack repo with a temporary test branch."""
    orig_dir = os.getcwd()
    repo_name = "yang/teststack"
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
        os.system(f"git checkout -b {test_branch}")

        # Configure git
        os.system("git config user.name 'Test User'")
        os.system("git config user.email 'test@example.com'")
        
        yield repo_name, test_branch

        # Cleanup - delete test branch locally and remotely
        os.system("git checkout main")
        os.system(f"git branch -D {test_branch}")
        try:
            os.system(f"git push origin --delete {test_branch}")
        except:
            print(f"Failed to delete remote branch {test_branch}, may not exist")

        # Return to original directory
        os.chdir(orig_dir)

def test_amend_workflow(test_repo):
    """Test full amend workflow with real PRs."""
    repo_name, test_branch = test_repo
    owner = "yang"  # Hard-code since we know the test repo

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
    spr = StackedPR(config, github, git_cmd)
    
    # Create 3 commits
    def make_commit(file, line, msg):
        with open(file, "w") as f:
            f.write(f"{file}\n{line}\n")
        os.system(f"git add {file}")
        os.system(f'git commit -m "{msg}"')
        return git_cmd.must_git("rev-parse HEAD").strip()
        
    print("Creating commits...")
    c1_hash = make_commit("ta.txt", "line 1", "First commit")
    c2_hash = make_commit("tb.txt", "line 1", "Second commit")  
    c3_hash = make_commit("tc.txt", "line 1", "Third commit")
    os.system(f"git push -u origin {test_branch}")  # Push branch with commits
    
    # Initial update to create PRs
    print("Creating initial PRs...")
    spr.update_pull_requests(None)
    
    # Verify PRs created
    info = github.get_info(None, git_cmd)
    assert len(info.pull_requests) == 3
    pr1, pr2, pr3 = sorted(info.pull_requests, key=lambda pr: pr.number)
    print(f"Created PRs: #{pr1.number}, #{pr2.number}, #{pr3.number}")
    
    # Save PR numbers and commit IDs and hashes
    pr1_num = pr1.number
    pr2_num = pr2.number
    pr3_num = pr3.number
    
    c1_id = pr1.commit.commit_id
    c2_id = pr2.commit.commit_id
    c3_id = pr3.commit.commit_id

    c1_hash_post = pr1.commit.commit_hash
    c2_hash_post = pr2.commit.commit_hash
    c3_hash_post = pr3.commit.commit_hash

    # Debug: Check if commit messages have IDs after first update
    print("\nChecking commit messages after first update:")
    for c_hash in [c1_hash, c2_hash, c3_hash]:
        msg = git_cmd.must_git(f"show -s --format=%B {c_hash}").strip()
        print(f"Commit {c_hash[:8]} message:\n{msg}\n")

    # Verify initial PR chain
    assert pr1.base_ref == "main"
    assert pr2.base_ref == f"spr/main/{c1_id}"
    assert pr3.base_ref == f"spr/main/{c2_id}"
    
    print("Amending middle commit...")
    # Get current messages (which should have IDs from spr update)
    c1_msg = git_cmd.must_git(f"show -s --format=%B HEAD~2").strip()
    c2_msg = git_cmd.must_git(f"show -s --format=%B HEAD~1").strip()
    c3_msg = git_cmd.must_git(f"show -s --format=%B HEAD").strip()

    # Reset and cherry-pick c1, preserving SPR-updated message
    os.system("git reset --hard HEAD~3")
    os.system(f"git cherry-pick {c1_hash}")
    os.system(f'git commit --amend -m "{c1_msg}"')
    c1_hash_new = git_cmd.must_git("rev-parse HEAD").strip()
    log1 = git_cmd.must_git(f"show -s --format=%B {c1_hash_new}").strip()
    print(f"Commit 1: old={c1_hash} new={c1_hash_new} id={c1_id}")
    print(f"Log 1:\n{log1}")
    
    # Cherry-pick and amend c2 with staged changes, preserving SPR-updated message
    os.system(f"git cherry-pick {c2_hash}")
    with open("tb.txt", "a") as f:
        f.write("line 2\n")
    os.system("git add tb.txt")
    os.system(f'git commit --amend -m "{c2_msg}"')
    new_c2_hash = git_cmd.must_git("rev-parse HEAD").strip()
    log2 = git_cmd.must_git(f"show -s --format=%B {new_c2_hash}").strip()
    print(f"Commit 2: old={c2_hash} new={new_c2_hash} id={c2_id}")
    print(f"Log 2:\n{log2}")
    
    # Cherry-pick c3, preserving SPR-updated message
    os.system(f"git cherry-pick {c3_hash}")
    os.system(f'git commit --amend -m "{c3_msg}"')
    c3_hash_new = git_cmd.must_git("rev-parse HEAD").strip()
    log3 = git_cmd.must_git(f"show -s --format=%B {c3_hash_new}").strip()
    print(f"Commit 3: old={c3_hash} new={c3_hash_new} id={c3_id}")
    print(f"Log 3:\n{log3}")
    
    print("Updating PRs after amend...")
    # Run update with amended commits
    spr.update_pull_requests(None)
    
    # Verify PRs updated properly
    info = github.get_info(None, git_cmd)
    assert len(info.pull_requests) == 3
    pr1, pr2, pr3 = sorted(info.pull_requests, key=lambda pr: pr.number)
    
    # Same PR numbers - verify existing PRs were updated, not new ones created 
    assert pr1.number == pr1_num, f"PR1 number changed from {pr1_num} to {pr1.number}"
    assert pr2.number == pr2_num, f"PR2 number changed from {pr2_num} to {pr2.number}" 
    assert pr3.number == pr3_num, f"PR3 number changed from {pr3_num} to {pr3.number}"
    
    # Same commit IDs
    assert pr1.commit.commit_id == c1_id, f"PR1 commit ID changed from {c1_id} to {pr1.commit.commit_id}"
    assert pr2.commit.commit_id == c2_id, f"PR2 commit ID changed from {c2_id} to {pr2.commit.commit_id}"
    assert pr3.commit.commit_id == c3_id, f"PR3 commit ID changed from {c3_id} to {pr3.commit.commit_id}"
    
    # All commit hashes changed due to adding commit IDs
    assert pr1.commit.commit_hash == c1_hash_new, f"PR1 hash should be {c1_hash_new}"
    assert pr2.commit.commit_hash == new_c2_hash, f"PR2 hash should be {new_c2_hash}" 
    assert pr3.commit.commit_hash == c3_hash_new, f"PR3 hash should be {c3_hash_new}"
    
    # Verify PR targets remained correct
    assert pr1.base_ref == "main", f"PR1 base ref incorrect: {pr1.base_ref}"
    assert pr2.base_ref == f"spr/main/{c1_id}", f"PR2 base ref incorrect: {pr2.base_ref}"
    assert pr3.base_ref == f"spr/main/{c2_id}", f"PR3 base ref incorrect: {pr3.base_ref}"

    # Verify commit IDs exist in messages and are preserved through updates
    print("\nVerifying commit IDs in messages after updates:")
    for pr in [pr1, pr2, pr3]:
        message = git_cmd.must_git(f"show -s --format=%B {pr.commit.commit_hash}").strip()
        print(f"PR #{pr.number} message:\n{message}\n")
        assert f"commit-id:{pr.commit.commit_id}" in message, f"PR #{pr.number} should have correct commit ID in message"