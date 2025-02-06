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
    # Check git dir
    git_cmd = RealGit(default_config())
    try:
        git_cmd.run_cmd("status --porcelain")
    except Exception as e:
        check(e)
        sys.exit(2)

    cfg = parse_config(git_cmd)
    config = Config(cfg)
    git_cmd = RealGit(config)

    ctx.obj = {
        'config': config,
        'git_cmd': git_cmd,
        'github': GitHubClient(None, config),
    }

@cli.command(name="update", help="Update and create pull requests for updated commits in the stack")
@click.option('--reviewer', '-r', multiple=True, 
              help="Add the specified reviewer to newly created pull requests")
@click.option('--count', '-c', type=int,
              help="Update a specified number of pull requests from the bottom of the stack")
@click.option('--no-rebase', '-nr', is_flag=True, help="Disable rebasing")
@click.pass_context
def update(ctx, reviewer: List[str], count: Optional[int], no_rebase: bool):
    """Update command."""
    if no_rebase:
        os.environ["SPR_NOREBASE"] = "true"

    stackedpr = StackedPR(ctx.obj['config'], ctx.obj['github'], ctx.obj['git_cmd'])
    stackedpr.update_pull_requests(None, reviewer if reviewer else None, count)

@cli.command(name="status", help="Show status of open pull requests")
@click.pass_context
def status(ctx):
    """Status command."""
    stackedpr = StackedPR(ctx.obj['config'], ctx.obj['github'], ctx.obj['git_cmd'])
    stackedpr.status_pull_requests(None)

def main():
    """Main entry point."""
    cli(obj={})

if __name__ == "__main__":
    main()