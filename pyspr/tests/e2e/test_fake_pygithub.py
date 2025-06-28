"""Tests for the fake_pygithub module."""

import os
import tempfile
from pathlib import Path

from pyspr.tests.e2e.fake_pygithub import (
    create_fake_github,
    FakeGithub,
    FakeRepository,
    FakePullRequest
)

def test_basic_operations() -> None:
    """Test basic operations with the fake GitHub."""
    # Set up a temp directory for state storage
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a fake GitHub instance with state file in temp dir
        state_file = os.path.join(tmpdir, "fake_github_state.yaml")
        
        # Create a fresh instance with no previous state
        github: FakeGithub = create_fake_github(
            data_dir=Path(tmpdir),
            state_file=Path(state_file),
        )
        
        # Create repository and verify it exists
        repo: FakeRepository = github.get_repo("testorg/testrepo")
        assert repo.full_name == "testorg/testrepo"
        assert repo.owner_login == "testorg"
        assert repo.name == "testrepo"
        assert repo.github_ref is github
        
        # Create a PR
        pr: FakePullRequest = repo.create_pull(
            title="Test PR",
            body="Test body",
            base="main",
            head="feature-branch"
        )
        
        # Verify PR was created and stored properly
        assert pr.number == 1
        assert pr.title == "Test PR"
        assert pr.body == "Test body"
        # PR should have reference to the github instance via its repository
        assert pr.repository is not None
        assert pr.repository.github_ref is github
        
        # PR should be in pull_requests dict with composite key
        pr_key: str = f"testorg/testrepo:1"
        assert pr_key in github.pull_requests
        assert github.pull_requests[pr_key] is pr
        
        # Verify we can get the PR by number
        retrieved_pr = github.get_pull(1)
        assert retrieved_pr is pr
        
        # Edit the PR
        pr.edit(title="Updated Title")
        assert pr.title == "Updated Title"
        
        # Verify state was saved to file
        assert os.path.exists(state_file)
        
        # Create a new GitHub instance that loads from the same state file
        github2: FakeGithub = create_fake_github(
            data_dir=Path(tmpdir),
            state_file=Path(state_file),
        )
        
        # Verify PR data was loaded from state
        pr_key: str = f"testorg/testrepo:1"
        assert pr_key in github2.pull_requests
        loaded_pr: FakePullRequest = github2.pull_requests[pr_key]
        assert loaded_pr.title == "Updated Title"
        assert loaded_pr.body == "Test body"
        
        # Verify repository was loaded
        assert "testorg/testrepo" in github2.repositories
        loaded_repo: FakeRepository = github2.repositories["testorg/testrepo"]
        assert loaded_repo.name == "testrepo"

def test_circular_references() -> None:
    """Test that circular references are handled correctly."""
    # Set up a temp directory for state storage
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a fake GitHub instance with state file in temp dir
        state_file = os.path.join(tmpdir, "fake_github_state.yaml")
        
        # Create an entirely fresh instance with no pre-loaded state
        github: FakeGithub = create_fake_github(
            data_dir=Path(tmpdir),
            state_file=Path(state_file),
        )
        
        # Create two repositories that refer to the same owner
        repo1: FakeRepository = github.get_repo("testuser/repo1")
        repo2: FakeRepository = github.get_repo("testuser/repo2")
        assert repo1.owner is repo2.owner
        
        # Create PRs with cross-references
        pr1: FakePullRequest = repo1.create_pull(
            title="PR1",
            body="PR1 body",
            base="main",
            head="feature1"
        )
        # Create PR2 (used for testing circular references after reload)
        _ = repo2.create_pull(
            title="PR2",
            body="PR2 body",
            base="main",
            head="feature2"
        )
        
        # Add reviewers
        pr1.create_review_request(reviewers=["testuser"])
        
        # Explicitly save state to file
        github.save_state()
        
        # Create new instance and load state
        github2: FakeGithub = create_fake_github(
            data_dir=Path(tmpdir),
            state_file=Path(state_file),
        )
        
        # Verify PRs loaded correctly
        assert len(github2.pull_requests) == 2
        
        # Find the PRs by their titles since the numbering may vary
        loaded_prs: list[FakePullRequest] = list(github2.pull_requests.values())
        loaded_pr1: FakePullRequest | None = next((pr for pr in loaded_prs if pr.title == "PR1"), None)
        loaded_pr2: FakePullRequest | None = next((pr for pr in loaded_prs if pr.title == "PR2"), None)
        
        assert loaded_pr1 is not None, "PR1 not found in loaded state"
        assert loaded_pr2 is not None, "PR2 not found in loaded state"
        
        # Verify user reference works
        assert "testuser" in github2.users
        assert loaded_pr1.user is github2.users["testuser"]
        
        # Verify repositories were loaded
        assert "testuser/repo1" in github2.repositories
        assert "testuser/repo2" in github2.repositories
        
        # Verify properties work after reload
        assert loaded_pr1.repository is github2.repositories["testuser/repo1"]
        assert loaded_pr1.base.ref == "main"
        assert loaded_pr1.base.repo is github2.repositories["testuser/repo1"]
        
        # Verify reviewers
        assert "testuser" in loaded_pr1.data_record.reviewers

def test_graphql_functionality() -> None:
    """Test GraphQL functionality."""
    # Set up a temp directory for state storage
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a fresh GitHub instance with a clean state
        state_file = os.path.join(tmpdir, "fake_github_state.yaml")
        github: FakeGithub = create_fake_github(
            data_dir=Path(tmpdir),
            state_file=Path(state_file),
        )
        
        repo: FakeRepository = github.get_repo("testorg/testrepo")
        
        # Create PRs
        pr1: FakePullRequest = repo.create_pull(
            title="GraphQL Test PR1",
            body="PR1 body",
            base="main",
            head="spr/main/abcd1234"
        )
        pr2: FakePullRequest = repo.create_pull(
            title="GraphQL Test PR2",
            body="PR2 body",
            base=f"spr/main/{pr1.data_record.commit_id}",
            head="spr/main/1234abcd"
        )
        
        # Request GraphQL data
        from pyspr.tests.e2e.fake_pygithub import FakeRequester
        from typing import Any, Dict
        # Access requester using public API
        requester: FakeRequester = getattr(github, '_Github__requester')
        _: Dict[str, Any]
        response: Dict[str, Any]
        _, response = requester.requestJsonAndCheck(
            "POST", 
            "https://api.github.com/graphql", 
            {"query": "query { viewer { login pullRequests { nodes { number } } } }"}
        )
        
        # Verify response contains our PRs
        assert "data" in response
        assert "viewer" in response["data"]
        assert "pullRequests" in response["data"]["viewer"]
        assert "nodes" in response["data"]["viewer"]["pullRequests"]
        pr_nodes: list[Dict[str, Any]] = response["data"]["viewer"]["pullRequests"]["nodes"]
        assert len(pr_nodes) == 2
        pr_numbers: list[int] = [node["number"] for node in pr_nodes]
        assert pr1.number in pr_numbers
        assert pr2.number in pr_numbers