"""Fake PyGithub implementation for testing."""

import os
import yaml
import uuid
import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Any, Tuple, Optional, Set, Union, ClassVar

logger = logging.getLogger(__name__)

@dataclass
class FakeNamedUser:
    """Fake implementation of the NamedUser class from PyGithub."""
    login: str
    name: Optional[str] = None
    email: Optional[str] = None
    github_ref: Any = field(default=None, repr=False)
    
    def __post_init__(self):
        """Initialize default values after creation."""
        if self.name is None:
            self.name = self.login.capitalize()
        if self.email is None:
            self.email = f"{self.login}@example.com"

@dataclass
class FakeCommit:
    """Fake implementation of the Commit class from PyGithub."""
    sha: str
    message: str
    github_ref: Any = field(default=None, repr=False)
    
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
    github_ref: Any = field(default=None, repr=False)

@dataclass
class FakeRef:
    """Fake implementation of the Ref class from PyGithub."""
    ref: str
    sha: str = "fake-sha"
    repository_full_name: Optional[str] = None
    github_ref: Any = field(default=None, repr=False)
    
    @property
    def repo(self):
        """Get the repository this ref belongs to."""
        if not self.github_ref or not self.repository_full_name:
            return None
        return self.github_ref.get_repo(self.repository_full_name)
    
    # Allow dict-like access for compatibility
    def __getitem__(self, key):
        return getattr(self, key)

@dataclass
class FakeTeam:
    """Fake implementation of the Team class from PyGithub."""
    name: str
    slug: Optional[str] = None
    github_ref: Any = field(default=None, repr=False)
    
    def __post_init__(self):
        """Initialize default values after creation."""
        if self.slug is None:
            self.slug = self.name.lower()

