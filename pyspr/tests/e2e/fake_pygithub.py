"""Fake PyGithub implementation for testing."""

from __future__ import annotations

import yaml
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Any, Tuple, Optional, Union, cast
import subprocess
from pathlib import Path
import os
import tempfile

from pyspr.util import ensure

logger = logging.getLogger(__name__)

@dataclass
class FakeNamedUser:
    """Fake implementation of the NamedUser class from PyGithub."""
    login: str
    name: str
    email: str
    maybe_github_ref: Any = field(default=None, repr=False)
    
    @property
    def github_ref(self) -> 'FakeGithub':
        return ensure(self.maybe_github_ref)
    
    def __post_init__(self):
        """Initialize default values after creation."""
        if not self.name:
            self.name = self.login.capitalize()
        if not self.email:
            self.email = f"{self.login}@example.com"

@dataclass
class FakeCommit:
    """Fake implementation of the Commit class from PyGithub."""
    sha: str
    message: str
    maybe_github_ref: Any = field(default=None, repr=False)
    
    @property
    def github_ref(self) -> 'FakeGithub':
        return ensure(self.maybe_github_ref)
    
    # Nested structure just like PyGithub
    @property
    def commit(self):
        return self

@dataclass
class FakeCommitInfo:
    """Fake implementation of a commit object for FakePullRequest."""
    commit_id: str
    commit_hash: str
    subject: str
    maybe_github_ref: Any = field(default=None, repr=False)
    
    @property
    def github_ref(self) -> 'FakeGithub':
        return ensure(self.maybe_github_ref)

@dataclass
class FakeRef:
    """Fake implementation of the Ref class from PyGithub."""
    ref: str
    sha: str
    repository_full_name: str
    maybe_github_ref: Any = field(default=None, repr=False)
    
    @property
    def github_ref(self) -> 'FakeGithub':
        return ensure(self.maybe_github_ref)
    
    @property
    def repo(self):
        """Get the repository this ref belongs to."""
        if not self.github_ref or not self.repository_full_name:
            return None
        return self.github_ref.get_repo(self.repository_full_name)

@dataclass
class FakeTeam:
    """Fake implementation of the Team class from PyGithub."""
    name: str
    slug: str
    maybe_github_ref: Any = field(default=None, repr=False)
    
    @property
    def github_ref(self) -> 'FakeGithub':
        return ensure(self.maybe_github_ref)
    
    def __post_init__(self):
        """Initialize default values after creation."""
        if not self.slug:
            self.slug = self.name.lower()

@dataclass
class FakePullRequestData:
    """Database record for a pull request."""
    number: int
    title: str
    body: str
    state: str
    merged: bool
    owner_login: str
    repository_name: str
    base_ref: str  # The ref name (e.g. main or spr/main/abc123)
    head_ref: str  # The ref name (e.g. spr/main/def456)
    reviewers: List[str] = field(default_factory=list)
    labels: List[str] = field(default_factory=list)
    auto_merge_enabled: bool = False
    auto_merge_method: str = ""
    maybe_github_ref: Any = field(default=None, repr=False)
    
    @property
    def github_ref(self) -> 'FakeGithub':
        return ensure(self.maybe_github_ref)

