"""CLI entry point."""

import logging
import os
import sys
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Tuple

import click
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

    def create_github_client(
        ctx: Optional[StackedPRContextProtocol],
        config: PysprConfig,
        force_mock: bool = False,
    ) -> GitHubClient:
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

    def __init__(
        self,
        name: Optional[str] = None,
        commands: Optional[Dict[str, click.Command]] = None,
        **attrs: Any,
    ) -> None:
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


class GitState:
    """Holds git state for restoration."""

    def __init__(self, branch: str, head: str, stash_ref: Optional[str] = None):
        self.branch = branch
        self.head = head
        self.stash_ref = stash_ref


def save_git_state(git_cmd: GitInterface) -> GitState:
    """Save current git state including uncommitted changes."""
    current_branch = git_cmd.must_git("rev-parse --abbrev-ref HEAD").strip()
    current_head = git_cmd.must_git("rev-parse HEAD").strip()

    # Check if there are any changes to stash
    stash_ref = None
    try:
        # Check for uncommitted changes
        status = git_cmd.must_git("status --porcelain")
        if status.strip():
            logger.info("Stashing uncommitted changes...")
            # Create a stash with a descriptive message
            stash_output = git_cmd.must_git(
                "stash push -m 'pyspr: auto-stash before command'"
            )
            # Extract stash reference from output
            if "Saved working directory" in stash_output:
                stash_list = git_cmd.must_git("stash list -1")
                if stash_list:
                    # Extract stash ref (e.g., "stash@{0}")
                    stash_ref = stash_list.split(":")[0].strip()
                    logger.info(f"Created stash: {stash_ref}")
    except Exception as e:
        logger.warning(f"Failed to stash changes: {e}")

    return GitState(current_branch, current_head, stash_ref)


def restore_git_state(git_cmd: GitInterface, state: GitState) -> None:
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
        git_cmd.must_git(f"checkout {state.branch}")
    except Exception:
        try:
            git_cmd.must_git(f"checkout -f {state.branch}")
        except Exception:
            logger.error(f"Failed to checkout {state.branch}")

    # Restore stashed changes if any
    if state.stash_ref:
        try:
            logger.info(f"Restoring stashed changes from {state.stash_ref}...")
            git_cmd.must_git(f"stash pop {state.stash_ref}")
            logger.info("Stashed changes restored")
        except Exception as e:
            logger.warning(f"Failed to restore stashed changes: {e}")
            logger.warning(f"Your changes are still saved in {state.stash_ref}")
            logger.warning("You can manually restore with: git stash pop")


@contextmanager
def managed_git_state(git_cmd: RealGit):
    """Context manager for git state management with automatic restoration.

    Saves the current git state (branch, HEAD, uncommitted changes) and ensures
    it's restored even if the operation is interrupted with Ctrl-C or fails.
    """
    git_state = save_git_state(git_cmd)
    try:
        yield git_state
    except (Exception, KeyboardInterrupt) as e:
        if isinstance(e, KeyboardInterrupt):
            logger.error("Operation interrupted by user")
        else:
            logger.error(f"Error during operation: {e}")
        raise
    finally:
        restore_git_state(git_cmd, git_state)


def setup_git(directory: Optional[str] = None) -> Tuple[Config, RealGit, GitHubClient]:
    """Setup Git command and config."""
    if directory:
        os.chdir(directory)

    # Check git dir
    git_cmd = RealGit(default_config())
    try:
        output = git_cmd.run_cmd("rev-parse --git-dir")
        if not output or "not a git repository" in output.lower():
            raise Exception("Not in a git repository")
    except Exception as e:
        check(e)
        sys.exit(2)

    cfg = parse_config(git_cmd)
    config = Config(cfg)
    git_cmd = RealGit(config)

    # Use mock GitHub if available and not explicitly disabled
    if is_mock_available and should_use_mock_github():
        logger.debug("Using mock GitHub client")
        # Explicitly set force_mock=False to rely on environment variables
        github = create_github_client(None, config, force_mock=False)
    else:
        logger.debug("Using real GitHub client")
        # Need to create real PyGithub client with adapter
        from github import Github

        from ...github import find_github_token
        from ...github.adapters import PyGithubAdapter

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


