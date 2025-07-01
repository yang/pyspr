"""Git interfaces and implementation."""

import os
import re
import uuid
import logging
from typing import List, Optional, Tuple
import git
from git.exc import GitCommandError, InvalidGitRepositoryError
from ..typing import CommitID, GitInterface, Commit
from ..config.models import PysprConfig

# Get module logger
logger = logging.getLogger(__name__)

def get_local_commit_stack(config: PysprConfig, git_cmd: GitInterface) -> List[Commit]:
    """Get local commit stack. Returns commits ordered with bottom commit first."""
    try:
        # Try to get the upstream branch
        try:
            upstream = git_cmd.must_git("rev-parse --abbrev-ref @{upstream}").strip()
            logger.debug(f"Using upstream branch: {upstream}")
        except Exception:
            # Fall back to configured remote/branch
            remote = config.repo.github_remote
            branch = config.repo.github_branch
            upstream = f"{remote}/{branch}"
            logger.debug(f"No upstream set, using config: {upstream}")

        # Get commit log
        log_cmd = f"log --format=medium --no-color {upstream}..HEAD"
        commit_log = git_cmd.must_git(log_cmd)
    except Exception:
        # For tests, fall back to getting all commits
        commit_log = git_cmd.must_git("log --format=medium --no-color")
    
    commits: List[Commit] = []
    valid = True
    commits, valid = parse_local_commit_stack(commit_log)
    
    logger.info(f"get_local_commit_stack: parsed {len(commits)} commits, valid={valid}")
    if commits:
        logger.info("Parsed commits:")
        for c in commits:
            logger.info(f"  {c.commit_hash[:8]}: id={c.commit_id}, subject='{c.subject}'")
    
    # If not valid, it means commits are missing IDs - add them
    if not valid:
        logger.info("Parsing marked as invalid - some commits are missing commit-ids")
        # Get all commits for test
        commit_hashes: List[str] = []
        target = "HEAD"  # Default target
        try:
            # Try to get the upstream branch
            try:
                target = git_cmd.must_git("rev-parse --abbrev-ref @{upstream}").strip()
            except Exception:
                # Fall back to configured remote/branch
                remote = config.repo.github_remote
                branch = config.repo.github_branch
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
            commits_new: List[Commit] = []
            # Track the maximum changed index
            max_changed = -1

            for i, cid in enumerate(reversed(commit_hashes)):  # Work from newest to oldest
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
                    commits_new.insert(0, Commit.from_strings(commit_id, commit_hash, subject, body, wip))
                    logger.debug(f"Commit {cid[:8]} already has commit-id: {commit_id}")
                else:
                    # Need to add ID
                    logger.info(f"Commit {cid[:8]} missing commit-id, will add one")
                    new_id = str(uuid.uuid4())[:8]
                    
                    # Get current message
                    subject = git_cmd.must_git(f"show -s --format=%s {cid}").strip()
                    new_msg = f"{full_msg}\n\ncommit-id:{new_id}"
                    
                    # Checkout commit
                    # Debug: Check for git processes
                    import subprocess
                    try:
                        ps_output = subprocess.run(['ps', 'aux'], capture_output=True, text=True).stdout
                        git_processes = [line for line in ps_output.split('\n') if 'git' in line and 'grep' not in line and 'gitstatus' not in line and 'rsync' not in line]
                        if git_processes:
                            logging.info(f"Active git processes before checkout {cid}:")
                            for proc in git_processes:
                                logging.info(f"  {proc}")
                        
                        # Check for index.lock
                        lock_path = os.path.join(os.getcwd(), '.git', 'index.lock')
                        if os.path.exists(lock_path):
                            logging.warning(f"index.lock already exists before checkout {cid}!")
                    except Exception as e:
                        logging.warning(f"Failed to check git processes: {e}")
                    
                    git_cmd.must_git(f"checkout {cid}")
                    
                    # Amend with ID
                    # Use direct GitPython call to handle multiline messages properly
                    repo = git.Repo(os.getcwd(), search_parent_directories=True)
                    repo.git.commit("--amend", "-m", new_msg)
                    
                    # Get new hash
                    new_hash = git_cmd.must_git("rev-parse HEAD").strip()
                    
                    # Add to list
                    wip = subject.upper().startswith("WIP")
                    commits_new.insert(0, Commit.from_strings(new_id, new_hash, subject, new_msg, wip))

                    max_changed = i

            # Only rewrite history if we actually changed commits
            if max_changed >= 0:
                commits_changed = commits_new[-(max_changed + 1):]
                
                # Now rewrite history with the new commit IDs
                git_cmd.must_git(f"checkout {curr_branch}")
                git_cmd.must_git(f"reset --hard HEAD~{len(commits_changed)}")
                for commit in commits_changed:
                    cherry_pick_cmd = f"cherry-pick {commit.commit_hash}"
                    git_cmd.must_git(cherry_pick_cmd)
            else:
                # No commits were changed, just ensure we're on the right branch
                git_cmd.must_git(f"checkout {curr_branch}")
                
            return commits_new
        except Exception as e:
            # Clean up on error
            # Debug: Check for git processes before cleanup checkout
            import subprocess
            try:
                ps_output = subprocess.run(['ps', 'aux'], capture_output=True, text=True).stdout
                git_processes = [line for line in ps_output.split('\n') if 'git' in line and 'grep' not in line and 'gitstatus' not in line and 'rsync' not in line]
                if git_processes:
                    logging.info(f"Active git processes before cleanup checkout {curr_branch}:")
                    for proc in git_processes:
                        logging.info(f"  {proc}")
                
                # Check for index.lock
                lock_path = os.path.join(os.getcwd(), '.git', 'index.lock')
                if os.path.exists(lock_path):
                    logging.warning(f"index.lock already exists before cleanup checkout {curr_branch}!")
            except Exception as debug_e:
                logging.warning(f"Failed to check git processes: {debug_e}")
            
            git_cmd.must_git(f"checkout {curr_branch}")
            git_cmd.must_git(f"reset --hard {original_head}")
            raise Exception(f"Failed to add commit IDs: {e}")
        finally:
            # Always ensure we're back on the original branch
            try:
                current = git_cmd.must_git("rev-parse --abbrev-ref HEAD").strip()
                if current != curr_branch:
                    logger.info(f"Restoring branch from {current} to {curr_branch}")
                    git_cmd.must_git(f"checkout {curr_branch}")
            except Exception as e:
                logger.error(f"Failed to restore branch in finally block: {e}")

    return commits