@dataclass
class FakePullRequest:
    """API response object for a pull request."""
    data_record: FakePullRequestData
    
    @property
    def _repo(self) -> 'FakeRepository':
        """Get the repository this PR belongs to (internal helper)."""
        return ensure(self.data_record.github_ref).get_repo(f"{self.data_record.owner_login}/{self.data_record.repository_name}")
    
    @property
    def data(self) -> FakePullRequestData:
        """Get the pull request data."""
        return self.data_record
    
    @property
    def number(self) -> int:
        return self.data_record.number
        
    @property
    def title(self) -> str:
        return self.data_record.title
    
    @title.setter
    def title(self, value: str):
        self.data_record.title = value
        
    @property
    def body(self) -> str:
        return self.data_record.body
    
    @body.setter
    def body(self, value: str):
        self.data_record.body = value
        
    @property
    def state(self) -> str:
        return self.data_record.state
    
    @state.setter
    def state(self, value: str):
        self.data_record.state = value
        
    @property
    def merged(self) -> bool:
        return self.data_record.merged
        
    @property
    def mergeable(self) -> bool:
        # For tests, always return True to indicate PRs are mergeable
        return True
        
    @property
    def mergeable_state(self) -> str:
        # For tests, always return 'clean' to indicate PRs are ready to merge
        return "clean"
    
    @merged.setter
    def merged(self, value: bool):
        self.data_record.merged = value
    
    @property
    def user(self):
        """Get the user who created this PR."""
        if not self.data_record.github_ref:
            return None
        return self.data_record.github_ref.get_user(self.data_record.owner_login)
    
    @property
    def repository(self):
        """Get the repository this PR belongs to."""
        if not self.data_record.github_ref:
            return None
        repo_full_name = f"{self.data_record.owner_login}/{self.data_record.repository_name}"
        return self.data_record.github_ref.get_repo(repo_full_name)
    
    @property
    def base(self):
        """Get the base ref for this PR."""
        # Get the commit info on demand
        repo_dir = Path(self.data_record.github_ref.data_dir).parent.parent.parent
        remote_dir = repo_dir / "remote.git"
        _, base_sha, _ = get_commit_info(self.data_record.base_ref, remote_dir)
        
        return FakeRef(
            ref=self.data_record.base_ref, 
            sha=base_sha,
            repository_full_name=f"{self.data_record.owner_login}/{self.data_record.repository_name}",
            maybe_github_ref=self.data_record.github_ref
        )
    
    @property
    def head(self):
        """Get the head ref for this PR."""
        # Get the commit info on demand
        repo_dir = Path(self.data_record.github_ref.data_dir).parent.parent.parent
        remote_dir = repo_dir / "remote.git"
        _, head_sha, _ = get_commit_info(self.data_record.head_ref, remote_dir)
        
        return FakeRef(
            ref=self.data_record.head_ref,
            sha=head_sha,
            repository_full_name=f"{self.data_record.owner_login}/{self.data_record.repository_name}",
            maybe_github_ref=self.data_record.github_ref
        )
    
    @property
    def commit(self):
        """Get the commit info for this PR."""
        # Get the commit info on demand
        repo_dir = Path(self.data_record.github_ref.data_dir).parent.parent.parent
        remote_dir = repo_dir / "remote.git"
        commit_id, commit_hash, commit_subject = get_commit_info(self.data_record.head_ref, remote_dir)
        
        return FakeCommitInfo(
            commit_id=commit_id,
            commit_hash=commit_hash,
            subject=commit_subject,
            maybe_github_ref=self.data_record.github_ref
        )
    
    @property
    def auto_merge(self):
        """Get auto merge info if enabled."""
        if self.data_record.auto_merge_enabled and self.data_record.auto_merge_method:
            return {"enabled": True, "method": self.data_record.auto_merge_method}
        return None
    
    def edit(self, title: str | None = None, body: str | None = None, state: str | None = None, 
            base: str | None = None, maintainer_can_modify: bool | None = None):
        """Update pull request properties."""
        if title is not None:
            self.data_record.title = title
        if body is not None:
            self.data_record.body = body
        if state is not None:
            self.data_record.state = state
        if base is not None:
            self.data_record.base_ref = base
            
        # Save state after updating PR
        if self.data_record.github_ref:
            logger.debug(f"Saving state after editing PR #{self.number}")
            self.data_record.github_ref.save_state()
    
    def create_issue_comment(self, body: str):
        """Add a comment to the pull request."""
        # We don't need to store comments for testing
        logger.info(f"PR #{self.number} comment: {body}")
    
    def add_to_labels(self, *labels: str):
        """Add labels to the pull request."""
        for label in labels:
            if str(label) not in self.data_record.labels:
                self.data_record.labels.append(str(label))
        logger.info(f"PR #{self.number} labels: {labels}")
        
        # Save state after adding labels
        if self.data_record.github_ref:
            logger.debug(f"Saving state after adding labels to PR #{self.number}")
            self.data_record.github_ref.save_state()
    
    def get_commits(self) -> list[FakeCommit]:
        """Get commits in the pull request."""
        return []  # Not needed for testing
    
    def get_review_requests(self) -> tuple[list['FakeNamedUser'], list[Any]]:
        """Get users and teams requested for review."""
        # Always reload state first to ensure we have the latest data
        if self.data_record.github_ref:
            logger.info(f"Reloading state before getting review requests for PR #{self.number}")
            self.data_record.github_ref.load_state()
            
            # After reloading state, check if our PR is still in the pull_requests dictionary
            pr_key = f"{self.data_record.owner_login}/{self.data_record.repository_name}:{self.number}"
            if pr_key in self.data_record.github_ref.pull_requests:
                loaded_pr = self.data_record.github_ref.pull_requests[pr_key]
                # Update our reviewers with the loaded PR's reviewers
                self.data_record.reviewers = loaded_pr.data_record.reviewers.copy()
            
        # Convert reviewer login strings to FakeNamedUser objects
        requested_users: List['FakeNamedUser'] = []
        logger.info(f"PR #{self.number} has reviewers: {self.data_record.reviewers}")
        if self.data_record.github_ref and self.data_record.reviewers:
            for reviewer_login in self.data_record.reviewers:
                user = self.data_record.github_ref.get_user(reviewer_login, create=True)
                if user:
                    requested_users.append(user)
                else:
                    logger.warning(f"Failed to create user for reviewer: {reviewer_login}")
        
        logger.info(f"Returning requested users: {[u.login for u in requested_users if hasattr(u, 'login')]}")
        return requested_users, []  # No teams for testing
    
    def create_review_request(self, reviewers: list[str] | None = None, team_reviewers: list[str] | None = None):
        """Request reviews from users or teams."""
        if reviewers:
            logger.info(f"PR #{self.number} adding reviewers: {reviewers}")
            
            # Always reload state first to ensure we have the latest data
            if self.data_record.github_ref:
                logger.info(f"Reloading state before adding reviewers to PR #{self.number}")
                self.data_record.github_ref.load_state()
                
            # Make sure we don't add duplicates
            for reviewer in reviewers:
                if reviewer not in self.data_record.reviewers:
                    self.data_record.reviewers.append(reviewer)
                    logger.info(f"PR #{self.number} added reviewer: {reviewer}")
                else:
                    logger.info(f"PR #{self.number} already has reviewer: {reviewer}")
            
            logger.info(f"PR #{self.number} reviewers after update: {self.data_record.reviewers}")
            
            # Save state after requesting reviews
            if self.data_record.github_ref:
                # Update the PR in the github_ref's pull_requests dictionary to ensure it's saved correctly
                key = f"{self.data_record.owner_login}/{self.data_record.repository_name}:{self.number}"
                if key in self.data_record.github_ref.pull_requests:
                    logger.info(f"Updating PR #{self.number} in github_ref.pull_requests with reviewers: {self.data_record.reviewers}")
                    # Make sure the PR in the dictionary has the updated reviewers
                    self.data_record.github_ref.pull_requests[key].data_record.reviewers = self.data_record.reviewers.copy()
                    
                    # Write directly to state file
                    logger.info(f"Writing state file directly for PR #{self.number}")
                    try:
                        self.data_record.github_ref.save_state()
                        logger.info(f"Successfully saved state for PR #{self.number}")
                        
                        # Verify the state file was written correctly
                        if self.data_record.github_ref.state_file.exists():
                            with open(self.data_record.github_ref.state_file, 'r') as f:
                                content = f.read()
                                logger.info(f"State file size: {len(content)} bytes")
                                logger.info(f"State file contains 'reviewers': {'reviewers' in content}")
                                # Check if each reviewer is in the state file
                                for rev in self.data_record.reviewers:
                                    logger.info(f"State file contains '{rev}': {rev in content}")
                    except Exception as e:
                        logger.error(f"Error saving state: {e}")
                
                # Force reload state to ensure it's properly saved
                try:
                    self.data_record.github_ref.load_state()
                    logger.info(f"Verified reviewers after reload: {self.data_record.reviewers}")
                except Exception as e:
                    logger.error(f"Error reloading state: {e}")
    
    def merge(self, commit_title: str = "", commit_message: str = "", 
             sha: str = "", merge_method: str = "merge"):
        """Merge the pull request."""
        self.data_record.merged = True
        self.data_record.state = "closed"
        
        # Create an actual merge commit in the Git repository by merging the PR branch into main
        try:
            # Get the repository directory
            repo_dir = Path(self.data_record.github_ref.data_dir).parent.parent.parent  # Go up to teststack
            remote_dir = repo_dir / "remote.git"  # Go up to tmpdir and find remote.git
            
            # Generate merge commit message
            if not commit_title:
                commit_title = f"Merge pull request #{self.number} from {self.data_record.head_ref}"
            if not commit_message:
                commit_message = f"Merge pull request #{self.number}\n\nThis closes #{self.number}"
            
            full_message = f"{commit_title}\n\n{commit_message}"
            
            # Create a temporary directory to perform the merge
            with tempfile.TemporaryDirectory() as tmpdir:
                clone_dir = Path(tmpdir) / "repo"
                clone_dir.mkdir()
                
                # Clone the bare repository
                _run_git_command(['clone', str(remote_dir), '.'], clone_dir)
                
                # Make sure we're on main branch
                _run_git_command(['checkout', 'main'], clone_dir)
                
                # Fetch the PR branch
                head_ref = self.data_record.head_ref
                logger.info(f"Fetching PR branch: {head_ref}")
                _run_git_command(['fetch', 'origin', head_ref], clone_dir)
                
                # Create a local branch to track the remote PR branch
                pr_branch = f"pr-{self.number}"
                _run_git_command(['checkout', '-b', pr_branch, f"origin/{head_ref}"], clone_dir)
                
                # Go back to main branch to prepare for merge
                _run_git_command(['checkout', 'main'], clone_dir)
                
                # Prepare the merge message file
                message_file = clone_dir / "merge_message.txt"
                with open(message_file, 'w') as f:
                    f.write(full_message)
                
                # Perform the actual merge with the PR branch
                if merge_method == 'squash':
                    # For squash merge, we need to squash all commits from the PR branch
                    # Get the base commit where the PR branch diverged from main
                    base_ref = self.data_record.base_ref
                    logger.info(f"Squash merging PR branch {pr_branch} into {base_ref}")
                    _run_git_command(['merge', '--squash', pr_branch], clone_dir)
                    _run_git_command(['commit', '-F', 'merge_message.txt'], clone_dir)
                elif merge_method == 'rebase':
                    # For rebase merge, we rebase the PR branch onto main
                    logger.info(f"Rebase merging PR branch {pr_branch}")
                    _run_git_command(['rebase', pr_branch], clone_dir)
                    _run_git_command(['commit', '--amend', '-F', 'merge_message.txt'], clone_dir)
                else:  # Regular merge
                    # For regular merge, we do a no-ff merge to ensure a merge commit
                    logger.info(f"Merging PR branch {pr_branch} with no-ff")
                    _run_git_command(['merge', '--no-ff', '-F', 'merge_message.txt', pr_branch], clone_dir)
                
                # Push the merge commit to the remote repository
                _run_git_command(['push', 'origin', 'main'], clone_dir)
                
                logger.info(f"Created merge commit for PR #{self.number} with message: {commit_title}")
        except Exception as e:
            logger.error(f"Error creating merge commit: {e}")
            logger.exception(e)  # Log the full exception for debugging
        
        # Save state after merging PR
        if self.data_record.github_ref:
            logger.debug(f"Saving state after merging PR #{self.number}")
            self.data_record.github_ref.save_state()
    
    def enable_automerge(self, merge_method: str = "merge"):
        """Enable auto-merge for the pull request."""
        self.data_record.auto_merge_enabled = True
        self.data_record.auto_merge_method = merge_method
        
        # Save state after enabling auto-merge
        if self.data_record.github_ref:
            logger.debug(f"Saving state after enabling auto-merge for PR #{self.number}")
            self.data_record.github_ref.save_state()

