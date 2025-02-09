"""Test helpers for e2e tests."""
import os
import subprocess
import uuid
import tempfile
import yaml
import logging
from dataclasses import dataclass
import pytest
from typing import Generator, List, Tuple, Optional, Union

from pyspr.config import Config
from pyspr.git import RealGit
from pyspr.github import GitHubClient, PullRequest

log = logging.getLogger(__name__)

@dataclass
class RepoContext:
    """Test repository context with helpers for test operations."""
    owner: str
    name: str
    branch: str
    repo_dir: str
    tag: str
    git_cmd: RealGit
    github: GitHubClient

    def make_commit(self, file: str, content: str, msg: str) -> str:
        """Create a commit with the test tag embedded."""
        full_msg = f"{msg} [test-tag:{self.tag}]"
        full_path = os.path.join(self.repo_dir, file)
        with open(full_path, "w") as f:
            f.write(f"{file}\n{content}\n")
        run_cmd(f"git add {file}")
        run_cmd(f'git commit -m "{full_msg}"')
        return self.git_cmd.must_git("rev-parse HEAD").strip()

    def get_test_prs(self) -> List[PullRequest]:
        """Get PRs filtered by this test's tag."""
        result: List[PullRequest] = []
        info = self.github.get_info(None, self.git_cmd)
        if not info:
            return result
        for pr in info.pull_requests:
            if pr.from_branch and pr.from_branch.startswith('spr/main/'):
                try:
                    commit_msg = self.git_cmd.must_git(f"show -s --format=%B {pr.commit.commit_hash}")
                    if f"test-tag:{self.tag}" in commit_msg:
                        log.info(f"Found PR #{pr.number} with tag and commit ID {pr.commit.commit_id}")
                        result.append(pr)
                except Exception as e:
                    log.info(f"Error checking PR: {e}")
                    pass
        return result

def get_gh_token() -> str:
    """Get GitHub token from gh CLI config."""
    try:
        result = subprocess.run(['gh', 'auth', 'token'], check=True, capture_output=True, text=True)
        token = result.stdout.strip()
        if token:
            return token
    except subprocess.CalledProcessError:
        pass

    try:
        gh_config_path = os.path.expanduser("~/.config/gh/hosts.yml")
        if os.path.exists(gh_config_path):
            with open(gh_config_path) as f:
                config = yaml.safe_load(f)
                if config and "github.com" in config:
                    github_config = config["github.com"]
                    if "oauth_token" in github_config:
                        return github_config["oauth_token"]
    except Exception as e:
        log.info(f"Error reading gh config: {e}")

    raise Exception("Could not get GitHub token from gh CLI")

def run_cmd(cmd: str, cwd: Optional[str] = None) -> Optional[str]:
    """Run a shell command using subprocess with proper error handling."""
    # Find project root for rye commands
    project_root = None
    orig_cwd = os.getcwd()
    try:
        # Try to find project root by looking for pyproject.toml
        test_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        while test_dir != '/':
            if os.path.exists(os.path.join(test_dir, 'pyproject.toml')):
                project_root = test_dir
                break
            test_dir = os.path.dirname(test_dir)
    except:
        pass

    # Replace standalone pyspr command with rye run pyspr from project root
    actual_cwd = cwd
    if cmd.startswith("pyspr ") or cmd == "pyspr":
        cmd = "rye run " + cmd
        if project_root:
            actual_cwd = project_root
            cmd = f"cd {actual_cwd} && {cmd} -C {cwd}" if cwd else f"cd {actual_cwd} && {cmd} -C {orig_cwd}"
        
    log.info(f"Running command: {cmd}")
    try:
        result = subprocess.run(cmd, shell=True, check=True, capture_output=True, text=True, cwd=actual_cwd)
        if result.stdout.strip():
            log.info(f"STDOUT: {result.stdout.strip()}")
        return result.stdout
    except subprocess.CalledProcessError as e:
        log.error(f"Command failed with exit code {e.returncode}")
        if e.stdout:
            log.error(f"STDOUT: {e.stdout}")
        if e.stderr:
            log.error(f"STDERR: {e.stderr}")
        raise

def get_test_prs(git_cmd: RealGit, github: GitHubClient, unique_tag: str) -> List[PullRequest]:
    """Get test PRs filtered by unique tag."""
    log.info(f"Looking for PRs with tag: {unique_tag}")
    result: List[PullRequest] = []
    github_info = github.get_info(None, git_cmd)
    if not github_info:
        return result
    for pr in github_info.pull_requests:
        if pr.from_branch and pr.from_branch.startswith('spr/main/'):
            try:
                commit_msg = git_cmd.must_git(f"show -s --format=%B {pr.commit.commit_hash}")
                if f"test-tag:{unique_tag}" in commit_msg:
                    log.info(f"Found PR #{pr.number} with tag and commit ID {pr.commit.commit_id}")
                    result.append(pr)
            except:
                pass
    return result

