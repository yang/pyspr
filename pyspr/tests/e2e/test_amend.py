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
    c4_hash = make_commit("td.txt", "line 1", "Fourth commit")
    os.system(f"git push -u origin {test_branch}")  # Push branch with commits
    
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
    os.system("git reset --hard HEAD~4")
    os.system(f"git cherry-pick {c1_hash}")
    os.system(f'git commit --amend -m "{c1_msg}"')
    c1_hash_new = git_cmd.must_git("rev-parse HEAD").strip()
    log1 = git_cmd.must_git(f"show -s --format=%B {c1_hash_new}").strip()
    print(f"Commit 1: old={c1_hash} new={c1_hash_new} id={c1_id}")
    print(f"Log 1:\n{log1}")
    
    # Skip c2 entirely - delete it from stack
    print("Skipping c2 - deleting it")
    
    # Cherry-pick and amend c3, preserving SPR-updated message
    os.system(f"git cherry-pick {c3_hash}")
    with open("tc.txt", "a") as f:
        f.write("line 2\n")
    os.system("git add tc.txt")
    os.system(f'git commit --amend -m "{c3_msg}"')
    c3_hash_new = git_cmd.must_git("rev-parse HEAD").strip()
    log3 = git_cmd.must_git(f"show -s --format=%B {c3_hash_new}").strip()
    print(f"Commit 3: old={c3_hash} new={c3_hash_new} id={c3_id}")
    print(f"Log 3:\n{log3}")
    
    # Insert new c3.5
    print("Inserting new c3.5")
    new_c35_hash = make_commit("tc5.txt", "line 1", "Commit three point five")
    
    # Cherry-pick c4, preserving SPR-updated message
    os.system(f"git cherry-pick {c4_hash}")
    os.system(f'git commit --amend -m "{c4_msg}"')
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