@dataclass
class FakeRepository:
    """Fake implementation of the Repository class from PyGithub."""
    owner_login: str
    name: str
    full_name: str
    next_pr_number: int
    maybe_github_ref: Any = field(default=None, repr=False)
    
    @property
    def github_ref(self) -> 'FakeGithub':
        return ensure(self.maybe_github_ref)
    
    @property
    def owner(self):
        """Get the owner of this repository."""
        if not self.github_ref:
            return None
        return self.github_ref.get_user(self.owner_login)
    
    def get_assignees(self) -> List['FakeNamedUser']:
        """Get assignable users for repository."""
        # For simplicity, just return some default users
        if not self.github_ref:
            return []
            
        # Always reload state first
        self.github_ref.load_state()
            
        # Filter out None values to satisfy the return type
        return [u for u in [self.github_ref.get_user(login, create=True) for login in ["yang", "testuser", "testluser"] if login] if u is not None]
    
    def get_pull(self, number: int) -> 'FakePullRequest':
        """Get pull request by number."""
        if not self.github_ref:
            raise ValueError("Repository not linked to GitHub instance")
            
        # Always reload state first to ensure we have the latest data
        logger.info(f"Reloading state before getting PR #{number} from repository {self.full_name}")
        self.github_ref.load_state()
            
        pr = self.github_ref.get_pull(number, repo_name=self.full_name)
        if pr is None:
            raise ValueError(f"PR #{number} not found in repository {self.full_name}")
        return pr

    def get_pulls(self, state: str = "open", sort: str = "", 
                 direction: str = "", head: str = "", base: str = "") -> List['FakePullRequest']:
        """Get pull requests with optional filtering."""
        if not self.github_ref:
            return []
            
        # Always reload state first
        self.github_ref.load_state()
        
        # Use a properly typed list and filter PRs based on parameters
        result: List['FakePullRequest'] = []
        for pr in self.github_ref.pull_requests.values():
            # Use a helper method to check if PR belongs to this repo
            if self._pr_belongs_to_repo(pr) and \
               (not state or pr.state == state) and \
               (not head or pr.head.ref == head) and \
               (not base or pr.base.ref == base):
                result.append(pr)
        
        return result
        
    def _pr_belongs_to_repo(self, pr: 'FakePullRequest') -> bool:
        """Check if a PR belongs to this repository."""
        # Access data fields through data_record
        return pr.data_record.owner_login == self.owner_login and \
               pr.data_record.repository_name == self.name
    def create_pull(self, title: str, body: str, base: str, head: str, 
                   maintainer_can_modify: bool = True, draft: bool = False) -> 'FakePullRequest':
        """Create a new pull request."""
        if not self.github_ref:
            raise ValueError("Repository not linked to GitHub instance")
        
        # Always reload state first
        self.github_ref.load_state()
        
        # Check if a PR already exists for this branch
        for pr in self.github_ref.pull_requests.values():
            if pr.head.ref == head and pr.state == "open":
                # GitHub returns 422 with specific error message for duplicate PRs
                from github import GithubException
                raise GithubException(
                    422, 
                    {
                        "message": "Validation Failed",
                        "errors": [{
                            "resource": "PullRequest",
                            "code": "custom", 
                            "message": f"A pull request already exists for {self.owner_login}:{head}."
                        }],
                        "documentation_url": "https://docs.github.com/rest/pulls/pulls#create-a-pull-request",
                        "status": "422"
                    },
                    None
                )
        
        # Create new PR with repository-specific number
        pr_number = self.next_pr_number
        self.next_pr_number += 1
        
        # Get the remote git repository path
        # Directory structure is:
        # tmpdir/
        #   ├── remote.git/    # The bare repository
        #   └── teststack/     # The working repository
        #       └── .git/
        #           └── fake_github/  # Where our state is stored (self.github_ref.data_dir)
        repo_dir = Path(self.github_ref.data_dir).parent.parent.parent  # Go up to teststack
        remote_dir = repo_dir / "remote.git"  # Go up to tmpdir and find remote.git
        
        # Get the actual commit information from the remote git repository
        # We get the commit information but don't need it for PR creation in this mock
        # The PR object in the real GitHub API would use these, but our mock doesn't need them
        _head_id, _head_hash, _head_subject = get_commit_info(head, remote_dir)
        
        # Get base commit info - for main branch, use origin/main
        _base_id, _base_hash, _base_subject = get_commit_info(base, remote_dir)
        
        # Create PR with real commit information
        pr_data = FakePullRequestData(
            number=pr_number,
            title=title,
            body=body,
            state="open",  # New PRs are always open
            merged=False,  # New PRs are never merged
            owner_login=self.owner_login,
            repository_name=self.name,
            base_ref=base,  # Keep the original base ref
            head_ref=head,
            reviewers=[],  # Start with no reviewers
            labels=[],     # Start with no labels
            auto_merge_enabled=False,  # Auto-merge is off by default
            auto_merge_method="merge",  # Default merge method
            maybe_github_ref=self.github_ref
        )
        
        pr = FakePullRequest(pr_data)
        
        logger.debug(f"Created PR #{pr.number} in repo {self.full_name}")
        
        # Add PR to GitHub's global PR dictionary using a composite key
        # Format: "owner/repo:number" to ensure uniqueness across repos
        pr_key = f"{self.full_name}:{pr_number}"
        self.github_ref.pull_requests[pr_key] = pr
        
        # Save state after creating PR
        if self.github_ref:
            logger.debug(f"Saving state after creating PR #{pr.number}")
            self.github_ref.save_state()
        else:
            logger.warning("Cannot save state after creating PR - github_ref is None")
        
        return pr

