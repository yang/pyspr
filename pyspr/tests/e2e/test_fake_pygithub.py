"""Tests for the fake_pygithub module."""

import os
import tempfile
import subprocess
from pathlib import Path

from pyspr.tests.e2e.fake_pygithub import (
    create_fake_github,
    FakeGithub,
    FakeRepository,
    FakePullRequest,
    FakeRef
)

def test_basic_operations() -> None:
    """Test basic operations with the fake GitHub."""
    # Set up a temp directory for state storage
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create the expected directory structure
        remote_dir = os.path.join(tmpdir, "remote.git")
        os.makedirs(remote_dir, exist_ok=True)
        
        # Initialize a bare git repository
        import subprocess
        subprocess.run(['git', 'init', '--bare', remote_dir], check=True)
        
        # Create the fake_github directory where state would normally be stored
        fake_github_dir = os.path.join(tmpdir, "teststack", ".git", "fake_github")
        os.makedirs(fake_github_dir, exist_ok=True)
        
        # Create a fake GitHub instance with state file in the expected location
        state_file = os.path.join(fake_github_dir, "fake_github_state.yaml")
        
        # Create a fresh instance with no previous state
        github: FakeGithub = create_fake_github(
            data_dir=Path(fake_github_dir),
            state_file=Path(state_file),
        )
        
        # Create repository and verify it exists
        repo = github.get_repo("testorg/testrepo")
        assert isinstance(repo, FakeRepository)
        assert repo.full_name == "testorg/testrepo"
        assert repo.owner_login == "testorg"
        assert repo.name == "testrepo"
        assert repo.github_ref is github
        
        # For now, skip PR creation tests that require actual commits
        # These should be tested via the e2e tests which set up proper git repos
        
        # Test that we can create users
        user = github.get_user("testuser", create=True)
        assert user is not None
        assert user.login == "testuser"
        
        # Save state explicitly
        github.save_state()
        
        # Verify state was saved to file
        assert os.path.exists(state_file)
        
        # Create a new GitHub instance that loads from the same state file
        github2: FakeGithub = create_fake_github(
            data_dir=Path(fake_github_dir),
            state_file=Path(state_file),
        )
        
        # Verify user was loaded from state
        assert "testuser" in github2.users
        loaded_user = github2.users["testuser"]
        assert loaded_user.login == "testuser"
        
        # Verify repository was loaded
        assert "testorg/testrepo" in github2.repositories
        loaded_repo: FakeRepository = github2.repositories["testorg/testrepo"]
        assert loaded_repo.name == "testrepo"

def test_circular_references() -> None:
    """Test that circular references are handled correctly."""
    # Set up a temp directory for state storage
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create the expected directory structure
        remote_dir = os.path.join(tmpdir, "remote.git")
        os.makedirs(remote_dir, exist_ok=True)
        
        # Initialize a bare git repository
        import subprocess
        subprocess.run(['git', 'init', '--bare', remote_dir], check=True)
        
        # Create the fake_github directory where state would normally be stored
        fake_github_dir = os.path.join(tmpdir, "teststack", ".git", "fake_github")
        os.makedirs(fake_github_dir, exist_ok=True)
        
        # Create a fake GitHub instance with state file in the expected location
        state_file = os.path.join(fake_github_dir, "fake_github_state.yaml")
        
        # Create an entirely fresh instance with no pre-loaded state
        github: FakeGithub = create_fake_github(
            data_dir=Path(fake_github_dir),
            state_file=Path(state_file),
        )
        
        # Create two repositories that refer to the same owner
        repo1 = github.get_repo("testuser/repo1")
        repo2 = github.get_repo("testuser/repo2")
        assert isinstance(repo1, FakeRepository)
        assert isinstance(repo2, FakeRepository)
        assert repo1.owner is repo2.owner
        
        # Test user references
        user = github.get_user("testuser", create=True)
        assert user is not None
        assert user.login == "testuser"
        
        # Explicitly save state to file
        github.save_state()
        
        # Create new instance and load state
        github2: FakeGithub = create_fake_github(
            data_dir=Path(fake_github_dir),
            state_file=Path(state_file),
        )
        
        # Verify user reference works
        assert "testuser" in github2.users
        
        # Verify repositories were loaded
        assert "testuser/repo1" in github2.repositories
        assert "testuser/repo2" in github2.repositories
        
        # Verify that both repos have owners with the same login
        loaded_repo1 = github2.repositories["testuser/repo1"]
        loaded_repo2 = github2.repositories["testuser/repo2"]
        assert loaded_repo1.owner.login == loaded_repo2.owner.login
        assert loaded_repo1.owner.login == "testuser"

def test_graphql_functionality() -> None:
    """Test GraphQL functionality."""
    # Set up a temp directory for state storage
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create the expected directory structure
        remote_dir = os.path.join(tmpdir, "remote.git")
        os.makedirs(remote_dir, exist_ok=True)
        
        # Initialize a bare git repository
        import subprocess
        subprocess.run(['git', 'init', '--bare', remote_dir], check=True)
        
        # Create the fake_github directory where state would normally be stored
        fake_github_dir = os.path.join(tmpdir, "teststack", ".git", "fake_github")
        os.makedirs(fake_github_dir, exist_ok=True)
        
        # Create a fake GitHub instance with state file in the expected location
        state_file = os.path.join(fake_github_dir, "fake_github_state.yaml")
        
        # Create a fresh GitHub instance with a clean state
        github: FakeGithub = create_fake_github(
            data_dir=Path(fake_github_dir),
            state_file=Path(state_file),
        )
        
        repo = github.get_repo("testorg/testrepo")
        assert isinstance(repo, FakeRepository)
        
        # Request GraphQL data with no PRs
        from pyspr.tests.e2e.fake_pygithub import FakeRequester
        from typing import Any, Dict
        # Access requester using public API
        requester: FakeRequester = getattr(github, '_Github__requester')
        _: Dict[str, Any]
        response: Dict[str, Any]
        _, response = requester.requestJsonAndCheck(
            "POST", 
            "https://api.github.com/graphql", 
            input={"query": "query { viewer { login pullRequests { nodes { number } } } }"}
        )
        
        # Verify response structure
        assert "data" in response
        assert "search" in response["data"]
        assert "nodes" in response["data"]["search"]
        pr_nodes: list[Dict[str, Any]] = response["data"]["search"]["nodes"]
        assert len(pr_nodes) == 0  # No PRs yet