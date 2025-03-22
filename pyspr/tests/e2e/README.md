# End-to-End Tests with Mock GitHub

This directory contains end-to-end tests for pyspr that can run with either mock or real GitHub API.

## Design Overview

The test system uses:

1. **Real Git repository with local remotes** - We use actual Git commands with `file://` protocol for remotes
2. **Fake PyGithub implementation** - Direct mocks of PyGithub classes that GitHubClient uses
3. **Pydantic serialization** - For state persistence between test runs
4. **Environment variable control** - Switch between mock and real GitHub using environment variables

## How to Run Tests

By default, all tests run with mock GitHub:

```bash
rye run pytest -xvs pyspr/tests/e2e/
```

To use real GitHub (requires GitHub token and real repositories):

```bash
SPR_USE_REAL_GITHUB=true rye run pytest -xvs pyspr/tests/e2e/
```

## Components

- `fake_pygithub.py` - Fake implementation of PyGithub classes
- `mock_github_module.py` - Interface to make fake PyGithub available
- `mock_repo.py` - Creates local Git repositories with file:// remotes
- `mock_setup.py` - Helper functions for the main application to use mock GitHub
- `fixtures.py` - Pytest fixtures that handle mock/real switching
- `conftest.py` - Ensures mock environment is properly set up

## How it Works

### In Tests

1. Tests use a `RepoContext` object that contains Git and GitHub clients
2. When running with mock GitHub, the context uses:
   - Real Git with local file:// remotes
   - Regular `GitHubClient` with our fake PyGithub implementation injected
3. The fake PyGithub implementation:
   - Stores state in JSON files (under `.git/fake_github`)
   - Simulates API responses for both REST and GraphQL
   - Persists between test runs

### In Main Application

The main pyspr application also supports using the mock GitHub client:

1. The `setup_git` function in `main.py` checks for `SPR_USE_REAL_GITHUB` environment variable
2. If not set to "true", it uses `create_github_client` from `mock_setup.py` to create a mock GitHub client
3. This means the `pyspr update` commands run during tests will use the same mock system

## How to Use in Your Own Tests

To use the mock GitHub in your own tests:

```python
from pyspr.tests.e2e.mock_repo import create_mock_repo_context

# Create a test repository with mock GitHub
with create_mock_repo_context("owner", "repo", "test_name") as repo_ctx:
    # Use repo_ctx.git_cmd for Git operations
    # Use repo_ctx.github for GitHub operations
    
    # Run pyspr commands - they'll automatically use mock GitHub
    run_cmd("pyspr update")
```

This approach makes tests fast and reliable without any GitHub API access while still testing the real application code.