def get_commit_info(ref: str, remote_path: Path) -> Tuple[str, str, str]:
    """Get commit info (id, hash, subject) for a given ref from the remote repository."""
    # Create a temporary clone to work with
    with tempfile.TemporaryDirectory() as tmpdir:
        clone_dir = Path(tmpdir) / "repo"
        clone_dir.mkdir()
        
        # Clone the bare repository
        _run_git_command(['clone', str(remote_path), '.'], clone_dir)
        
        # Fetch all refs
        _run_git_command(['fetch', '--all'], clone_dir)
        
        # For main branch, get the commit from origin/main
        if ref == "main":
            ref = "origin/main"
        elif not ref.startswith("origin/"):
            # For other refs, try origin/{ref} first
            origin_ref = f"origin/{ref}"
            if _run_git_command(['rev-parse', '--verify', origin_ref], clone_dir, check=False):
                ref = origin_ref
        
        # Get the commit info
        commit_hash = _run_git_command(['rev-parse', ref], clone_dir)
        commit_id = _run_git_command(['rev-parse', '--short', commit_hash], clone_dir)
        commit_subject = _run_git_command(['log', '-1', '--format=%s', commit_hash], clone_dir)
        
        return commit_id, commit_hash, commit_subject

def _run_git_command(cmd: List[str], cwd: Path, check: bool = True) -> str:
    """Run a git command and return its output."""
    try:
        result = subprocess.run(['git'] + cmd, 
                              cwd=str(cwd),
                              capture_output=True, 
                              text=True, 
                              check=check)
        return result.stdout.strip()
    except subprocess.CalledProcessError:
        if check:
            raise
        return ""

