"""CLI entry point."""

import os
import sys
import click
import logging
from typing import Any, List, Optional, Tuple, Dict
from click import Context

# Get module logger
logger = logging.getLogger(__name__)

from ...config import Config, default_config
from ...config.config_parser import parse_config
from ...git import RealGit
from ...github import GitHubClient 
from ...spr import StackedPR

def check(err: Exception) -> None:
    """Check for error and exit if needed."""
    if err:
        logger.error(f"{err}")
        sys.exit(1)

class AliasedGroup(click.Group):
    """Command group with support for aliases."""
    
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize with aliases map."""
        super().__init__(*args, **kwargs)
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
    github = GitHubClient(None, config)
    
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
    
    if no_rebase:
        os.environ["SPR_NOREBASE"] = "true"

    config, git_cmd, github = setup_git(directory)
    stackedpr = StackedPR(config, github, git_cmd)
    stackedpr.pretend = pretend  # Set pretend mode
    stackedpr.update_pull_requests(ctx, reviewer if reviewer else None, count, labels=list(label) if label else None)

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
    
    if no_rebase:
        os.environ["SPR_NOREBASE"] = "true"
    
    config, git_cmd, github = setup_git(directory)
    stackedpr = StackedPR(config, github, git_cmd)
    stackedpr.merge_pull_requests(ctx, count)
    # Don't update after merge - this would create new PRs


def main() -> None:
    """Main entry point."""
    # Add command aliases
    cli.aliases['up'] = 'update'
    cli.aliases['st'] = 'status'
    cli(obj={})

if __name__ == "__main__":
    main()