def create_repo_context(owner: str, name: str, test_name: str) -> Generator[RepoContext, None, None]:
    """Base fixture factory for creating repo contexts.
    Args:
        owner: Repo owner
        name: Repo name
        test_name: Test function name (for tag generation)
    """
    # Always use temp branch for isolation
    orig_dir = os.getcwd()
    test_type = test_name.replace('test_', '')
    unique_tag = f"test-{test_type}-{uuid.uuid4().hex[:8]}"
    
    repo_name = f"{owner}/{name}"
    test_branch = f"test-spr-{uuid.uuid4().hex[:7]}" 
    log.info(f"Using test branch {test_branch} in {repo_name}")
    
    # Get token
    token = get_gh_token()
    os.environ["GITHUB_TOKEN"] = token

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            os.chdir(tmpdir)
            
            # Clone via SSH to avoid hangs
            ssh_url = f"git@github.com:{repo_name}.git"
            run_cmd(f"git clone {ssh_url}")
            os.chdir(name)

            # Branch setup
            run_cmd(f"git checkout -b {test_branch}")
            run_cmd("git checkout -b test_local")  # Local branch for tests

            # Git config
            run_cmd("git config user.name 'Test User'")
            run_cmd("git config user.email 'test@example.com'")
            
            repo_dir = os.path.abspath(os.getcwd())
            
            # Create context objects
            config = Config({
                'repo': {
                    'github_remote': 'origin',
                    'github_branch': 'main',
                    'github_repo_owner': owner,
                    'github_repo_name': name,
                },
                'user': {}
            })
            git_cmd = RealGit(config)
            github = GitHubClient(None, config)
            
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

            # Cleanup
            run_cmd("git checkout main")
            run_cmd(f"git branch -D {test_branch}")
            try:
                run_cmd(f"git push origin --delete {test_branch}")
            except subprocess.CalledProcessError:
                log.info(f"Failed to delete remote branch {test_branch}, may not exist")
    finally:
        os.chdir(orig_dir)

@pytest.fixture
def test_repo_ctx(request) -> Generator[RepoContext, None, None]:
    """Regular test repo fixture using yang/teststack."""
    yield from create_repo_context("yang", "teststack", request.node.name)

@pytest.fixture
def test_mq_repo_ctx(request) -> Generator[RepoContext, None, None]:
    """Merge queue test repo fixture using yangenttest1/teststack."""
    yield from create_repo_context("yangenttest1", "teststack", request.node.name)

def create_test_repo(owner: str, name: str, use_temp_branch: bool = True) -> Generator[Tuple[str, str, str, str], None, None]:
    """Legacy test repo fixture for backward compatibility.
    Args:
        owner: Repo owner
        name: Repo name
        use_temp_branch: Whether to create a unique test branch
    """
    orig_dir = os.getcwd()
    repo_name = f"{owner}/{name}"
    test_branch = f"test-spr-{uuid.uuid4().hex[:7]}" if use_temp_branch else None
    log.info(f"Using {'test branch ' + str(test_branch) if use_temp_branch else 'main branch'} in {repo_name}")
    
    # Get token
    token = get_gh_token()
    os.environ["GITHUB_TOKEN"] = token

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            os.chdir(tmpdir)
            
            # Clone via SSH to avoid hangs
            ssh_url = f"git@github.com:{repo_name}.git"
            run_cmd(f"git clone {ssh_url}")
            os.chdir(name)

            # Branch setup
            if use_temp_branch:
                run_cmd(f"git checkout -b {test_branch}")
            run_cmd("git checkout -b test_local")  # Local branch for tests

            # Git config
            run_cmd("git config user.name 'Test User'")
            run_cmd("git config user.email 'test@example.com'")
            
            repo_dir = os.path.abspath(os.getcwd())
            yield owner, name, test_branch if test_branch else "main", repo_dir

            # Cleanup
            if use_temp_branch and test_branch:
                run_cmd("git checkout main")
                run_cmd(f"git branch -D {test_branch}")
                try:
                    run_cmd(f"git push origin --delete {test_branch}")
                except subprocess.CalledProcessError:
                    log.info(f"Failed to delete remote branch {test_branch}, may not exist")
    finally:
        os.chdir(orig_dir)

def create_test_repo(owner: str, name: str) -> Generator[Tuple[str, str, str, str], None, None]:
    """Legacy test repo fixture factory for tests that haven't been migrated to RepoContext yet.
    This provides the same tuple output as the old fixture for compatibility.
    
    Args:
        owner: Repo owner
        name: Repo name
    
    Returns:
        Tuple of (owner, name, test_branch, repo_dir)
    """
    orig_dir = os.getcwd()
    repo_name = f"{owner}/{name}"
    test_branch = f"test-spr-{uuid.uuid4().hex[:7]}" 
    
    # Get token
    token = get_gh_token()
    os.environ["GITHUB_TOKEN"] = token

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            os.chdir(tmpdir)
            
            # Clone via SSH to avoid hangs
            ssh_url = f"git@github.com:{repo_name}.git"
            run_cmd(f"git clone {ssh_url}")
            os.chdir(name)

            # Branch setup
            run_cmd(f"git checkout -b {test_branch}")
            run_cmd("git checkout -b test_local")  # Local branch for tests

            # Git config
            run_cmd("git config user.name 'Test User'")
            run_cmd("git config user.email 'test@example.com'")
            
            repo_dir = os.path.abspath(os.getcwd())
            
            yield owner, name, test_branch, repo_dir

            # Cleanup
            run_cmd("git checkout main")
            run_cmd(f"git branch -D {test_branch}")
            try:
                run_cmd(f"git push origin --delete {test_branch}")
            except subprocess.CalledProcessError:
                log.info(f"Failed to delete remote branch {test_branch}, may not exist")
    finally:
        os.chdir(orig_dir)