@dataclass
class FakeRequester:
    """Fake implementation of requester for GraphQL."""
    maybe_github_ref: Any = field(default=None, repr=False)
    
    @property
    def github_ref(self) -> 'FakeGithub':
        return ensure(self.maybe_github_ref)
    
    def requestJsonAndCheck(self, method: str, url: str, input: Optional[Dict[str, Any]] = None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Handle GraphQL requests."""
        if method == "POST" and url == "https://api.github.com/graphql" and input:
            return self._handle_graphql(input)
        
        # Default empty response
        return cast(Tuple[Dict[str, Any], Dict[str, Any]], ({}, {}))
    
    def _handle_graphql(self, input: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Handle GraphQL query."""
        # Always reload state first
        self.github_ref.load_state()
        
        _query = input.get("query", "")
        _variables = input.get("variables", {})
        
        # Default empty response structure
        response: Dict[str, Dict[str, Dict[str, Dict[str, bool | None] | List[Dict[str, Any]]]]] = {
            "data": {
                "search": {
                    "pageInfo": {
                        "hasNextPage": False,
                        "endCursor": None
                    },
                    "nodes": []
                }
            }
        }
        
        # For our tests, we just need to build a response with open PRs
        pr_nodes: List[Dict[str, Any]] = []
        
        # Get all open PRs - debug dictionary contents
        logger.debug(f"GraphQL request - PR dictionary has {len(self.github_ref.pull_requests)} entries")
        for key in self.github_ref.pull_requests:
            logger.debug(f"PR key: {key}")
            
        # Get all open PRs - make a copy of the items since we're filtering
        pr_items: List[Tuple[str, FakePullRequest]] = list(self.github_ref.pull_requests.items())
        
        for key, pr in pr_items:
            logger.debug(f"Checking PR with key {key}: state={pr.state}, title={pr.title}")
            if pr.state == "open":
                # Build PR node for response
                pr_node: Dict[str, Any] = {
                    "id": f"pr_{pr.number}",
                    "number": pr.number,
                    "title": pr.title,
                    "body": pr.body,
                    "baseRefName": pr.base.ref,
                    "headRefName": pr.head.ref,
                    "mergeable": "MERGEABLE",
                    "reviewDecision": None,
                    "repository": {
                        "id": f"repo_{pr.data_record.repository_name}"
                    },
                    "commits": {
                        "nodes": [
                            {
                                "commit": {
                                    "oid": pr.commit.commit_hash,
                                    "messageHeadline": pr.title,
                                    "messageBody": pr.body,
                                    "statusCheckRollup": {
                                        "state": "SUCCESS"
                                    }
                                }
                            }
                        ]
                    }
                }
                pr_nodes.append(pr_node)
        
        # Add PR nodes to response
        response["data"]["search"]["nodes"] = pr_nodes
        
        # Log for debugging
        logger.info(f"GraphQL returned {len(pr_nodes)} open PRs (newest first)")
        
        # Return tuple of (headers, data)
        return cast(Tuple[Dict[str, Any], Dict[str, Any]], ({}, response))

@dataclass
class FakeGithub:
    """Fake implementation of the Github class from PyGithub."""
    token: str
    users: Dict[str, FakeNamedUser]
    repositories: Dict[str, FakeRepository]
    pull_requests: Dict[str, FakePullRequest]
    _user: FakeNamedUser
    data_dir: Path
    state_file: Path
    
    def initialize(self, load_state: bool = True):
        """Initialize the instance with proper setup.
        
        This method handles the side effects like creating directories,
        loading state from disk, and linking objects. Call this after
        constructing the instance when you want these side effects.
        
        Args:
            load_state: Whether to load state from the state file if it exists
            
        Returns:
            Self, for method chaining
        """
        # Set up file paths
        if not self.data_dir:
            self.data_dir = Path(os.getcwd()) / ".git" / "fake_github"
        else:
            # Ensure data_dir is a Path object
            self.data_dir = Path(self.data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        if not self.state_file:
            self.state_file = self.data_dir / "fake_github_state.yaml"
        else:
            # Ensure state_file is a Path object
            self.state_file = Path(self.state_file)
        
        # Load state if requested and file exists
        if load_state:
            self._load_state()
        
        # Create default user if doesn't exist
        if "yang" not in self.users:
            self.users["yang"] = FakeNamedUser(
                login="yang",
                name="Yang",
                email="yang@example.com",
                maybe_github_ref=self
            )
        
        # Link all objects to this GitHub instance
        self._link_objects()
        
        return self
    
    def _link_objects(self):
        """Link all objects to this GitHub instance."""
        for user in self.users.values():
            user.maybe_github_ref = self
        
        for repo in self.repositories.values():
            repo.maybe_github_ref = self
        
        for pr in self.pull_requests.values():
            pr.data_record.maybe_github_ref = self
    
    def _load_state(self):
        """Load state from file."""
        if not self.state_file.exists():
            logger.info(f"State file {self.state_file} does not exist")
            return
        
        try:
            # Configure YAML to handle object references properly
            # Type ignore for YAML loader configuration - this is a known pattern
            yaml.Loader.ignore_aliases = lambda *args: False  # type: ignore
            
            with open(self.state_file, "r") as f:
                # Use the Loader that preserves object types and references
                data = yaml.load(f, Loader=yaml.Loader)
            
            if data:
                if isinstance(data, FakeGithub):
                    # New format - direct object
                    self.users = data.users
                    self.repositories = data.repositories
                    self.pull_requests = data.pull_requests
                    self._user = data._user
                    # Don't copy state_file and data_dir
                
                # Set proper github_ref for all objects after loading
                self._link_objects()
                
                # Fix next_pr_number in repositories to ensure new PRs get unique numbers
                max_pr_numbers: Dict[str, int] = {}
                for pr in self.pull_requests.values():
                    repo_name = f"{pr.data_record.owner_login}/{pr.data_record.repository_name}"
                    current_max = max_pr_numbers.get(repo_name, 0)
                    max_pr_numbers[repo_name] = max(current_max, pr.number + 1)
                
                # Update next_pr_number in repositories
                for repo_name, repo in self.repositories.items():
                    if repo_name in max_pr_numbers:
                        repo.next_pr_number = max_pr_numbers[repo_name]
                    else:
                        repo.next_pr_number = 1
                
                logger.info(f"Loaded state from {self.state_file} - {len(self.users)} users, {len(self.repositories)} repos, {len(self.pull_requests)} PRs")
            else:
                logger.info(f"Empty state file {self.state_file}")
        except Exception as e:
            logger.error(f"Error loading state: {e}")
            logger.exception(e)  # Log the full exception for debugging
            
    def load_state(self):
        """Public method to load state from file."""
        # Protected method is accessible within this class
        return self._load_state()
    
    def _save_state(self):
        """Save state to file."""
        logger.info(f"FakeGithub._save_state() called, saving to {self.state_file}")
        logger.info(f"State has {len(self.pull_requests)} PRs")
        
        # Clean up any duplicate PRs by using a temporary dict with only composite keys
        clean_pull_requests = {}
        for key, pr in self.pull_requests.items():
            if ":" in key:  # This is a composite key like "owner/repo:number"
                clean_pull_requests[key] = pr
            # Skip any numeric keys or non-composite keys

        # Replace the pull_requests dict with the cleaned version
        self.pull_requests = clean_pull_requests
        
        # Configure YAML to properly handle object references
        # Type ignore for YAML dumper configuration - this is a known pattern
        yaml.Dumper.ignore_aliases = lambda *args: False  # type: ignore
        
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.state_file, "w") as f:
                # Use yaml.Dumper to preserve object types and references
                yaml.dump(self, f, Dumper=yaml.Dumper, default_flow_style=False)
            logger.info(f"Saved state to {self.state_file}")
        except Exception as e:
            logger.error(f"Error saving state: {e}")
            logger.exception(e)  # Log the full exception for debugging
    
    def save_state(self):
        """Public method to save state to file."""
        # Protected method is accessible within this class
        return self._save_state()
    
    def get_user(self, login: Optional[str] = None, **kwargs: Any) -> Optional['FakeNamedUser']:
        """Get user by login or current authenticated user."""
        # Always reload state first
        self.load_state()        
        if login is None:
            return self._user
        
        if login in self.users:
            return self.users[login]
        
        create = kwargs.get('create', False)
        if create:
            # Provide default values for name and email when creating a new user
            user = FakeNamedUser(login=login, name=login, email=f"{login}@example.com", maybe_github_ref=self)
            self.users[login] = user
            return user
        
        return None
    
    def get_repo(self, full_name_or_id: str) -> 'FakeRepository':
        """Get repository by full name."""
        # Always reload state first
        self.load_state()
        
        if full_name_or_id in self.repositories:
            return self.repositories[full_name_or_id]
        
        # Create repo if it doesn't exist
        owner_login, name = full_name_or_id.split('/')
        repo = FakeRepository(
            owner_login=owner_login,
            name=name,
            full_name=full_name_or_id,
            next_pr_number=1,
            maybe_github_ref=self
        )
        
        # Store in repositories dict
        self.repositories[full_name_or_id] = repo
        
        return repo
    
    def get_pull(self, number: int, repo_name: Optional[str] = None) -> Optional['FakePullRequest']:
        """Get pull request by number.
        
        Args:
            number: The PR number
            repo_name: Optional repository name (full_name format: "owner/repo")
                      If not provided, will try to find any PR with this number
        """
        # Always reload state first
        self.load_state()
        
        # If repo_name provided, use composite key
        composite_key = f"{repo_name}:{number}" if repo_name else None
        if composite_key and composite_key in self.pull_requests:
            return self.pull_requests[composite_key]
        
        # If specific repository not provided, look through all PRs to find one with matching number
        for _, pr in self.pull_requests.items():
            if pr.number == number:
                return pr
                
        raise ValueError(f"Pull request #{number} not found")
    
    # Requester for GraphQL API
    @property
    def _Github__requester(self) -> FakeRequester:
        """Fake requester for GraphQL."""
        return FakeRequester(maybe_github_ref=self)

# Fake exceptions
class FakeGithubException(Exception):
    """Base exception class for fake GitHub."""
    pass

class FakeBadCredentialsException(FakeGithubException):
    """Fake bad credentials exception."""
    pass

class FakeUnknownObjectException(FakeGithubException):
    """Fake unknown object exception."""
    pass

def create_fake_github(token: Optional[str] = None, 
                    data_dir: Optional[Path] = None,
                    state_file: Optional[Path] = None) -> FakeGithub:
    """Create a fake GitHub instance for direct injection.

    This is the recommended way to create a fake GitHub client.
    
    Args:
        token: Optional GitHub token (not used, but included for API compatibility)
        data_dir: Directory to store state files in, defaults to $CWD/.git/fake_github
        state_file: Path to the state file to use, defaults to data_dir/fake_github_state.yaml
    """
    if not data_dir:
        data_dir = Path(os.getcwd()) / ".git" / "fake_github"
    if not state_file:
        state_file = data_dir / "fake_github_state.yaml"
    
    # Create initial empty state
    github = FakeGithub(
        token=token or "",
        users={},
        repositories={},
        pull_requests={},
        _user=FakeNamedUser(login="yang", name="Yang", email="yang@example.com", maybe_github_ref=None),
        data_dir=data_dir,
        state_file=state_file
    )
    
    # Initialize with proper setup
    github.initialize()
    
    return github