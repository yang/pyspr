"""Test helpers for e2e tests."""
import os
import subprocess
import uuid
import tempfile
import yaml
import logging
from dataclasses import dataclass, field
import pytest
from _pytest.fixtures import FixtureRequest
from typing import Generator, List, Tuple, Optional, Union, Any, Callable, TYPE_CHECKING, Dict

from pyspr.config import Config
from pyspr.git import RealGit
from pyspr.github import GitHubClient, PullRequest

if TYPE_CHECKING:
    from pyspr.github import GitHubPullRequestProtocol

import time
import datetime

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
    obj: Dict[str, object] = field(default_factory=dict)  # For protocol compatibility

    def make_commit(self, file: str, content: str, msg: str) -> str:
        """Create a commit with the test tag embedded."""
        full_msg = f"{msg} [test-tag:{self.tag}]"
        # Make filename unique by including part of the tag
        tag_suffix = self.tag.split('-')[-1][:8]  # Use last 8 chars of tag
        unique_file = f"{file}.{tag_suffix}" if not file.endswith(tag_suffix) else file
        full_path = os.path.join(self.repo_dir, unique_file)
        try:
            with open(full_path, "w") as f:
                f.write(f"{unique_file}\n{content}\n")
            run_cmd(f"git add {unique_file}")
            run_cmd(f'git commit -m "{full_msg}"')
            return self.git_cmd.must_git("rev-parse HEAD").strip()
        except subprocess.CalledProcessError as e:
            log.error(f"Commit failed: {e}")
            self.dump_git_state()
            self.dump_dir_contents()
            raise

    def get_test_prs(self) -> List[PullRequest]:
        """Get PRs filtered by this test's tag."""
        result: List[PullRequest] = []
        log.info(f"Looking for PRs with tag: {self.tag}")
        
        # Debug: Check the state of the fake GitHub
        if hasattr(self.github, 'client') and hasattr(self.github.client, '_repos'):
            repos = getattr(self.github.client, '_repos', {})
            for repo_name, repo in repos.items():
                pulls = getattr(repo, '_pulls', {})
                log.info(f"Repo {repo_name} has {len(pulls)} PRs")
                for pr_num, pr in pulls.items():
                    log.info(f"PR #{pr_num}: {pr.title}")
        
        # Otherwise continue with standard approach
        info = self.github.get_info(None, self.git_cmd)
        if not info:
            log.warning("GitHub info is None")
            return result
            
        log.info(f"Found {len(info.pull_requests)} PRs in info")
        for pr in info.pull_requests:
            log.info(f"Checking PR #{pr.number} with commit hash {pr.commit.commit_hash}")
            if pr.from_branch and (pr.from_branch.startswith('spr/main/') or pr.from_branch.startswith('pyspr/cp/main/')):
                try:
                    # Check the commit message for test tag
                    commit_msg = self.git_cmd.must_git(f"show -s --format=%B {pr.commit.commit_hash}")
                    log.info(f"PR #{pr.number} commit message: {commit_msg}")
                    if f"test-tag:{self.tag}" in commit_msg:
                        log.info(f"Found PR #{pr.number} with tag '{self.tag}' and commit ID {pr.commit.commit_id}")
                        result.append(pr)
                    else:
                        log.info(f"PR #{pr.number} does not have tag '{self.tag}'")
                except Exception as e:
                    log.info(f"Error checking PR #{pr.number}: {e}")
                    pass
            else:
                log.info(f"PR #{pr.number} branch '{pr.from_branch}' doesn't start with 'spr/main/'")
        
        log.info(f"Final result: found {len(result)} PRs with tag '{self.tag}'")
        return result

    def dump_git_state(self) -> None:
        """Dump git state for debugging."""
        try:
            log.info("=== Git State Debug Info ===")
            log.info("Git log:")
            log.info(self.git_cmd.must_git("log --oneline -n 3"))
            log.info("Remote branches:")
            log.info(self.git_cmd.must_git("ls-remote --heads origin"))
            log.info("Local branches:")
            log.info(self.git_cmd.must_git("branch -vv"))
            log.info("Git status:")
            log.info(self.git_cmd.must_git("status"))
        except Exception as e:
            log.error(f"Failed to dump git state: {e}")

    def dump_dir_contents(self) -> None:
        """Dump directory contents for debugging."""
        try:
            log.info("=== Directory Contents ===")
            log.info(run_cmd("ls -la", cwd=self.repo_dir))
        except Exception as e:
            log.error(f"Failed to dump directory contents: {e}")

    def dump_pr_state(self) -> None:
        """Dump PR state for debugging."""
        try:
            log.info("=== PR State Debug Info ===")
            info = self.github.get_info(None, self.git_cmd)
            if info and info.pull_requests:
                for pr in sorted(info.pull_requests, key=lambda pr: pr.number):
                    if self.github.repo:
                        gh_pr = self.github.repo.get_pull(pr.number)
                        log.info(f"PR #{pr.number}:")
                        log.info(f"  Title: {gh_pr.title}")
                        log.info(f"  Base: {gh_pr.base.ref}")
                        log.info(f"  State: {gh_pr.state}")
                        log.info(f"  Merged: {gh_pr.merged}")
                        self.dump_review_requests(gh_pr)
        except Exception as e:
            log.error(f"Failed to dump PR state: {e}")

    def dump_review_requests(self, pr: 'GitHubPullRequestProtocol') -> None:
        """Dump review requests for a PR."""
        try:
            requested_users, requested_teams = pr.get_review_requests()
            log.info("  Review requests:")
            if requested_users:
                requested_logins = [u.login.lower() for u in requested_users]
                log.info(f"    Users: {requested_logins}")
            if requested_teams:
                team_slugs = [t.slug.lower() for t in requested_teams]
                log.info(f"    Teams: {team_slugs}")
        except Exception as e:
            log.error(f"Failed to get review requests: {e}")

    def timed_operation(self, operation_name: str, func: Callable[[], Any]) -> Any:
        """Run a function with timing information."""
        log.info(f"{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]} Starting {operation_name}...")
        start_time = time.time()
        try:
            result = func()
            end_time = time.time()
            log.info(f"{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]} {operation_name} completed in {end_time - start_time:.2f} seconds")
            return result
        except Exception as e:
            end_time = time.time()
            log.error(f"{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]} {operation_name} failed after {end_time - start_time:.2f} seconds: {e}")
            self.dump_git_state()
            self.dump_dir_contents()
            self.dump_pr_state()
            raise