@cli.command(
    name="update",
    help="Update and create pull requests for updated commits in the stack",
)
@click.option(
    "-C",
    "--directory",
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
    help="Run as if spr was started in DIRECTORY instead of the current working directory",
)
@click.option(
    "--reviewer",
    "-r",
    multiple=True,
    help="Add the specified reviewer to newly created pull requests",
)
@click.option(
    "--count",
    "-c",
    type=int,
    help="Update a specified number of pull requests from the bottom of the stack",
)
@click.option("--no-rebase", "-nr", is_flag=True, help="Disable rebasing")
@click.option(
    "--label", "-l", multiple=True, help="Add the specified label to new pull requests"
)
@click.option(
    "-v",
    "--verbose",
    count=True,
    help="Increase verbosity (can be used multiple times for more verbosity)",
)
@click.option(
    "--pretend",
    is_flag=True,
    help="Don't actually push or create/update pull requests, just show what would happen",
)
@click.pass_context
def update(
    ctx: Context,
    directory: Optional[str],
    reviewer: List[str],
    count: Optional[int],
    no_rebase: bool,
    label: List[str],
    verbose: int,
    pretend: bool,
) -> None:
    """Update command."""
    from ... import setup_logging

    setup_logging(verbose)

    config, git_cmd, github = setup_git(directory)
    config.tool.pretend = pretend  # Set pretend mode

    try:
        with managed_git_state(git_cmd):
            if no_rebase:
                config.user.no_rebase = True
            stackedpr = StackedPR(config, github, git_cmd)
            stackedpr.pretend = pretend  # Set pretend mode
            stackedpr.update_pull_requests(
                ctx,
                reviewer if reviewer else None,
                count,
                labels=list(label) if label else None,
            )
    except (Exception, KeyboardInterrupt):
        sys.exit(1)


@cli.command(name="status", help="Show status of open pull requests")
@click.option(
    "-C",
    "--directory",
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
    help="Run as if spr was started in DIRECTORY instead of the current working directory",
)
@click.option(
    "-v",
    "--verbose",
    count=True,
    help="Increase verbosity (can be used multiple times for more verbosity)",
)
@click.pass_context
def status(ctx: Context, directory: Optional[str], verbose: int) -> None:
    """Status command."""
    from ... import setup_logging

    setup_logging(verbose)

    config, git_cmd, github = setup_git(directory)
    stackedpr = StackedPR(config, github, git_cmd)
    stackedpr.status_pull_requests(ctx)


@cli.command(name="merge", help="Merge all mergeable pull requests")
@click.option(
    "-C",
    "--directory",
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
    help="Run as if spr was started in DIRECTORY instead of the current working directory",
)
@click.option(
    "--count",
    "-c",
    type=int,
    help="Merge a specified number of pull requests from the bottom of the stack",
)
@click.option("--no-rebase", "-nr", is_flag=True, help="Disable rebasing")
@click.option(
    "-v",
    "--verbose",
    count=True,
    help="Increase verbosity (can be used multiple times for more verbosity)",
)
@click.pass_context
def merge(
    ctx: Context,
    directory: Optional[str],
    count: Optional[int],
    no_rebase: bool,
    verbose: int,
) -> None:
    """Merge command."""
    from ... import setup_logging

    setup_logging(verbose)

    config, git_cmd, github = setup_git(directory)

    if no_rebase:
        config.user.no_rebase = True
    stackedpr = StackedPR(config, github, git_cmd)
    stackedpr.merge_pull_requests(ctx, count)
    # Don't update after merge - this would create new PRs


