"""Shared utilities for pyspr tests."""
import os
import subprocess
import yaml
from typing import List, Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)

def get_gh_token() -> str:
    """Get GitHub token from ~/.config/gh/hosts.yml."""
    hosts_file = os.path.expanduser("~/.config/gh/hosts.yml")
    if not os.path.exists(hosts_file):
        raise ValueError("GitHub hosts file not found")
        
    with open(hosts_file) as f:
        hosts_data = yaml.safe_load(f)
        return hosts_data.get("github.com", {}).get("oauth_token", "")

def run_cmd(cmd: str, cwd: Optional[str] = None, check: bool = True) -> str:
    """Run shell command and return output.
    
    Args:
        cmd: Command to run
        cwd: Working directory
        check: Whether to check return code
        
    Returns:
        str: Command output
    """
    logger.debug(f"Running command: {cmd}")
    result = subprocess.run(
        cmd, shell=True, check=check, cwd=cwd,
        capture_output=True, text=True
    )
    logger.debug(f"Command output: {result.stdout.strip()}")
    if result.stderr:
        logger.debug(f"Command stderr: {result.stderr.strip()}")
    return result.stdout.strip()

def get_test_prs(owner: str, repo: str, tag: str) -> List[Dict[str, Any]]:
    """Get test PRs for a repo by tag.
    
    Args:
        owner: Repo owner
        repo: Repo name
        tag: Tag to search for
        
    Returns:
        List[Dict[str, Any]]: List of matching PRs
    """
    cmd = f"gh pr list -R {owner}/{repo} --state all --search '{tag} in:title' --json number,title,state,author"
    output = run_cmd(cmd)
    return yaml.safe_load(output) if output else []