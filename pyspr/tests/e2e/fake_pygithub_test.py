"""Tests for the fake_pygithub module."""

import os
import json
import tempfile
from pathlib import Path

import pytest
from pyspr.tests.e2e.fake_pygithub import (
    FakeGithubState,
    FakeNamedUser,
    FakeRepository,
    FakePullRequest,
    FakeGithub,
)

def test_state_serialization():
    """Test that state can be serialized and deserialized."""
    # Create a state
    state = FakeGithubState()
    
    # Add a user
    user = state.create_user(login="testuser", name="Test User", email="test@example.com")
    
    # Add a repository
    repo = state.create_repository(owner_login="testuser", name="testrepo")
    
    # Add a PR
    pr = state.create_pull_request(
        number=1,
        repo_full_name="testuser/testrepo",
        title="Test PR",
        body="Test body",
        commit_id="abcd1234",
        commit_hash="deadbeef",
        commit_subject="Test subject"
    )
    
    # Serialize to JSON
    json_data = state.model_dump_json()
    
    # Deserialize from JSON
    new_state = FakeGithubState.model_validate_json(json_data)
    
    # Verify objects were restored
    assert "testuser" in new_state.users
    assert "testuser/testrepo" in new_state.repositories
    assert 1 in new_state.pull_requests
    
    # Verify relationships
    new_pr = new_state.pull_requests[1]
    assert new_pr.repository is not None
    assert new_pr.repository.name == "testrepo"
    assert new_pr.repository.owner is not None
    assert new_pr.repository.owner.login == "testuser"
    
    # Verify PR properties
    assert new_pr.commit.commit_id == "abcd1234"
    assert new_pr.commit.commit_hash == "deadbeef"
    assert new_pr.commit.subject == "Test subject"
    
    # Verify repository owner
    assert new_state.repositories["testuser/testrepo"].owner.name == "Test User"

def test_github_state_file():
    """Test saving and loading state to/from a file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a state file path
        state_file = os.path.join(tmpdir, "state.json")
        
        # Create and populate a state
        state = FakeGithubState()
        state.create_user(login="fileuser")
        state.create_repository(owner_login="fileuser", name="filerepo")
        state.create_pull_request(
            number=1,
            repo_full_name="fileuser/filerepo",
            title="File PR",
            commit_id="file1234"
        )
        
        # Save to file
        state.save_to_file(state_file)
        
        # Verify file exists
        assert os.path.exists(state_file)
        
        # Load from file
        new_state = FakeGithubState.load_from_file(state_file)
        
        # Verify contents
        assert "fileuser" in new_state.users
        assert "fileuser/filerepo" in new_state.repositories
        assert 1 in new_state.pull_requests
        assert new_state.pull_requests[1].commit.commit_id == "file1234"

def test_fake_github_api():
    """Test the FakeGithub API."""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["HOME"] = tmpdir  # Set home to temp dir to control state file location
        data_dir = os.path.join(tmpdir, ".git", "fake_github")
        os.makedirs(data_dir, exist_ok=True)
        
        # Create FakeGithub instance
        github = FakeGithub(token="fake-token")
        github.data_dir = data_dir
        github.state_file = os.path.join(data_dir, "state.json")
        
        # Get repo (creates it if not exists)
        repo = github.get_repo("testorg/testrepo")
        
        # Create a PR
        pr = repo.create_pull(
            title="API Test PR",
            body="PR from API test",
            base="main",
            head="feature-branch"
        )
        
        # Verify PR was created
        assert pr.number == 1
        assert pr.title == "API Test PR"
        
        # Get PR and verify fields
        pr2 = repo.get_pull(1)
        assert pr2.number == 1
        assert pr2.title == "API Test PR"
        assert pr2.commit.commit_id is not None
        
        # Edit PR
        pr2.edit(title="Updated PR", body="Updated body")
        assert pr2.title == "Updated PR"
        assert pr2.commit.subject == "Updated PR"  # Should update commit subject too
        
        # Test GraphQL API to get PRs
        requester = github._Github__requester
        _, response = requester.requestJsonAndCheck(
            "POST", 
            "https://api.github.com/graphql", 
            {"query": "query { viewer { login pullRequests { nodes { number } } } }"}
        )
        
        # Verify response contains our PR
        assert len(response["data"]["viewer"]["pullRequests"]["nodes"]) == 1
        assert response["data"]["viewer"]["pullRequests"]["nodes"][0]["number"] == 1