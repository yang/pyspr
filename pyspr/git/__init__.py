"""Git interfaces and implementation."""

import os
import re
import subprocess
from dataclasses import dataclass
from typing import List, Optional

@dataclass 
class Commit:
    """Git commit info."""
    commit_id: str
    commit_hash: str
    subject: str
    wip: bool = False

class GitInterface:
    """Git interface."""
    def run_cmd(self, command: str, output: Optional[str] = None) -> str:
        """Run git command and optionally capture output."""
        raise NotImplementedError()

    def must_git(self, command: str, output: Optional[str] = None) -> str:
        """Run git command, failing on error."""
        raise NotImplementedError()

def get_local_commit_stack(config, git_cmd) -> List[Commit]:
    """Get local commit stack."""
    # Get commits between origin/main and HEAD
    remote = config.repo.get('github_remote', 'origin')
    branch = config.repo.get('github_branch', 'main')
    target = f"{remote}/{branch}"

    cmd = f"rev-list --reverse {target}..HEAD"
    commit_ids = git_cmd.must_git(cmd).strip().split("\n")
    if not commit_ids or commit_ids[0] == '':
        return []

    commits = []
    for cid in commit_ids:
        if not cid:
            continue
        cmd = f"show -s --format=%H:%s {cid}"
        commit_info = git_cmd.must_git(cmd).strip()
        commit_hash, subject = commit_info.split(":", 1)
        wip = subject.upper().startswith("WIP")
        commits.append(Commit(cid, commit_hash, subject, wip))

    return commits

def branch_name_from_commit(config, commit: Commit) -> str:
    """Generate branch name from commit."""
    remote = config.repo.get('github_remote', 'origin')
    branch = config.repo.get('github_branch', 'main')
    base = f"{remote}_{branch}" if config.repo.get('branch_name_include_target', False) else "pr"
    return f"{base}_{commit.commit_hash[:8]}"

class RealGit(GitInterface):
    """Real Git implementation."""
    def __init__(self, config):
        """Initialize with config."""
        self.config = config

    def run_cmd(self, command: str, output: Optional[str] = None) -> str:
        """Run git command."""
        if self.config.user.get('log_git_commands', False):
            print(f"git {command}")
        try:
            # Split command properly handling quotes 
            import shlex
            cmd_parts = ["git"] + shlex.split(command)
            result = subprocess.run(cmd_parts, capture_output=True, text=True, check=False)
            if result.returncode != 0:
                raise Exception(f"Git command failed: {result.stderr}")
            return result.stdout
        except Exception as e:
            if str(e):
                print(f"Git error: {e}")
            raise

    def must_git(self, command: str, output: Optional[str] = None) -> str:
        """Run git command, failing on error."""
        return self.run_cmd(command, output)