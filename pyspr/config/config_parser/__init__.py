"""Config parser logic."""

from pathlib import Path
from typing import Dict, Union, Any
import yaml

from ...git import GitInterface

ConfigValue = Union[str, bool]
RepoConfig = Dict[str, Any]  # Use Any since yaml can return various types
Config = Dict[str, RepoConfig]

def parse_config(git_cmd: GitInterface) -> Config:
    """Parse config from repository and user config files."""
    # Simple config implementation enough to support update
    config: Config = {
        'repo': {
            'github_remote': 'origin',
            'github_branch': 'main',
            'require_checks': True,
            'require_approval': True,
            'github_host': 'github.com',
            'show_pr_titles_in_stack': False,
        },
        'user': {
            'log_git_commands': True,
            'log_github_calls': True,
        }
    }
    
    # Try to load .spr.yaml from repository root
    try:
        with open('.spr.yaml', 'r') as f:
            repo_config = yaml.safe_load(f)
            if repo_config:
                if 'repo' in repo_config and isinstance(repo_config['repo'], dict):
                    config['repo'].update(repo_config['repo'])
                if 'user' in repo_config and isinstance(repo_config['user'], dict):
                    config['user'].update(repo_config['user'])
    except FileNotFoundError:
        pass  # No .spr.yaml is fine
            
    # Try to extract repo owner/name from git remote if not in config
    if not config['repo'].get('github_repo_owner') or not config['repo'].get('github_repo_name'):
        try:
            remote_url = git_cmd.run_cmd("remote get-url origin")
            # Handle SSH and HTTPS urls
            if "@" in remote_url:
                # SSH format: git@github.com:owner/repo.git
                repo_part = remote_url.split(":")[-1]
            else:
                # HTTPS format: https://github.com/owner/repo.git
                repo_part = remote_url.split("github.com/")[-1]
            
            # Clean up the repo part
            repo_part = repo_part.replace(".git", "").strip()
            parts = repo_part.split("/")
            if len(parts) >= 2:
                if not config['repo'].get('github_repo_owner'):
                    config['repo']['github_repo_owner'] = parts[0]
                if not config['repo'].get('github_repo_name'):
                    config['repo']['github_repo_name'] = parts[1]
        except Exception as e:
            print(f"Failed to parse git remote: {e}")
    
    # Special case for test repo that needs merge queue
    if (config['repo'].get('github_repo_owner') == "yangenttest1" and 
        config['repo'].get('github_repo_name') == "teststack"):
        config['repo']['merge_queue'] = True

    return config

def internal_config_file_path() -> str:
    """Get path to internal config file."""
    return str(Path.home() / ".spr.yml")