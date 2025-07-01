"""CLI entry point."""

import os
import sys
import click
import logging
from typing import List, Optional, Tuple, Dict, Any
from click import Context

from ...config import Config, default_config
from ...config.config_parser import parse_config
from ...config.models import PysprConfig
from ...git import RealGit
from ...github import GitHubClient 
from ...spr import StackedPR
from ...typing import GitInterface, StackedPRContextProtocol

# Get module logger
logger = logging.getLogger(__name__)

# Import from tests - only used when running in test mode
# Define mock availability flag
is_mock_available = False

try:
    from ...tests.e2e.mock_setup import create_github_client, should_use_mock_github
    is_mock_available = True
except ImportError:
    # Define stub functions to avoid unbound variable errors
    def should_use_mock_github() -> bool:
        return False
        
    def create_github_client(ctx: Optional[StackedPRContextProtocol], config: PysprConfig, force_mock: bool = False) -> GitHubClient:
        # This function should never be called when is_mock_available is False
        # but we need to define it to avoid unbound variable errors
        return GitHubClient(ctx, config)
        
    logger.debug("Mock GitHub not available - running with real GitHub")

def check(err: Exception) -> None:
    """Check for error and exit if needed."""
    if err:
        logger.error(f"{err}")
        sys.exit(1)

class AliasedGroup(click.Group):
    """Command group with support for aliases."""
    
    def __init__(self, name: Optional[str] = None, commands: Optional[Dict[str, click.Command]] = None, **attrs: Any) -> None:
        """Initialize with aliases map."""
        super().__init__(name, commands, **attrs)
        self.aliases: Dict[str, str] = {}

    def add_alias(self, alias: str, command: str) -> None:
        """Add an alias for a command."""
        self.aliases[alias] = command

    def get_command(self, ctx: Context, cmd_name: str) -> Optional[click.Command]:
        """Get a command by name, supporting aliases."""
        # Check if cmd_name is a registered alias
        if cmd_name in self.aliases:
            cmd_name = self.aliases[cmd_name]
        return super().get_command(ctx, cmd_name)

@click.group(cls=AliasedGroup)
@click.pass_context
def cli(ctx: Context) -> None:
    """SPR - Stacked Pull Requests on GitHub."""
    ctx.obj = {}

def restore_git_state(git_cmd: GitInterface, branch: str, head: str) -> None:
    """Attempt to restore git to a known good state."""
    logger.info("Attempting to restore repository state...")
    
    # First abort any in-progress operations
    for abort_cmd in ["cherry-pick --abort", "rebase --abort", "merge --abort"]:
        try:
            git_cmd.run_cmd(abort_cmd)
        except Exception:
            pass
    
    # Try to checkout original branch
    try:
        git_cmd.must_git(f"checkout {branch}")
    except Exception:
        try:
            git_cmd.must_git(f"checkout -f {branch}")
        except Exception:
            logger.error(f"Failed to checkout {branch}")
    
    # Reset to original HEAD
    try:
        git_cmd.must_git(f"reset --hard {head}")
        logger.info("Repository restored to original state")
    except Exception as e:
        logger.error(f"Failed to reset to {head}: {e}")
        logger.error("Repository may be in an inconsistent state")
        logger.error(f"To manually restore: git checkout {branch} && git reset --hard {head}")

def setup_git(directory: Optional[str] = None) -> Tuple[Config, RealGit, GitHubClient]:
    """Setup Git command and config."""
    if directory:
        os.chdir(directory)
        
    # Check git dir
    git_cmd = RealGit(default_config())
    try:
        output = git_cmd.run_cmd("rev-parse --git-dir")
        if not output or 'not a git repository' in output.lower():
            raise Exception("Not in a git repository")
    except Exception as e:
        check(e)
        sys.exit(2)

    cfg = parse_config(git_cmd)
    config = Config(cfg)
    git_cmd = RealGit(config)
    
    # Use mock GitHub if available and not explicitly disabled
    if is_mock_available and should_use_mock_github():
        logger.info("Using mock GitHub client")
        # Explicitly set force_mock=False to rely on environment variables
        github = create_github_client(None, config, force_mock=False)
    else:
        logger.info("Using real GitHub client")
        # Need to create real PyGithub client with adapter
        from ...github import find_github_token
        from ...github.adapters import PyGithubAdapter
        from github import Github
        
        token = find_github_token()
        if not token:
            error_msg = "No GitHub token found. Try one of:\n1. Set GITHUB_TOKEN env var\n2. Log in with 'gh auth login'\n3. Put token in /home/ubuntu/code/pyspr/token file"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        # Create a real PyGithub client wrapped in our adapter
        real_github = Github(token)
        github_client = PyGithubAdapter(real_github)
        github = GitHubClient(None, config, github_client=github_client)

    return config, git_cmd, github

