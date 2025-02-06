"""Config parser logic."""

from pathlib import Path
from typing import Dict, Optional
import yaml

def parse_config(git_cmd) -> Dict:
    """Parse config from repository and user config files."""
    # Simple config implementation enough to support update
    config = {
        'repo': {
            'github_remote': 'origin',
            'github_branch': 'main',
            'require_checks': True,
            'require_approval': True,
            'github_host': 'github.com',
        },
        'user': {
            'log_git_commands': True,
            'log_github_calls': True,
        }
    }
    
    # Try to extract repo owner/name from git remote
    try:
        remote_url = git_cmd.run_cmd("git remote get-url origin")
        # Very basic parsing just to support update
        if "github.com" in remote_url:
            parts = remote_url.split("github.com/")[1].split(".git")[0].split("/")
            if len(parts) >= 2:
                config['repo']['github_repo_owner'] = parts[0]
                config['repo']['github_repo_name'] = parts[1]
    except:
        pass

    return config

def internal_config_file_path() -> str:
    """Get path to internal config file."""
    return str(Path.home() / ".spr.yml")