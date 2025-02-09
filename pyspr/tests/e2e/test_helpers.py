"""Test helpers for e2e tests."""
import os
import subprocess
import uuid
import tempfile
import yaml
import logging
from typing import Generator, List, Tuple, Optional

from pyspr.git import RealGit
from pyspr.github import GitHubClient, PullRequest

log = logging.getLogger(__name__)

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

def create_test_repo(owner: str, name: str, use_temp_branch: bool = True) -> Generator[Tuple[str, str, str, str], None, None]:
    """Create a test repo fixture.
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

        os.chdir(orig_dir)