"""Fake PyGithub implementation for testing."""

import os
import json
import uuid
import logging
import re
from typing import Dict, List, Any, Tuple, Optional, Set, Union, cast, ClassVar
from dataclasses import dataclass, field
from pathlib import Path
from pydantic import BaseModel, TypeAdapter, Field, model_validator

logger = logging.getLogger(__name__)

class FakeGithubState(BaseModel):
    """Root state model that contains all GitHub entities with their relationships."""
    users: Dict[str, "FakeNamedUser"] = Field(default_factory=dict)
    repositories: Dict[str, "FakeRepository"] = Field(default_factory=dict)
    pull_requests: Dict[int, "FakePullRequest"] = Field(default_factory=dict)
    
    def __init__(self, **data):
        super().__init__(**data)
        self._link_objects()
    
    def _link_objects(self):
        """Link all child objects to this state instance."""
        for user in self.users.values():
            user.state_ref = self
            
        for repo in self.repositories.values():
            repo.state_ref = self
            
        for pr in self.pull_requests.values():
            pr.state_ref = self
        
        # Handle repositories with old schema missing owner_login
        keys_to_remove = []
        repos_to_add = {}
        
        for key, repo in self.repositories.items():
            if not hasattr(repo, 'owner_login') or not repo.owner_login:
                # Try to extract owner_login from old schema
                if hasattr(repo, 'owner'):
                    owner = repo.owner
                    if isinstance(owner, str):
                        # For full backward compatibility
                        repo.owner_login = owner
                    elif hasattr(repo, 'full_name'):
                        # Extract from full_name as a fallback
                        parts = repo.full_name.split('/')
                        if len(parts) == 2:
                            repo.owner_login = parts[0]
                            keys_to_remove.append(key)
                            repos_to_add[repo.full_name] = repo
                            
        # Remove keys that need updating
        for key in keys_to_remove:
            del self.repositories[key]
            
        # Add with correct keys
        for key, repo in repos_to_add.items():
            self.repositories[key] = repo
    
    @model_validator(mode='after')
    def validate_and_link(self):
        """Link all objects after validation (during deserialization)."""
        self._link_objects()
        return self
    
    def create_user(self, login: str, **kwargs) -> "FakeNamedUser":
        """Create a new user linked to this state."""
        user = FakeNamedUser(login=login, **kwargs)
        user.state_ref = self
        self.users[login] = user
        return user
    
    def create_repository(self, owner_login: str, name: str, **kwargs) -> "FakeRepository":
        """Create a new repository linked to this state."""
        full_name = f"{owner_login}/{name}"
        repo = FakeRepository(
            owner_login=owner_login,
            name=name, 
            full_name=full_name,
            **kwargs
        )
        repo.state_ref = self
        self.repositories[full_name] = repo
        return repo
    
    def create_pull_request(self, number: int, repo_full_name: str, **kwargs) -> "FakePullRequest":
        """Create a new PR linked to this state."""
        owner_login, repo_name = repo_full_name.split('/')
        # Check if owner_login is already in kwargs
        if 'owner_login' not in kwargs:
            kwargs['owner_login'] = owner_login
        if 'repository_name' not in kwargs:
            kwargs['repository_name'] = repo_name
        
        pr = FakePullRequest(
            number=number,
            **kwargs
        )
        pr.state_ref = self
        self.pull_requests[number] = pr
        return pr
    
    def get_repo(self, full_name: str) -> "FakeRepository":
        """Get repository by full name, creating it if it doesn't exist."""
        if full_name not in self.repositories:
            owner_login, repo_name = full_name.split('/')
            # Create owner if doesn't exist
            if owner_login not in self.users:
                self.create_user(login=owner_login)
            
            self.create_repository(owner_login=owner_login, name=repo_name)
            
        return self.repositories[full_name]
    
    def save_to_file(self, file_path: str):
        """Save state to a JSON file."""
        try:
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            with open(file_path, "w") as f:
                f.write(self.model_dump_json(indent=2))
            logger.info(f"Saved state to {file_path}")
        except Exception as e:
            logger.error(f"Error saving state: {e}")
    
    @classmethod
    def load_from_file(cls, file_path: str) -> "FakeGithubState":
        """Load state from a JSON file."""
        try:
            if os.path.exists(file_path):
                with open(file_path, "r") as f:
                    state_json = f.read()
                state = cls.model_validate_json(state_json)
                logger.info(f"Loaded state from {file_path}")
                return state
        except Exception as e:
            logger.error(f"Error loading state: {e}")
        
        # Return empty state if loading failed
        return cls()


class FakeNamedUser(BaseModel):
    """Fake implementation of the NamedUser class from PyGithub."""
    login: str
    name: Optional[str] = None
    email: Optional[str] = None
    
    # Non-serialized reference to parent state
    state_ref: Optional[FakeGithubState] = Field(default=None, exclude=True)
    
    def model_post_init(self, __context):
        """Initialize default values after creation."""
        if self.name is None:
            self.name = self.login.capitalize()
        if self.email is None:
            self.email = f"{self.login}@example.com"
    
    @property
    def repositories(self) -> List["FakeRepository"]:
        """Get repositories owned by this user."""
        if not self.state_ref:
            return []
        return [repo for repo in self.state_ref.repositories.values() 
                if repo.owner_login == self.login]


