"""CLI entry point."""

import os
import sys
import click
from typing import List, Optional

from ...config import Config, default_config
from ...config.config_parser import parse_config
from ...git import RealGit
from ...github import GitHubClient
from ...spr import StackedPR

def check(err):
    """Check for error and exit if needed."""
    if err:
        print(f"error: {err}")
        sys.exit(1)

@click.group()
@click.pass_context
def cli(ctx):
    """SPR - Stacked Pull Requests on GitHub."""
    ctx.obj = {}

def setup_git(directory: Optional[str] = None):
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
@click.pass_context
def update(ctx, directory, reviewer: List[str], count: Optional[int], no_rebase: bool):
    """Update command."""
    if no_rebase:
        os.environ["SPR_NOREBASE"] = "true"

    config, git_cmd, github = setup_git(directory)
    stackedpr = StackedPR(config, github, git_cmd)
    stackedpr.update_pull_requests(None, reviewer if reviewer else None, count)

@cli.command(name="status", help="Show status of open pull requests")
@click.option('-C', '--directory', type=click.Path(exists=True, file_okay=False, dir_okay=True),
              help='Run as if spr was started in DIRECTORY instead of the current working directory')
@click.pass_context
def status(ctx, directory):
    """Status command."""
    config, git_cmd, github = setup_git(directory)
    stackedpr = StackedPR(config, github, git_cmd)
    stackedpr.status_pull_requests(None)

@cli.command(name="merge", help="Merge all mergeable pull requests")
@click.option('-C', '--directory', type=click.Path(exists=True, file_okay=False, dir_okay=True),
              help='Run as if spr was started in DIRECTORY instead of the current working directory')
@click.option('--count', '-c', type=int,
              help="Merge a specified number of pull requests from the bottom of the stack")
@click.pass_context
def merge(ctx, directory, count: Optional[int]):
    """Merge command."""
    config, git_cmd, github = setup_git(directory)
    stackedpr = StackedPR(config, github, git_cmd)
    stackedpr.merge_pull_requests(None, count)
    # Don't update after merge - this would create new PRs

def main():
    """Main entry point."""
    cli(obj={})

if __name__ == "__main__":
    main()