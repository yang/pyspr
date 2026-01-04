"""Git interfaces and implementation."""

import os
import re
import uuid
import logging
import time
import shlex
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
    
    logger.debug(f"get_local_commit_stack: parsed {len(commits)} commits, valid={valid}")
    if commits:
        logger.debug("Parsed commits:")
        for c in commits:
            logger.debug(f"  {c.commit_hash[:8]}: id={c.commit_id}, subject='{c.subject}'")
    
    # If not valid, it means commits are missing IDs - add them
    if not valid:
        logger.debug("Parsing marked as invalid - some commits are missing commit-ids")
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
                    logger.debug(f"Commit {cid[:8]} missing commit-id, will add one")
                    new_id = str(uuid.uuid4())[:8]
                    
                    # Get current message
                    subject = git_cmd.must_git(f"show -s --format=%s {cid}").strip()
                    new_msg = f"{full_msg}\n\ncommit-id:{new_id}"
                    
                    # Checkout commit
                    git_cmd.must_git(f"checkout {cid}")

                    # Amend with ID
                    # Use run_cmd to ensure index.lock waiting works
                    # Quote the message properly for shell execution
                    git_cmd.must_git(f"commit --amend -m {shlex.quote(new_msg)}")

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
            git_cmd.must_git(f"checkout {curr_branch}")
            git_cmd.must_git(f"reset --hard {original_head}")
            raise Exception(f"Failed to add commit IDs: {e}")

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
    """Get branch name for commit. Uses {prefix}{commit_id} pattern."""
    prefix = config.repo.branch_prefix
    return f"{prefix}{commit.commit_id}"

def breakup_branch_name_from_commit(config: PysprConfig, commit: Commit) -> str:
    """Get branch name for breakup commit. Uses {prefix}{commit_id} pattern."""
    prefix = config.repo.branch_prefix
    return f"{prefix}{commit.commit_id}"

