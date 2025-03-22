"""Local Git repository setup with mock GitHub for testing."""

import os
import tempfile
import uuid
import logging
import subprocess
from typing import Generator, Optional, Dict, Any

from pyspr.config import Config
from pyspr.git import RealGit
from pyspr.github import GitHubClient
from pyspr.tests.e2e.test_helpers import RepoContext, run_cmd
from pyspr.tests.e2e.mock_setup import create_github_client

logger = logging.getLogger(__name__)

def create_mock_repo_context(owner: str, name: str, test_name: str) -> Generator[RepoContext, None, None]:
    """Create a local repository context with a file remote for testing.
    
    Args:
        owner: Repo owner
        name: Repo name
        test_name: Test function name (for tag generation)
    
    Yields:
        RepoContext: Repository context with local Git remote and mock GitHub client
    """
    # Always use temp branch for isolation
    orig_dir = os.getcwd()
    test_type = test_name.replace('test_', '')
    unique_tag = f"test-{test_type}-{uuid.uuid4().hex[:8]}"
    
    repo_name = f"{owner}/{name}"
    test_branch = f"test-spr-{uuid.uuid4().hex[:7]}" 
    logger.info(f"Using test branch {test_branch} for local test repo")
    
    ctx = None
    
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a bare repository that will serve as our remote
            remote_dir = os.path.join(tmpdir, "remote.git")
            os.makedirs(remote_dir)
            run_cmd(f"git init --bare {remote_dir}")
            
            # Create a separate directory for our working repository
            repo_dir = os.path.join(tmpdir, name)
            os.mkdir(repo_dir)
            os.chdir(repo_dir)
            
            # Initialize git and set the remote
            run_cmd("git init")
            file_remote_url = f"file://{remote_dir}"
            run_cmd(f"git remote add origin {file_remote_url}")
            
            # Add an initial commit and push to establish main branch
            run_cmd("git config user.name 'Test User'")
            run_cmd("git config user.email 'test@example.com'")
            
            # Create initial file
            with open("README.md", "w") as f:
                f.write(f"# {name} test repository\n\nUsed for automated testing.")
            
            # Commit and push
            run_cmd("git add README.md")
            run_cmd("git commit -m 'Initial commit'")
            
            # Create and checkout main branch
            run_cmd("git branch -M main")
            run_cmd("git push -u origin main")
            
            # Create test branch
            run_cmd(f"git checkout -b {test_branch}")
            run_cmd(f"git push -u origin {test_branch}")
            
            # Create local branch for tests
            run_cmd("git checkout -b test_local")
            
            repo_dir = os.path.abspath(os.getcwd())
            
            # Create a .spr.yaml file in the repo to ensure config is read by subprocesses
            config_dict = {
                'repo': {
                    'github_remote': 'origin',
                    'github_branch': 'main',
                    'github_branch_target': 'main',
                    'github_repo_owner': owner,
                    'github_repo_name': name,
                    'use_mock_github': True,
                    'mock_remote_url': file_remote_url
                },
                'user': {}
            }
            
            # Write .spr.yaml file
            import yaml
            with open('.spr.yaml', 'w') as f:
                yaml.dump(config_dict, f)
            
            # Add .spr.yaml to git
            run_cmd("git add .spr.yaml")
            run_cmd("git commit -m 'Add .spr.yaml for testing'")
            
            # Create config - this will be used by RealGit and GitHubClient
            config = Config(config_dict)
            
            # Create git and GitHub clients
            git_cmd = RealGit(config)
            
            # Create GitHub client using our mock_setup helper
            github = create_github_client(None, config)
            
            # Create and return RepoContext
            ctx = RepoContext(
                owner=owner,
                name=name,
                branch=test_branch,
                repo_dir=repo_dir,
                tag=unique_tag,
                git_cmd=git_cmd,
                github=github
            )
            
            yield ctx
            
            # Clean up branches
            try:
                run_cmd("git checkout main")
                run_cmd(f"git branch -D {test_branch} || true")
                run_cmd(f"git push origin --delete {test_branch} || true")
            except subprocess.CalledProcessError as e:
                logger.warning(f"Error during cleanup: {e}")
            
    finally:
        os.chdir(orig_dir)