class FakeCommit(BaseModel):
    """Fake implementation of the Commit class from PyGithub."""
    sha: str
    message: str
    
    # Non-serialized reference to parent state
    state_ref: Optional[FakeGithubState] = Field(default=None, exclude=True)
    
    # Nested structure just like PyGithub
    @property
    def commit(self):
        return self


class FakeCommitInfo(BaseModel):
    """Fake implementation of a commit object for FakePullRequest."""
    commit_id: str
    commit_hash: str
    subject: str
    
    # Non-serialized reference to parent state
    state_ref: Optional[FakeGithubState] = Field(default=None, exclude=True)


class FakeRef(BaseModel):
    """Fake implementation of the Ref class from PyGithub."""
    ref: str
    sha: str = "fake-sha"
    repository_full_name: Optional[str] = None
    
    # Non-serialized reference to parent state
    state_ref: Optional[FakeGithubState] = Field(default=None, exclude=True)
    
    @property
    def repo(self) -> Optional["FakeRepository"]:
        """Get the repository this ref belongs to."""
        if not self.state_ref or not self.repository_full_name:
            return None
        return self.state_ref.repositories.get(self.repository_full_name)
    
    # Allow dict-like access for compatibility
    def __getitem__(self, key):
        return getattr(self, key)


class FakeTeam(BaseModel):
    """Fake implementation of the Team class from PyGithub."""
    name: str
    slug: Optional[str] = None
    
    # Non-serialized reference to parent state
    state_ref: Optional[FakeGithubState] = Field(default=None, exclude=True)
    
    def model_post_init(self, __context):
        """Initialize default values after creation."""
        if self.slug is None:
            self.slug = self.name.lower()


class FakePullRequest(BaseModel):
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
    reviewers: List[str] = Field(default_factory=list)
    labels: List[str] = Field(default_factory=list)
    auto_merge_enabled: bool = False
    auto_merge_method: Optional[str] = None
    commit_id: str = ""
    commit_hash: str = ""
    commit_subject: str = ""
    
    # Non-serialized reference to parent state
    state_ref: Optional[FakeGithubState] = Field(default=None, exclude=True)
    
    def model_post_init(self, __context):
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
    def user(self) -> Optional["FakeNamedUser"]:
        """Get the user who created this PR."""
        if not self.state_ref:
            return None
        return self.state_ref.users.get(self.owner_login)
    
    @property
    def repository(self) -> Optional["FakeRepository"]:
        """Get the repository this PR belongs to."""
        if not self.state_ref:
            return None
        repo_full_name = f"{self.owner_login}/{self.repository_name}"
        return self.state_ref.repositories.get(repo_full_name)
    
    @property
    def base(self) -> "FakeRef":
        """Get the base ref for this PR."""
        return FakeRef(
            ref=self.base_ref, 
            repository_full_name=f"{self.owner_login}/{self.repository_name}",
            state_ref=self.state_ref
        )
    
    @property
    def head(self) -> "FakeRef":
        """Get the head ref for this PR."""
        return FakeRef(
            ref=self.head_ref,
            sha=self.head_sha,
            repository_full_name=f"{self.owner_login}/{self.repository_name}",
            state_ref=self.state_ref
        )
    
    @property
    def commit(self) -> "FakeCommitInfo":
        """Get the commit info for this PR."""
        return FakeCommitInfo(
            commit_id=self.commit_id,
            commit_hash=self.commit_hash,
            subject=self.commit_subject,
            state_ref=self.state_ref
        )
    
    @property
    def auto_merge(self) -> Optional[Dict[str, Any]]:
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
        if self.state_ref:
            logger.debug(f"Saved state after editing PR #{self.number}")
    
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
        if self.state_ref:
            logger.debug(f"Saved state after adding labels to PR #{self.number}")
    
    def get_commits(self):
        """Get commits in the pull request."""
        # For simplicity, just return a list with the main commit
        return [
            FakeCommit(
                sha=self.commit_hash, 
                message=f"{self.commit_subject}\n\ncommit-id:{self.commit_id}",
                state_ref=self.state_ref
            )
        ]
    
    def get_review_requests(self) -> Tuple[List["FakeNamedUser"], List["FakeTeam"]]:
        """Get users and teams requested for review."""
        users = []
        teams = []
        
        if self.state_ref:
            for login in self.reviewers:
                user = self.state_ref.users.get(login)
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
                    if self.state_ref and reviewer not in self.state_ref.users:
                        self.state_ref.create_user(login=reviewer)
                else:
                    # Assume it's a FakeNamedUser
                    if reviewer.login not in self.reviewers:
                        self.reviewers.append(reviewer.login)
        
        # Save state after requesting reviews
        if self.state_ref:
            logger.debug(f"Saved state after requesting reviews for PR #{self.number}")
    
    def merge(self, commit_title: str = None, commit_message: str = None, 
             sha: str = None, merge_method: str = "merge"):
        """Merge the pull request."""
        self.merged = True
        self.state = "closed"
        
        # Save state after merging PR
        if self.state_ref:
            logger.debug(f"Saved state after merging PR #{self.number}")
    
    def enable_automerge(self, merge_method: str = "merge"):
        """Enable auto-merge for the pull request."""
        self.auto_merge_enabled = True
        self.auto_merge_method = merge_method
        logger.info(f"PR #{self.number} auto-merge enabled with method: {merge_method}")
        
        # Save state after enabling auto-merge
        if self.state_ref:
            logger.debug(f"Saved state after enabling auto-merge for PR #{self.number}")


