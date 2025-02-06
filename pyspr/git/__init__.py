"""Git interfaces and implementation."""

import os
import re
import subprocess
from dataclasses import dataclass
from typing import List, Optional, Tuple
import git

@dataclass 
class Commit:
    """Git commit info.
    CommitID persists across amends, CommitHash changes with each amend."""
    commit_id: str # Persists across amends
    commit_hash: str # Changes with each amend
    subject: str
    body: str = ""
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
    """Get local commit stack. Returns commits ordered with bottom commit first."""
    remote = config.repo.get('github_remote', 'origin')
    branch = config.repo.get('github_branch', 'main')
    
    # Get commit log
    log_cmd = f"log --format=medium --no-color {remote}/{branch}..HEAD"
    commit_log = git_cmd.must_git(log_cmd)
    
    commits, valid = parse_local_commit_stack(commit_log)
    
    # For minimal port, if not valid, use hashes
    if not valid or not commits:
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
            # Use hash as ID for minimal port
            commits.append(Commit(commit_hash[:8], commit_hash, subject, "", wip))

    return commits

def parse_local_commit_stack(commit_log: str) -> Tuple[List[Commit], bool]:
    """Parse commit log into commits. Returns (commits, valid)."""
    commits = []
    
    if not commit_log.strip():
        return [], True
    
    # Use same regexes as Go code
    commit_hash_regex = re.compile(r'^commit ([a-f0-9]{40})')
    commit_id_regex = re.compile(r'commit-id:([a-f0-9]{8})')
    
    commit_scan_on = False
    scanned_commit = None
    subject_index = 0
    
    lines = commit_log.split('\n')
    for index, line in enumerate(lines):
        # Match commit hash - start of new commit
        hash_match = commit_hash_regex.search(line)
        if hash_match:
            if commit_scan_on:
                # Missing commit ID in previous commit
                return [], False
            commit_scan_on = True
            scanned_commit = Commit(
                commit_id="",  # Will be filled by commit-id or hash
                commit_hash=hash_match.group(1),
                subject="",
                body=""
            )
            subject_index = index + 4
            
        # Match commit ID - last thing in commit
        id_match = commit_id_regex.search(line)
        if id_match:
            scanned_commit.commit_id = id_match.group(1)
            scanned_commit.body = scanned_commit.body.strip()
            
            if scanned_commit.subject.startswith("WIP"):
                scanned_commit.wip = True
                
            # Prepend to keep same order as Go code
            commits.insert(0, scanned_commit)
            commit_scan_on = False
            
        # Look for subject and body
        if commit_scan_on:
            if index == subject_index:
                scanned_commit.subject = line.strip()
            elif index > subject_index:
                if line.strip():
                    scanned_commit.body += line.strip() + "\n"
                    
    # If still scanning, missing commit ID
    if commit_scan_on:
        # For minimal port: use hash as ID 
        scanned_commit.commit_id = scanned_commit.commit_hash[:8]
        scanned_commit.body = scanned_commit.body.strip()
        if scanned_commit.subject.startswith("WIP"):
            scanned_commit.wip = True
        commits.insert(0, scanned_commit)
        return commits, True
        
    return commits, True

def branch_name_from_commit(config, commit: Commit) -> str:
    """Get branch name for commit. Matches Go implementation."""
    remote_branch = config.repo.get('github_branch', 'main')
    return f"spr/{remote_branch}/{commit.commit_id}"

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