class RealGit:
    """Real Git implementation."""
    def __init__(self, config: PysprConfig):
        """Initialize with config."""
        self.config: PysprConfig = config

    def _wait_for_index_lock(self) -> None:
        """Wait for git index.lock to be released.

        This handles NFS lag issues where index.lock might persist briefly
        even after the git operation that created it has completed.

        Uses config values for wait time and check interval.
        """
        # Check if waiting is enabled
        if not self.config.tool.index_lock_wait_enabled:
            return

        max_wait = self.config.tool.index_lock_max_wait
        check_interval = self.config.tool.index_lock_check_interval
        stale_threshold = self.config.tool.index_lock_stale_threshold

        try:
            # Find the git directory
            repo = git.Repo(os.getcwd(), search_parent_directories=True)
            git_dir = repo.git_dir
            index_lock_path = os.path.join(git_dir, 'index.lock')

            start_time = time.time()
            first_detection = True

            logger.info(f"Checking {index_lock_path=}")
            # Check if lock file exists and might be stale
            start = time.time()
            while time.time() - start < 0:
                if os.path.exists(index_lock_path):
                    try:
                        # Get file modification time
                        lock_mtime = os.path.getmtime(index_lock_path)
                        lock_age = time.time() - lock_mtime

                        # If lock is old, it's likely stale from NFS lag
                        if lock_age > stale_threshold:
                            logger.warning(f"Found stale index.lock (age: {lock_age:.1f}s), removing it")
                            try:
                                os.remove(index_lock_path)
                                logger.info("Successfully removed stale index.lock")
                                return
                            except OSError as e:
                                logger.debug(f"Failed to remove stale index.lock: {e}")
                                # Continue with normal wait logic
                    except OSError:
                        # Can't get mtime, continue with normal wait logic
                        pass

            logger.info(f"No {index_lock_path=}")

            while os.path.exists(index_lock_path):
                elapsed = time.time() - start_time

                if elapsed > max_wait:
                    # Try to remove it as a last resort
                    logger.warning(f"index.lock still exists after {max_wait}s, attempting to remove it")
                    try:
                        os.remove(index_lock_path)
                        logger.info("Successfully removed stuck index.lock")
                    except OSError as e:
                        logger.warning(f"Failed to remove index.lock: {e}, proceeding anyway")
                    break

                if first_detection:
                    logger.debug("Detected index.lock, waiting for it to be released (NFS lag workaround)")
                    first_detection = False

                time.sleep(check_interval)

            if not first_detection:
                # We did wait for the lock
                elapsed = time.time() - start_time
                logger.debug(f"index.lock released after {elapsed:.2f}s")

        except (InvalidGitRepositoryError, AttributeError):
            # Not in a git repo or couldn't find git_dir, proceed without waiting
            pass
        except Exception as e:
            # Log but don't fail - we want git commands to proceed
            logger.debug(f"Error checking for index.lock: {e}")

    def run_cmd(self, command: str, output: Optional[str] = None) -> str:
        """Run git command."""
        cmd_str = command.strip()

        # Check for no-rebase flag
        no_rebase = self.config.user.no_rebase
        if no_rebase:
            # Skip any commands that could modify commit hashes
            if any(cmd_str.startswith(cmd) for cmd in ("rebase",)):
                logger.info(f"Skipping command '{cmd_str}' due to --no-rebase")
                return ""

        if self.config.tool.pretend and 'push' in cmd_str:
            # Pretend mode - just log
            logger.info(f"> git {cmd_str}")
            return ""

        # Wait for index.lock to be released (NFS lag workaround)
        logger.info(f"< git {cmd_str}")
        self._wait_for_index_lock()

        # Log git commands at debug level
        logger.info(f"> git {cmd_str}")
        
        # Check if this is a cherry-pick command without --no-gpg-sign
        is_cherry_pick = cmd_str.startswith("cherry-pick") and "--no-gpg-sign" not in cmd_str
        max_retries = 3
        
        if is_cherry_pick:
            logger.debug(f"Detected cherry-pick command, will retry up to {max_retries} times on GPG signing failure")
        
        last_exception = None
        for attempt in range(max_retries):
            logger.info(f"Attempt {attempt=}")
            try:
                # Use GitPython
                repo = git.Repo(os.getcwd(), search_parent_directories=True)
                git_cmd = repo.git
                # Convert command to method call
                cmd_parts = shlex.split(cmd_str)
                git_command = cmd_parts[0]
                git_args = cmd_parts[1:]
                method = getattr(git_cmd, git_command.replace('-', '_'))
                result = method(*git_args)
                return result if isinstance(result, str) else str(result)
            except GitCommandError as e:
                last_exception = e
                # Check if this is a GPG signing failure
                if any(msg in str(e) for msg in [
                    "Another git process",
                    "communication with agent failed",
                    "Couldn't sign message",
                    "failed to write commit object",
                    "Signing file"
                ]):
                    if attempt < max_retries - 1:
                        logger.warning(f"GPG signing failed during cherry-pick, attempt {attempt + 1}/{max_retries}: {str(e)}")
                        # Abort the failed cherry-pick
                        try:
                            abort_repo = git.Repo(os.getcwd(), search_parent_directories=True)
                            abort_repo.git.cherry_pick('--abort')
                            logger.debug("Aborted failed cherry-pick")
                        except Exception:
                            # cherry-pick --abort might fail if not in cherry-pick state
                            pass
                        # Wait with exponential backoff
                        wait_time = (2 ** attempt) * 0.5  # 0.5s, 1s, 2s
                        logger.info(f"Waiting {wait_time}s before retry...")
                        time.sleep(wait_time)
                        continue
                # Not a signing error or not cherry-pick, raise immediately
                raise Exception(f"Git command failed: {str(e)}")
            except InvalidGitRepositoryError:
                raise Exception("Not in a git repository")
            except Exception as e:
                if str(e):
                    logger.error(f"Git error: {e}")
                raise
        
        # All retries exhausted
        if last_exception:
            raise Exception(f"Git command failed after {max_retries} attempts: {str(last_exception)}")
        raise Exception("Unexpected error in git command")

    def must_git(self, command: str, output: Optional[str] = None) -> str:
        """Run git command, failing on error."""
        return self.run_cmd(command, output)
