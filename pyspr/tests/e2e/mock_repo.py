"""Local Git repository setup with mock GitHub for testing."""

import os
import tempfile
import uuid
import logging
import subprocess
from typing import Generator, Dict

from pyspr.config import Config
from pyspr.git import RealGit
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
    
    test_branch = f"test-spr-{uuid.uuid4().hex[:7]}" 
    logger.info(f"Using test branch {test_branch} for local test repo")
    logger.info(f"Starting in directory: {orig_dir}")
    
    ctx = None
    tmpdir = None
    
    try:
        # Check if we should preserve state between runs
        preserve_state = os.environ.get("SPR_PRESERVE_FAKE_GITHUB_STATE", "").lower() == "true"
        
        if preserve_state:
            # Use a persistent directory based on test name
            tmpdir = os.path.join(tempfile.gettempdir(), f"pyspr_test_persistent_{test_name}")
            if not os.path.exists(tmpdir):
                os.makedirs(tmpdir)
            logger.info(f"Using PERSISTENT directory for state preservation: {tmpdir}")
        else:
            # Use manual temporary directory to prevent cleanup so we can debug the state file
            tmpdir = tempfile.mkdtemp(prefix="pyspr_test_")
            logger.info(f"Using temporary directory: {tmpdir}")
        
        # Create a bare repository that will serve as our remote
        remote_dir = os.path.join(tmpdir, "remote.git")
        repo_dir = os.path.join(tmpdir, name)
        
        # Check if this is a second run with existing state
        is_second_run = preserve_state and os.path.exists(repo_dir) and os.path.exists(remote_dir)
        
        if is_second_run:
            logger.info(f"Reusing existing repository at {repo_dir}")
            os.chdir(repo_dir)
            # Reset to main branch for a clean start
            run_cmd("git checkout main")
            run_cmd("git pull origin main")
        else:
            # First run or non-persistent mode - create fresh repos
            if not os.path.exists(remote_dir):
                os.makedirs(remote_dir)
                run_cmd(f"git init --bare {remote_dir}")
            
            if not os.path.exists(repo_dir):
                os.mkdir(repo_dir)
            os.chdir(repo_dir)
            logger.info(f"Changed to repository directory: {repo_dir}")
            
            # Initialize git and set the remote
            run_cmd("git init")
            file_remote_url = f"file://{remote_dir}"
            run_cmd(f"git remote add origin {file_remote_url}")
        
        if not is_second_run:
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
        
        # Always need file_remote_url
        file_remote_url = f"file://{remote_dir}"
        
        if not is_second_run:
            # Create a .spr.yaml file in the repo to ensure config is read by subprocesses
            config_dict: Dict[str, Dict[str, object]] = {
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
            
            # Add .spr.yaml to git and push to main
            run_cmd("git add .spr.yaml")
            run_cmd("git commit -m 'Add .spr.yaml for testing'")
            run_cmd("git push origin main")
        else:
            # Load existing config
            import yaml
            with open('.spr.yaml', 'r') as f:
                config_dict = yaml.safe_load(f)
        
        if is_second_run:
            # For second run, create a new test branch with different name
            test_branch = f"test-spr-run2-{uuid.uuid4().hex[:7]}"
            logger.info(f"Second run: using new test branch {test_branch}")
        
        # Create test branch from updated main
        run_cmd(f"git checkout -b {test_branch}")
        run_cmd(f"git push -u origin {test_branch}")
        
        # Create or reset local branch for tests
        if is_second_run:
            # Delete old test_local if it exists
            run_cmd("git branch -D test_local || true")
        run_cmd("git checkout -b test_local")
        
        repo_dir = os.path.abspath(os.getcwd())
        
        # Create config - this will be used by RealGit and GitHubClient
        config = Config(config_dict)
        
        # Create git and GitHub clients
        git_cmd = RealGit(config)
        
        # Create GitHub client using our mock_setup helper
        # Force mock GitHub for tests to ensure consistency
        github = create_github_client(None, config, force_mock=True)
        
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
        logger.info(f"Changing back to original directory: {orig_dir}")
        os.chdir(orig_dir)
        logger.info(f"Test completed. Temp directory: {tmpdir}")
        # Note: We purposely do not clean up the temp directory so we can examine the state file