@dataclass
class FakePullRequest:
    """Fake implementation of the PullRequest class from PyGithub."""
    number: int
    title: str = ""
    body: str = ""
    state: str = "open"
    merged: bool = False
    owner_login: str = "yang"
    repository_name: str = ""
    base_ref: str = "main"
    head_ref: str = ""
    head_sha: str = "fake-sha"
    reviewers: List[str] = field(default_factory=list)
    labels: List[str] = field(default_factory=list)
    auto_merge_enabled: bool = False
    auto_merge_method: Optional[str] = None
    commit_id: str = ""
    commit_hash: str = ""
    commit_subject: str = ""
    github_ref: Any = field(default=None, repr=False)
    
    def __post_init__(self):
        """Initialize default values after creation."""
        if not self.head_ref:
            self.head_ref = f"pr-{self.number}-branch"
        if not self.commit_id:
            self.commit_id = uuid.uuid4().hex[:8]
        if not self.commit_hash:
            self.commit_hash = uuid.uuid4().hex
        if not self.commit_subject and self.title:
            self.commit_subject = self.title
    
    @property
    def user(self):
        """Get the user who created this PR."""
        if not self.github_ref:
            return None
        return self.github_ref.get_user(self.owner_login)
    
    @property
    def repository(self):
        """Get the repository this PR belongs to."""
        if not self.github_ref:
            return None
        repo_full_name = f"{self.owner_login}/{self.repository_name}"
        return self.github_ref.get_repo(repo_full_name)
    
    @property
    def base(self):
        """Get the base ref for this PR."""
        return FakeRef(
            ref=self.base_ref, 
            repository_full_name=f"{self.owner_login}/{self.repository_name}",
            github_ref=self.github_ref
        )
    
    @property
    def head(self):
        """Get the head ref for this PR."""
        return FakeRef(
            ref=self.head_ref,
            sha=self.head_sha,
            repository_full_name=f"{self.owner_login}/{self.repository_name}",
            github_ref=self.github_ref
        )
    
    @property
    def commit(self):
        """Get the commit info for this PR."""
        return FakeCommitInfo(
            commit_id=self.commit_id,
            commit_hash=self.commit_hash,
            subject=self.commit_subject,
            github_ref=self.github_ref
        )
    
    @property
    def auto_merge(self):
        """Get auto merge info if enabled."""
        if self.auto_merge_enabled and self.auto_merge_method:
            return {"enabled": True, "method": self.auto_merge_method}
        return None
    
    def edit(self, title: str = None, body: str = None, state: str = None, 
            base: str = None, maintainer_can_modify: bool = None):
        """Update pull request properties."""
        if title is not None:
            self.title = title
            self.commit_subject = title
        if body is not None:
            self.body = body
        if state is not None:
            self.state = state
        if base is not None:
            self.base_ref = base
            
        # Save state after updating PR
        if self.github_ref:
            logger.debug(f"Saving state after editing PR #{self.number}")
            self.github_ref._save_state()
    
    def create_issue_comment(self, body: str):
        """Add a comment to the pull request."""
        # We don't need to store comments for testing
        logger.info(f"PR #{self.number} comment: {body}")
    
    def add_to_labels(self, *labels):
        """Add labels to the pull request."""
        for label in labels:
            if str(label) not in self.labels:
                self.labels.append(str(label))
        logger.info(f"PR #{self.number} labels: {labels}")
        
        # Save state after adding labels
        if self.github_ref:
            logger.debug(f"Saving state after adding labels to PR #{self.number}")
            self.github_ref._save_state()
    
    def get_commits(self):
        """Get commits in the pull request."""
        # For simplicity, just return a list with the main commit
        return [
            FakeCommit(
                sha=self.commit_hash, 
                message=f"{self.commit_subject}\n\ncommit-id:{self.commit_id}",
                github_ref=self.github_ref
            )
        ]
    
    def get_review_requests(self):
        """Get users and teams requested for review."""
        users = []
        teams = []
        
        if self.github_ref:
            for login in self.reviewers:
                user = self.github_ref.get_user(login)
                if user:
                    users.append(user)
        
        return (users, teams)
    
    def create_review_request(self, reviewers=None, team_reviewers=None):
        """Request reviews from users or teams."""
        if reviewers:
            for reviewer in reviewers:
                if isinstance(reviewer, str):
                    # Add reviewer by login
                    if reviewer not in self.reviewers:
                        self.reviewers.append(reviewer)
                    # Create user if doesn't exist
                    if self.github_ref:
                        self.github_ref.get_user(reviewer, create=True)
                else:
                    # Assume it's a FakeNamedUser
                    if reviewer.login not in self.reviewers:
                        self.reviewers.append(reviewer.login)
        
        # Save state after requesting reviews
        if self.github_ref:
            logger.debug(f"Saving state after requesting reviews for PR #{self.number}")
            self.github_ref._save_state()
    
    def merge(self, commit_title: str = None, commit_message: str = None, 
             sha: str = None, merge_method: str = "merge"):
        """Merge the pull request."""
        self.merged = True
        self.state = "closed"
        
        # Save state after merging PR
        if self.github_ref:
            logger.debug(f"Saving state after merging PR #{self.number}")
            self.github_ref._save_state()
    
    def enable_automerge(self, merge_method: str = "merge"):
        """Enable auto-merge for the pull request."""
        self.auto_merge_enabled = True
        self.auto_merge_method = merge_method
        logger.info(f"PR #{self.number} auto-merge enabled with method: {merge_method}")
        
        # Save state after enabling auto-merge
        if self.github_ref:
            logger.debug(f"Saving state after enabling auto-merge for PR #{self.number}")
            self.github_ref._save_state()