def get_gh_token() -> str:
    """Get GitHub token from gh CLI config."""
    try:
        # Need custom run_cmd call since we can't use shell=True with array command
        token = subprocess.run(
            ['gh', 'auth', 'token'], 
            check=True, 
            capture_output=True, 
            text=True
        ).stdout.strip()
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

def run_cmd(cmd: str, cwd: Optional[str] = None, check: bool = True, 
           capture_output: bool = True) -> str:
    """Run a shell command using subprocess with consistent output capture and logging.
    
    Args:
        cmd: The command to run
        cwd: Working directory. If None, uses current directory
        check: If True, raises CalledProcessError on non-zero exit
        capture_output: If True, captures and returns stdout
        
    Returns:
        The command's stdout output as string if capture_output=True
        
    Raises:
        subprocess.CalledProcessError: If command fails and check=True
    """
    # Find project root for rye commands
    project_root = None
    orig_cwd = os.getcwd()
    try:
        # Try to find project root by looking for pyproject.toml
        test_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        while test_dir != '/' and test_dir != '':  # Empty check for Windows
            if os.path.exists(os.path.join(test_dir, 'pyproject.toml')):
                project_root = test_dir
                break
            test_dir = os.path.dirname(test_dir)
    except Exception:
        pass

    # Replace standalone pyspr command with rye run pyspr from project root
    actual_cwd = cwd
    if cmd.startswith("pyspr ") or cmd == "pyspr":
        # Preserve the current SPR_USING_MOCK_GITHUB setting
        mock_setting = os.environ.get("SPR_USING_MOCK_GITHUB", "true")
        cmd = f"SPR_USING_MOCK_GITHUB={mock_setting} rye run " + cmd
        if project_root:
            actual_cwd = project_root
            cmd = f"cd {actual_cwd} && {cmd} -C {cwd}" if cwd else f"cd {actual_cwd} && {cmd} -C {orig_cwd}"
        
    log.info(f"Running command: {cmd}")
    result = None
    try:
        result = subprocess.run(
            cmd, 
            shell=True, 
            check=check, 
            capture_output=capture_output, 
            text=True, 
            cwd=actual_cwd
        )
        # Always log stdout and stderr
        if result.stdout and result.stdout.strip():
            log.info(f"STDOUT: {result.stdout.strip()}")
        if result.stderr and result.stderr.strip():
            log.info(f"STDERR: {result.stderr.strip()}")
        return result.stdout or ""
    except subprocess.CalledProcessError as e:
        log.error(f"Command failed with exit code {e.returncode}")
        if e.stdout and e.stdout.strip():
            log.error(f"STDOUT: {e.stdout.strip()}")
        if e.stderr and e.stderr.strip():
            log.error(f"STDERR: {e.stderr.strip()}")
        raise
    finally:
        if result and not capture_output:
            return ""  # Return empty string for non-captured output

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
            except Exception:
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
    ctx: Union[RepoContext, None] = None

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
            # Import here to avoid circular imports
            from pyspr.tests.e2e.mock_setup import create_github_client
            # Create GitHub client - will use real GitHub since SPR_USING_MOCK_GITHUB=false
            github = create_github_client(None, config)
            
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
    except Exception as e:
        log.error(f"Test failed: {e}")
        if ctx:
            log.error("=== Test Failure Debug Information ===")
            ctx.dump_git_state()
            ctx.dump_dir_contents()
            ctx.dump_pr_state()
    finally:
        os.chdir(orig_dir)

@pytest.fixture
def test_repo_ctx(request: FixtureRequest) -> Generator[RepoContext, None, None]:
    """Regular test repo fixture using yang/teststack."""
    # Use a combination of available attributes to create a unique test identifier
    test_identifier = f"{request.fixturename}_{request.scope}"
    yield from create_repo_context("yang", "teststack", test_identifier)

@pytest.fixture
def test_mq_repo_ctx(request: FixtureRequest) -> Generator[RepoContext, None, None]:
    """Merge queue test repo fixture using yangenttest1/teststack."""
    # Use a combination of available attributes to create a unique test identifier
    test_identifier = f"{request.fixturename}_{request.scope}"
    yield from create_repo_context("yangenttest1", "teststack", test_identifier)

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