@cli.command(name="update", help="Update and create pull requests for updated commits in the stack")
@click.option('-C', '--directory', type=click.Path(exists=True, file_okay=False, dir_okay=True),
              help='Run as if spr was started in DIRECTORY instead of the current working directory')
@click.option('--reviewer', '-r', multiple=True, 
              help="Add the specified reviewer to newly created pull requests")
@click.option('--count', '-c', type=int,
              help="Update a specified number of pull requests from the bottom of the stack")
@click.option('--no-rebase', '-nr', is_flag=True, help="Disable rebasing")
@click.option('--label', '-l', multiple=True,
              help="Add the specified label to new pull requests")
@click.option('-v', '--verbose', count=True, help="Increase verbosity (can be used multiple times for more verbosity)")
@click.option('--pretend', is_flag=True, help="Don't actually push or create/update pull requests, just show what would happen")
@click.pass_context
def update(ctx: Context, directory: Optional[str], reviewer: List[str], 
          count: Optional[int], no_rebase: bool, label: List[str], verbose: int, pretend: bool) -> None:
    """Update command."""
    from ... import setup_logging
    setup_logging(verbose)
    
    config, git_cmd, github = setup_git(directory)
    config.tool.pretend = pretend  # Set pretend mode
    
    # Save current git state for recovery
    try:
        current_branch = git_cmd.must_git("rev-parse --abbrev-ref HEAD").strip()
        current_head = git_cmd.must_git("rev-parse HEAD").strip()
    except Exception as e:
        logger.error(f"Failed to get current git state: {e}")
        sys.exit(1)
    
    try:
        if no_rebase:
            config.user.no_rebase = True
        stackedpr = StackedPR(config, github, git_cmd)
        stackedpr.pretend = pretend  # Set pretend mode
        stackedpr.update_pull_requests(ctx, reviewer if reviewer else None, count, labels=list(label) if label else None)
    except Exception as e:
        logger.error(f"Error during update: {e}")
        restore_git_state(git_cmd, current_branch, current_head)
        sys.exit(1)

@cli.command(name="status", help="Show status of open pull requests")
@click.option('-C', '--directory', type=click.Path(exists=True, file_okay=False, dir_okay=True),
              help='Run as if spr was started in DIRECTORY instead of the current working directory')
@click.option('-v', '--verbose', count=True, help="Increase verbosity (can be used multiple times for more verbosity)")
@click.pass_context
def status(ctx: Context, directory: Optional[str], verbose: int) -> None:
    """Status command."""
    from ... import setup_logging
    setup_logging(verbose)
    
    config, git_cmd, github = setup_git(directory)
    stackedpr = StackedPR(config, github, git_cmd)
    stackedpr.status_pull_requests(ctx)

@cli.command(name="merge", help="Merge all mergeable pull requests")
@click.option('-C', '--directory', type=click.Path(exists=True, file_okay=False, dir_okay=True),
              help='Run as if spr was started in DIRECTORY instead of the current working directory')
@click.option('--count', '-c', type=int,
              help="Merge a specified number of pull requests from the bottom of the stack")
@click.option('--no-rebase', '-nr', is_flag=True, help="Disable rebasing")
@click.option('-v', '--verbose', count=True, help="Increase verbosity (can be used multiple times for more verbosity)")
@click.pass_context
def merge(ctx: Context, directory: Optional[str], count: Optional[int], no_rebase: bool, verbose: int) -> None:
    """Merge command."""
    from ... import setup_logging
    setup_logging(verbose)
    
    config, git_cmd, github = setup_git(directory)
    
    if no_rebase:
        config.user.no_rebase = True
    stackedpr = StackedPR(config, github, git_cmd)
    stackedpr.merge_pull_requests(ctx, count)
    # Don't update after merge - this would create new PRs