def parse_local_commit_stack(commit_log: str) -> Tuple[List[Commit], bool]:
    """Parse commit log into commits. Returns (commits, valid)."""
    commits: List[Commit] = []
    
    if not commit_log.strip():
        return [], True
    
    # Use same regexes as Go code
    commit_hash_regex = re.compile(r'^commit ([a-f0-9]{40})')
    commit_id_regex = re.compile(r'commit-id:([a-f0-9]{8})')
    
    commit_scan_on = False
    scanned_commit: Optional[Commit] = None
    subject_index = 0
    
    lines = commit_log.split('\n')
    for index, line in enumerate(lines):
        # Match commit hash - start of new commit
        hash_match = commit_hash_regex.search(line)
        if hash_match:
            if commit_scan_on:
                # Missing commit ID in previous commit
                logger.debug(f"parse_local_commit_stack: Missing commit-id in commit {scanned_commit.commit_hash[:8] if scanned_commit else 'None'} with subject: '{scanned_commit.subject if scanned_commit else 'None'}' at line {index}")
                logger.debug(f"  New commit hash found: {hash_match.group(1)[:8]}")
                # Don't return empty list - return what we've parsed so far
                # This allows reusing commits that already have IDs
                return commits, False
            commit_scan_on = True
            scanned_commit = Commit.from_strings(
                commit_id="",  # Will be filled by commit-id or hash
                commit_hash=hash_match.group(1),
                subject="",
                body=""
            )
            subject_index = index + 4
            
        # Match commit ID - last thing in commit
        id_match = commit_id_regex.search(line)
        if id_match and scanned_commit:
            scanned_commit.commit_id = CommitID(id_match.group(1))
            scanned_commit.body = scanned_commit.body.strip()
            
            if scanned_commit.subject.upper().startswith("WIP"):
                scanned_commit.wip = True
                
            # Prepend to keep same order as Go code
            commits.insert(0, scanned_commit)
            commit_scan_on = False
            
        # Look for subject and body
        if commit_scan_on and scanned_commit:
            if index == subject_index:
                scanned_commit.subject = line.strip()
            elif index > subject_index:
                if line.strip():
                    scanned_commit.body += line.strip() + "\n"
                    
    # If still scanning, missing commit ID
    if commit_scan_on:
        logger.debug(f"parse_local_commit_stack: Still scanning at end. Last commit subject: '{scanned_commit.subject if scanned_commit else 'None'}'")
        # Return what we've parsed so far
        return commits, False
        
    return commits, True

def branch_name_from_commit(config: PysprConfig, commit: Commit) -> str:
    """Get branch name for commit. Now unified to use pyspr/cp/ prefix."""
    remote_branch = config.repo.github_branch
    return f"pyspr/cp/{remote_branch}/{commit.commit_id}"

def breakup_branch_name_from_commit(config: PysprConfig, commit: Commit) -> str:
    """Get branch name for breakup commit. Uses pyspr pattern."""
    remote_branch = config.repo.github_branch
    return f"pyspr/cp/{remote_branch}/{commit.commit_id}"

class RealGit:
    """Real Git implementation."""
    def __init__(self, config: PysprConfig):
        """Initialize with config."""
        self.config: PysprConfig = config

    def run_cmd(self, command: str, output: Optional[str] = None) -> str:
        """Run git command."""
        cmd_str = command.strip()
        
        # Check for no-rebase flag
        no_rebase = self.config.user.no_rebase
        if no_rebase:
            # Skip any commands that could modify commit hashes
            if any(cmd_str.startswith(cmd) for cmd in ("rebase",)):
                logger.debug(f"Skipping command '{cmd_str}' due to --no-rebase")
                return ""

        if self.config.tool.pretend and 'push' in cmd_str:
            # Pretend mode - just log
            logger.info(f"> git {cmd_str}")
            return ""

        # Always log git commands
        logger.info(f"> git {cmd_str}")
        try:
            # Use GitPython
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
        except GitCommandError as e:
            raise Exception(f"Git command failed: {str(e)}")
        except InvalidGitRepositoryError:
            raise Exception("Not in a git repository")
        except Exception as e:
            if str(e):
                logger.error(f"Git error: {e}")
            raise

    def must_git(self, command: str, output: Optional[str] = None) -> str:
        """Run git command, failing on error."""
        return self.run_cmd(command, output)