@dataclass
class FakeRepository:
    """Fake implementation of the Repository class from PyGithub."""
    owner_login: str
    name: str
    full_name: str
    next_pr_number: int = 1
    github_ref: Any = field(default=None, repr=False)
    
    @property
    def owner(self):
        """Get the owner of this repository."""
        if not self.github_ref:
            return None
        return self.github_ref.get_user(self.owner_login)
    
    def get_assignees(self):
        """Get assignable users for repository."""
        # For simplicity, just return some default users
        if not self.github_ref:
            return []
            
        result = []
        for login in ["yang", "testuser"]:
            user = self.github_ref.get_user(login, create=True)
            if user:
                result.append(user)
                
        return result
    
    def get_pull(self, number: int):
        """Get pull request by number."""
        if not self.github_ref:
            raise ValueError("Repository not linked to GitHub instance")
        return self.github_ref.get_pull(number)
    
    def get_pulls(self, state: str = "open", sort: str = None, 
                 direction: str = None, head: str = None, base: str = None):
        """Get pull requests with optional filtering."""
        if not self.github_ref:
            return []
            
        result = []
        for pr in self.github_ref.pull_requests.values():
            if pr.owner_login == self.owner_login and pr.repository_name == self.name:
                if state and pr.state != state:
                    continue
                if head and pr.head_ref != head:
                    continue
                if base and pr.base_ref != base:
                    continue
                result.append(pr)
        return result
    
    def create_pull(self, title: str, body: str, base: str, head: str, 
                   maintainer_can_modify: bool = True, draft: bool = False):
        """Create a new pull request."""
        if not self.github_ref:
            raise ValueError("Repository not linked to GitHub instance")
            
        # Create new PR
        pr_number = self.next_pr_number
        self.next_pr_number += 1
        
        pr = FakePullRequest(
            number=pr_number,
            title=title,
            body=body,
            base_ref=base,
            head_ref=head,
            head_sha="fake-sha",  # Placeholder
            owner_login=self.owner_login,
            repository_name=self.name,
            github_ref=self.github_ref
        )
        
        # Extract commit-id from branch name if it's in spr format
        branch_match = re.match(r'^spr/[^/]+/([a-f0-9]{8})', head)
        if branch_match:
            commit_id = branch_match.group(1)
            pr.commit_id = commit_id
        
        # Add commit-id to body if not present
        if "commit-id:" not in body:
            pr.body = f"{body}\ncommit-id:{pr.commit_id}"
        
        logger.debug(f"Created PR #{pr.number}")
        
        # Add PR to GitHub
        self.github_ref.pull_requests[pr_number] = pr
        
        # Save state after creating PR
        if self.github_ref:
            logger.debug(f"Saving state after creating PR #{pr.number}")
            self.github_ref._save_state()
        else:
            logger.warning(f"Cannot save state after creating PR - github_ref is None")
        
        return pr

