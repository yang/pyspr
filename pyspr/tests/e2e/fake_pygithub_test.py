"""Tests for the fake_pygithub module."""

import os
import tempfile

from pyspr.tests.e2e.fake_pygithub import (
    create_fake_github
)

def test_basic_operations():
    """Test basic operations with the fake GitHub."""
    # Set up a temp directory for state storage
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a fake GitHub instance with state file in temp dir
        state_file = os.path.join(tmpdir, "fake_github_state.yaml")
        
        # Create a fresh instance with no previous state
        github = create_fake_github(
            data_dir=tmpdir,
            state_file=state_file,
            load_state=False
        )
        
        # Create repository and verify it exists
        repo = github.get_repo("testorg/testrepo")
        assert repo.full_name == "testorg/testrepo"
        assert repo.owner_login == "testorg"
        assert repo.name == "testrepo"
        assert repo.github_ref is github
        
        # Create a PR
        pr = repo.create_pull(
            title="Test PR",
            body="Test body",
            base="main",
            head="feature-branch"
        )
        
        # Verify PR was created and stored properly
        assert pr.number == 1
        assert pr.title == "Test PR"
        assert pr.body == "Test body"
        assert pr.github_ref is github
        
        # PR should be in pull_requests dict with composite key
        pr_key = f"testorg/testrepo:1"
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
        github2 = create_fake_github(
            data_dir=tmpdir,
            state_file=state_file,
            load_state=True
        )
        
        # Verify PR data was loaded from state
        pr_key = f"testorg/testrepo:1"
        assert pr_key in github2.pull_requests
        loaded_pr = github2.pull_requests[pr_key]
        assert loaded_pr.title == "Updated Title"
        assert loaded_pr.body == "Test body"
        
        # Verify repository was loaded
        assert "testorg/testrepo" in github2.repositories
        loaded_repo = github2.repositories["testorg/testrepo"]
        assert loaded_repo.name == "testrepo"

def test_circular_references():
    """Test that circular references are handled correctly."""
    # Set up a temp directory for state storage
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a fake GitHub instance with state file in temp dir
        state_file = os.path.join(tmpdir, "fake_github_state.yaml")
        
        # Create an entirely fresh instance with no pre-loaded state
        github = create_fake_github(
            data_dir=tmpdir,
            state_file=state_file,
            load_state=False
        )
        
        # Create two repositories that refer to the same owner
        repo1 = github.get_repo("testuser/repo1")
        repo2 = github.get_repo("testuser/repo2")
        assert repo1.owner is repo2.owner
        
        # Create PRs with cross-references
        pr1 = repo1.create_pull(
            title="PR1",
            body="PR1 body",
            base="main",
            head="feature1"
        )
        pr2 = repo2.create_pull(
            title="PR2",
            body="PR2 body",
            base="main",
            head="feature2"
        )
        
        # Add reviewers
        pr1.create_review_request(reviewers=["testuser"])
        
        # Explicitly save state to file
        github._save_state()
        
        # Create new instance and load state
        github2 = create_fake_github(
            data_dir=tmpdir,
            state_file=state_file,
            load_state=True
        )
        
        # Verify PRs loaded correctly
        assert len(github2.pull_requests) == 2
        
        # Find the PRs by their titles since the numbering may vary
        loaded_prs = list(github2.pull_requests.values())
        loaded_pr1 = next((pr for pr in loaded_prs if pr.title == "PR1"), None)
        loaded_pr2 = next((pr for pr in loaded_prs if pr.title == "PR2"), None)
        
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
        assert "testuser" in loaded_pr1.reviewers

def test_graphql_functionality():
    """Test GraphQL functionality."""
    # Set up a temp directory for state storage
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a fresh GitHub instance with a clean state
        state_file = os.path.join(tmpdir, "fake_github_state.yaml")
        github = create_fake_github(
            data_dir=tmpdir,
            state_file=state_file,
            load_state=False
        )
        
        repo = github.get_repo("testorg/testrepo")
        
        # Create PRs
        pr1 = repo.create_pull(
            title="GraphQL Test PR1",
            body="PR1 body",
            base="main",
            head="spr/main/abcd1234"
        )
        pr2 = repo.create_pull(
            title="GraphQL Test PR2",
            body="PR2 body",
            base=f"spr/main/{pr1.commit_id}",
            head="spr/main/1234abcd"
        )
        
        # Request GraphQL data
        requester = github._Github__requester
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
        pr_nodes = response["data"]["viewer"]["pullRequests"]["nodes"]
        assert len(pr_nodes) == 2
        pr_numbers = [node["number"] for node in pr_nodes]
        assert pr1.number in pr_numbers
        assert pr2.number in pr_numbers