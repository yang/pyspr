"""Git interfaces and implementation."""

import os
import re
import subprocess
from dataclasses import dataclass
from typing import List, Optional
import git

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
        cmd_str = command.strip()
        if self.config.user.get('log_git_commands', False):
            print(f"git {cmd_str}")
        try:
            # Use GitPython
            import git
            repo = git.Repo(os.getcwd(), search_parent_directories=True)
            git_cmd = repo.git
            # Convert command to method call
            import shlex
            cmd_parts = shlex.split(cmd_str)
            git_command = cmd_parts[0]
            git_args = cmd_parts[1:]
            method = getattr(git_cmd, git_command.replace('-', '_'))
            result = method(*git_args)
            return result if isinstance(result, str) else str(result)
        except git.exc.GitCommandError as e:
            raise Exception(f"Git command failed: {e.stderr}")
        except git.exc.InvalidGitRepositoryError:
            raise Exception("Not in a git repository")
        except Exception as e:
            if str(e):
                print(f"Git error: {e}")
            raise
        except Exception as e:
            if str(e):
                print(f"Git error: {e}")
            raise

    def must_git(self, command: str, output: Optional[str] = None) -> str:
        """Run git command, failing on error."""
        return self.run_cmd(command, output)