@cli.command(
    name="breakup", help="Break up current commit stack into independent branches/PRs"
)
@click.option(
    "-C",
    "--directory",
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
    help="Run as if spr was started in DIRECTORY instead of the current working directory",
)
@click.option(
    "-v",
    "--verbose",
    count=True,
    help="Increase verbosity (can be used multiple times for more verbosity)",
)
@click.option(
    "--pretend",
    is_flag=True,
    help="Don't actually push or create/update pull requests, just show what would happen",
)
@click.option(
    "--no-rebase", "-nr", is_flag=True, help="Disable rebasing on latest upstream"
)
@click.option(
    "--reviewer",
    "-r",
    multiple=True,
    help="Add the specified reviewer to newly created pull requests",
)
@click.option(
    "--count",
    "-c",
    type=int,
    help="Break up a specified number of commits from the bottom of the stack",
)
@click.option(
    "--update-only-these-ids",
    type=str,
    help="Only update PRs for specific commit IDs (comma-separated)",
)
@click.option(
    "--stacks",
    is_flag=True,
    help="Create multiple PR stacks based on commit dependencies",
)
@click.option('--single-stack', is_flag=True,
              help="Create a single stack with independents removed")
@click.pass_context
def breakup(
    ctx: Context,
    directory: Optional[str],
    verbose: int,
    pretend: bool,
    no_rebase: bool,
    reviewer: List[str],
    count: Optional[int],
    update_only_these_ids: Optional[str],
    stacks: bool,
    single_stack: bool,
) -> None:
    """Breakup command."""
    from ... import setup_logging

    setup_logging(verbose)

    config, git_cmd, github = setup_git(directory)
    config.tool.pretend = pretend  # Set pretend mode
    
    # Save current git state for recovery
    git_state = save_git_state(git_cmd)
    
    try:
        if no_rebase:
            config.user.no_rebase = True
        stackedpr = StackedPR(config, github, git_cmd)
        stackedpr.pretend = pretend  # Set pretend mode
        
        # Parse commit IDs if provided
        commit_ids = None
        if update_only_these_ids:
            commit_ids = [id.strip() for id in update_only_these_ids.split(',') if id.strip()]
        
        # Determine mode based on flags
        if single_stack:
            mode = 'single_stack'
        elif stacks:
            mode = 'stacks'
        else:
            mode = 'stacks'  # default
        
        stackedpr.breakup_pull_requests(ctx, reviewer if reviewer else None, count, commit_ids, stacks or single_stack, mode)
    except Exception as e:
        logger.error(f"Error during breakup: {e}")
        restore_git_state(git_cmd, git_state)
        sys.exit(1)
    finally:
        # Always try to restore state if we still have uncommitted changes
        try:
            status = git_cmd.must_git("status --porcelain")
            if not status.strip() and git_state.stash_ref:
                # No uncommitted changes but we have a stash - restore it
                restore_git_state(git_cmd, git_state)
        except Exception:
            pass


@cli.command(
    name="analyze",
    help="Analyze which commits can be independently submitted without stacking",
)
@click.option(
    "-C",
    "--directory",
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
    help="Run as if spr was started in DIRECTORY instead of the current working directory",
)
@click.option(
    "-v",
    "--verbose",
    count=True,
    help="Increase verbosity (can be used multiple times for more verbosity)",
)
@click.pass_context
def analyze(ctx: Context, directory: Optional[str], verbose: int) -> None:
    """Analyze command."""
    from ... import setup_logging

    setup_logging(verbose)

    config, git_cmd, github = setup_git(directory)

    try:
        with managed_git_state(git_cmd):
            stackedpr = StackedPR(config, github, git_cmd)
            stackedpr.analyze(ctx)
    except (Exception, KeyboardInterrupt):
        sys.exit(1)


def main() -> None:
    """Main entry point."""
    # Add command aliases
    cli.aliases["up"] = "update"
    cli.aliases["st"] = "status"
    cli(obj={})


if __name__ == "__main__":
    main()
