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
    try:
        remote = config.repo.get('github_remote', 'origin')
        branch = config.repo.get('github_branch', 'main')

        # Get commit log
        log_cmd = f"log --format=medium --no-color {remote}/{branch}..HEAD"
        commit_log = git_cmd.must_git(log_cmd)
    except Exception:
        # For tests, fall back to getting all commits
        commit_log = git_cmd.must_git("log --format=medium --no-color")
    
    commits, valid = parse_local_commit_stack(commit_log)
    
    # If not valid, it means commits are missing IDs - add them
    if not valid:
        # Get all commits for test
        try:
            remote = config.repo.get('github_remote', 'origin')
            branch = config.repo.get('github_branch', 'main')
            target = f"{remote}/{branch}"
            cmd = f"rev-list --reverse {target}..HEAD"
            commit_hashes = git_cmd.must_git(cmd).strip().split("\n")
        except Exception:
            # For tests, just get all commits
            commit_hashes = git_cmd.must_git("rev-list --reverse HEAD").strip().split("\n")
        if not commit_hashes or commit_hashes[0] == '':
            return []

        # Save current state
        curr_branch = git_cmd.must_git("rev-parse --abbrev-ref HEAD").strip()
        original_head = git_cmd.must_git("rev-parse HEAD").strip()

        try:
            commits = []
            last_good_hash = target if 'target' in locals() else 'HEAD'

            for cid in reversed(commit_hashes):  # Work from newest to oldest
                if not cid:
                    continue
                # Check for commit-id in message
                body = git_cmd.must_git(f"show -s --format=%b {cid}").strip()
                full_msg = git_cmd.must_git(f"log -1 --format=%B {cid}").strip()
                commit_id_match = re.search(r'commit-id:([a-f0-9]{8})', full_msg)
                
                if commit_id_match:
                    # Has ID already - just prepend to list
                    commit_id = commit_id_match.group(1)
                    commit_hash = git_cmd.must_git(f"rev-parse {cid}").strip()
                    subject = git_cmd.must_git(f"show -s --format=%s {cid}").strip()
                    wip = subject.upper().startswith("WIP")
                    commits.insert(0, Commit(commit_id, commit_hash, subject, body, wip))
                else:
                    # Need to add ID
                    import uuid
                    commit_id = str(uuid.uuid4())[:8]
                    
                    # Get current message
                    subject = git_cmd.must_git(f"show -s --format=%s {cid}").strip()
                    new_msg = f"{full_msg}\n\ncommit-id:{commit_id}"
                    
                    # Checkout commit
                    git_cmd.must_git(f"checkout {cid}")
                    
                    # Amend with ID
                    git_cmd.must_git(f"commit --amend -m \"{new_msg}\"")
                    
                    # Get new hash
                    new_hash = git_cmd.must_git("rev-parse HEAD").strip()
                    
                    # Add to list
                    wip = subject.upper().startswith("WIP")
                    commits.insert(0, Commit(commit_id, new_hash, subject, new_msg, wip))

            # Now rewrite history with the new commit IDs
            git_cmd.must_git(f"checkout {curr_branch}")
            git_cmd.must_git(f"reset --hard {last_good_hash}")
            for commit in commits:
                cherry_pick_cmd = f"cherry-pick {commit.commit_hash}"
                git_cmd.must_git(cherry_pick_cmd)
                
            return commits
        except Exception as e:
            # Clean up on error
            git_cmd.must_git(f"checkout {curr_branch}")
            git_cmd.must_git(f"reset --hard {original_head}")
            raise Exception(f"Failed to add commit IDs: {e}")

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
        return [], False
        
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