@dataclass
class FakeRequester:
    """Fake implementation of requester for GraphQL."""
    github_ref: Any = field(default=None, repr=False)
    
    def requestJsonAndCheck(self, method: str, url: str, input: Dict[str, Any] = None):
        """Handle GraphQL requests."""
        if method == "POST" and url == "https://api.github.com/graphql" and input:
            return self._handle_graphql(input)
        
        # Default empty response
        return {}, {}
    
    def _handle_graphql(self, input: Dict[str, Any]):
        """Handle GraphQL query."""
        query = input.get("query", "")
        variables = input.get("variables", {})
        
        # Default empty response structure
        response = {
            "data": {
                "viewer": {
                    "login": "yang",
                    "pullRequests": {
                        "pageInfo": {
                            "hasNextPage": False,
                            "endCursor": None
                        },
                        "nodes": []
                    }
                }
            }
        }
        
        # For our tests, we just need to build a response with open PRs
        pr_nodes = []
        
        # Get all open PRs
        for pr in self.github_ref.pull_requests.values():
            if pr.state == "open":
                # Build PR node for response
                pr_node = {
                    "id": f"pr_{pr.number}",
                    "number": pr.number,
                    "title": pr.title,
                    "body": pr.body,
                    "baseRefName": pr.base_ref,
                    "headRefName": pr.head_ref,
                    "mergeable": "MERGEABLE",
                    "reviewDecision": None,
                    "repository": {
                        "id": f"repo_{pr.repository_name}"
                    },
                    "commits": {
                        "nodes": [
                            {
                                "commit": {
                                    "oid": pr.commit_hash,
                                    "messageHeadline": pr.title,
                                    "messageBody": f"{pr.body}\ncommit-id:{pr.commit_id}",
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
        response["data"]["viewer"]["pullRequests"]["nodes"] = pr_nodes
        
        # Log for debugging
        logger.info(f"GraphQL returned {len(pr_nodes)} open PRs (newest first)")
        
        # Return tuple of (headers, data)
        return {}, response

@dataclass
class FakeGithub:
    """Fake implementation of the Github class from PyGithub."""
    token: Optional[str] = None
    users: Dict[str, FakeNamedUser] = field(default_factory=dict)
    repositories: Dict[str, FakeRepository] = field(default_factory=dict)
    pull_requests: Dict[int, FakePullRequest] = field(default_factory=dict)
    _user: Optional[FakeNamedUser] = None
    data_dir: str = field(default="")
    state_file: str = field(default="")
    
    def __post_init__(self):
        """Initialize after creation."""
        # Set up file paths
        if not self.data_dir:
            self.data_dir = os.path.join(os.getcwd(), ".git", "fake_github")
        os.makedirs(self.data_dir, exist_ok=True)
        
        if not self.state_file:
            self.state_file = os.path.join(self.data_dir, "fake_github_state.yaml")
        
        # Load state if file exists
        self._load_state()
        
        # Create default user if doesn't exist
        if "yang" not in self.users:
            self.users["yang"] = FakeNamedUser(login="yang", github_ref=self)
        
        # Set default user
        self._user = self.users["yang"]
        
        # Set github_ref for all objects
        self._link_objects()
    
    def _link_objects(self):
        """Link all objects to this GitHub instance."""
        for user in self.users.values():
            user.github_ref = self
        
        for repo in self.repositories.values():
            repo.github_ref = self
        
        for pr in self.pull_requests.values():
            pr.github_ref = self
    
    def _load_state(self):
        """Load state from file."""
        if not os.path.exists(self.state_file):
            logger.info(f"State file {self.state_file} does not exist")
            return
        
        try:
            with open(self.state_file, "r") as f:
                data = yaml.unsafe_load(f)
            
            if data:
                # Copy all loaded data to our instance
                if "users" in data:
                    self.users = data["users"]
                if "repositories" in data:
                    self.repositories = data["repositories"]
                if "pull_requests" in data:
                    self.pull_requests = data["pull_requests"]
                
                logger.info(f"Loaded state from {self.state_file} - {len(self.users)} users, {len(self.repositories)} repos, {len(self.pull_requests)} PRs")
            else:
                logger.info(f"Empty state file {self.state_file}")
        except Exception as e:
            logger.error(f"Error loading state: {e}")
    
    def _save_state(self):
        """Save state to file."""
        logger.info(f"FakeGithub._save_state() called, saving to {self.state_file}")
        logger.info(f"State has {len(self.pull_requests)} PRs")
        
        # Prepare data for YAML
        data = {
            "users": self.users,
            "repositories": self.repositories,
            "pull_requests": self.pull_requests
        }
        
        try:
            os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
            with open(self.state_file, "w") as f:
                yaml.dump(data, f, default_flow_style=False)
            logger.info(f"Saved state to {self.state_file}")
        except Exception as e:
            logger.error(f"Error saving state: {e}")
    
    def get_user(self, login: str = None, create: bool = False):
        """Get user by login or current authenticated user."""
        if login is None:
            return self._user
        
        if login in self.users:
            return self.users[login]
        
        if create:
            user = FakeNamedUser(login=login, github_ref=self)
            self.users[login] = user
            return user
        
        return None
    
    def get_repo(self, full_name_or_id: str):
        """Get repository by full name."""
        if full_name_or_id in self.repositories:
            return self.repositories[full_name_or_id]
        
        # Create repo if it doesn't exist
        owner_login, name = full_name_or_id.split('/')
        repo = FakeRepository(
            owner_login=owner_login,
            name=name,
            full_name=full_name_or_id,
            github_ref=self
        )
        self.repositories[full_name_or_id] = repo
        
        # Create owner if doesn't exist
        if owner_login not in self.users:
            self.users[owner_login] = FakeNamedUser(login=owner_login, github_ref=self)
        
        return repo
    
    def get_pull(self, number: int):
        """Get pull request by number."""
        if number in self.pull_requests:
            return self.pull_requests[number]
        raise ValueError(f"Pull request #{number} not found")
    
    # Requester for GraphQL API
    @property
    def _Github__requester(self):
        """Fake requester for GraphQL."""
        return FakeRequester(github_ref=self)

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

def create_fake_github(token: Optional[str] = None) -> FakeGithub:
    """Create a fake GitHub instance for direct injection.
    
    This is the recommended way to create a fake GitHub client.
    """
    return FakeGithub(token=token)