@cli.command(name="breakup", help="Break up current commit stack into independent branches/PRs")
@click.option('-C', '--directory', type=click.Path(exists=True, file_okay=False, dir_okay=True),
              help='Run as if spr was started in DIRECTORY instead of the current working directory')
@click.option('-v', '--verbose', count=True, help="Increase verbosity (can be used multiple times for more verbosity)")
@click.option('--pretend', is_flag=True, help="Don't actually push or create/update pull requests, just show what would happen")
@click.option('--no-rebase', '-nr', is_flag=True, help="Disable rebasing on latest upstream")
@click.option('--reviewer', '-r', multiple=True, 
              help="Add the specified reviewer to newly created pull requests")
@click.option('--count', '-c', type=int,
              help="Break up a specified number of commits from the bottom of the stack")
@click.option('--update-only-these-ids', type=str,
              help="Only update PRs for specific commit IDs (comma-separated)")
@click.option('--stacks', is_flag=True,
              help="Create multiple PR stacks based on commit dependencies (strongly connected components)")
@click.option('--stack-mode', type=click.Choice(['components', 'trees', 'stacks'], case_sensitive=False),
              default='components', 
              help="Algorithm for --stacks: 'components' (scenario 1), 'trees' (scenario 2), or 'stacks' (scenario 3)")
@click.pass_context
def breakup(ctx: Context, directory: Optional[str], verbose: int, pretend: bool, no_rebase: bool, reviewer: List[str], count: Optional[int], update_only_these_ids: Optional[str], stacks: bool, stack_mode: str) -> None:
    """Breakup command."""
    from ... import setup_logging
    setup_logging(verbose)
    
    config, git_cmd, github = setup_git(directory)
    config.tool.pretend = pretend  # Set pretend mode
    
    # Save current git state for recovery
    try:
        current_branch = git_cmd.must_git("rev-parse --abbrev-ref HEAD").strip()
        current_head = git_cmd.must_git("rev-parse HEAD").strip()
    except Exception as e:
        logger.error(f"Failed to get current git state: {e}")
        sys.exit(1)
    
    try:
        if no_rebase:
            config.user.no_rebase = True
        stackedpr = StackedPR(config, github, git_cmd)
        stackedpr.pretend = pretend  # Set pretend mode
        
        # Parse commit IDs if provided
        commit_ids = None
        if update_only_these_ids:
            commit_ids = [id.strip() for id in update_only_these_ids.split(',') if id.strip()]
        
        stackedpr.breakup_pull_requests(ctx, reviewer if reviewer else None, count, commit_ids, stacks, stack_mode)
    except Exception as e:
        logger.error(f"Error during breakup: {e}")
        restore_git_state(git_cmd, current_branch, current_head)
        sys.exit(1)

@cli.command(name="analyze", help="Analyze which commits can be independently submitted without stacking")
@click.option('-C', '--directory', type=click.Path(exists=True, file_okay=False, dir_okay=True),
              help='Run as if spr was started in DIRECTORY instead of the current working directory')
@click.option('-v', '--verbose', count=True, help="Increase verbosity (can be used multiple times for more verbosity)")
@click.pass_context
def analyze(ctx: Context, directory: Optional[str], verbose: int) -> None:
    """Analyze command."""
    from ... import setup_logging
    setup_logging(verbose)
    
    config, git_cmd, github = setup_git(directory)
    
    # Save current git state
    current_branch = git_cmd.must_git("rev-parse --abbrev-ref HEAD").strip()
    current_head = git_cmd.must_git("rev-parse HEAD").strip()
    
    try:
        stackedpr = StackedPR(config, github, git_cmd)
        stackedpr.analyze(ctx)
    finally:
        # Always restore git state
        restore_git_state(git_cmd, current_branch, current_head)


def main() -> None:
    """Main entry point."""
    # Add command aliases
    cli.aliases['up'] = 'update'
    cli.aliases['st'] = 'status'
    cli(obj={})

if __name__ == "__main__":
    main()