class FakeRepository(BaseModel):
    """Fake implementation of the Repository class from PyGithub."""
    owner_login: str
    name: str
    full_name: str
    next_pr_number: int = 1
    
    # Non-serialized reference to parent state
    state_ref: Optional[FakeGithubState] = Field(default=None, exclude=True)
    
    @property
    def owner(self) -> Optional["FakeNamedUser"]:
        """Get the owner of this repository."""
        if not self.state_ref:
            return None
        return self.state_ref.users.get(self.owner_login)
    
    @property
    def _pulls(self) -> Dict[int, "FakePullRequest"]:
        """Get PRs for this repository (as a dictionary by number)."""
        if not self.state_ref:
            return {}
        
        result = {}
        for pr in self.state_ref.pull_requests.values():
            if pr.owner_login == self.owner_login and pr.repository_name == self.name:
                result[pr.number] = pr
        
        return result
    
    def get_assignees(self) -> List["FakeNamedUser"]:
        """Get assignable users for repository."""
        # For simplicity, just return some default users
        if not self.state_ref:
            return []
            
        result = []
        for login in ["yang", "testuser"]:
            user = self.state_ref.users.get(login)
            if not user and self.state_ref:
                user = self.state_ref.create_user(login=login)
            if user:
                result.append(user)
                
        return result
    
    def get_pull(self, number: int) -> "FakePullRequest":
        """Get pull request by number."""
        pulls = self._pulls
        if number not in pulls:
            raise ValueError(f"Pull request #{number} not found")
        return pulls[number]
    
    def get_pulls(self, state: str = "open", sort: str = None, 
                 direction: str = None, head: str = None, base: str = None) -> List["FakePullRequest"]:
        """Get pull requests with optional filtering."""
        result = []
        for pr in self._pulls.values():
            if state and pr.state != state:
                continue
            if head and pr.head_ref != head:
                continue
            if base and pr.base_ref != base:
                continue
            result.append(pr)
        return result
    
    def create_pull(self, title: str, body: str, base: str, head: str, 
                   maintainer_can_modify: bool = True, draft: bool = False) -> "FakePullRequest":
        """Create a new pull request."""
        if not self.state_ref:
            raise ValueError("Repository not linked to state")
            
        # Create new PR
        pr_number = self.next_pr_number
        self.next_pr_number += 1
        
        pr = self.state_ref.create_pull_request(
            number=pr_number,
            repo_full_name=self.full_name,
            title=title,
            body=body,
            base_ref=base,
            head_ref=head,
            head_sha="fake-sha",  # Placeholder
            owner_login=self.owner_login,
            repository_name=self.name
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
        return pr


class FakeRequester:
    """Fake implementation of requester for GraphQL."""
    
    def __init__(self, state: FakeGithubState):
        self.state = state
    
    def requestJsonAndCheck(self, method: str, url: str, input: Dict[str, Any] = None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Handle GraphQL requests."""
        if method == "POST" and url == "https://api.github.com/graphql" and input:
            return self._handle_graphql(input)
        
        # Default empty response
        return {}, {}
    
    def _handle_graphql(self, input: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
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
        for pr in self.state.pull_requests.values():
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


class FakeGithub:
    """Fake implementation of the Github class from PyGithub."""
    
    def __init__(self, token: str = None, *args, **kwargs):
        """Initialize with token, but don't actually use it."""
        # Initialize state
        self.token = token
        self.data_dir = os.path.join(os.getcwd(), ".git", "fake_github")
        os.makedirs(self.data_dir, exist_ok=True)
        
        # State file path
        self.state_file = os.path.join(self.data_dir, "fake_github_state.json")
        
        # Load or create state
        self.state = FakeGithubState.load_from_file(self.state_file)
        
        # Create default user if not exists
        if "yang" not in self.state.users:
            self.state.create_user(login="yang")
        
        # Set default user
        self._user = self.state.users["yang"]
    
    def _save_state(self):
        """Save state to file storage."""
        self.state.save_to_file(self.state_file)
    
    def get_user(self) -> "FakeNamedUser":
        """Get current authenticated user."""
        return self._user
    
    def get_repo(self, full_name_or_id: str) -> "FakeRepository":
        """Get repository by full name."""
        repo = self.state.get_repo(full_name_or_id)
        self._save_state()
        return repo
    
    # Add requester for GraphQL - also acts as a proxy
    @property
    def _Github__requester(self):
        """Fake requester for GraphQL."""
        return FakeRequester(self.state)