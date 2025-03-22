"""Fake PyGithub implementation for testing."""

import os
import json
import uuid
import logging
from typing import Dict, List, Any, Tuple, Optional, Set, Union, cast
from dataclasses import dataclass, field
from pathlib import Path
from pydantic import BaseModel, TypeAdapter, Field

logger = logging.getLogger(__name__)

# Fake PyGithub classes
@dataclass
class FakeNamedUser:
    """Fake implementation of the NamedUser class from PyGithub."""
    login: str
    name: Optional[str] = None
    email: Optional[str] = None
    
    def __post_init__(self):
        if self.name is None:
            self.name = self.login.capitalize()
        if self.email is None:
            self.email = f"{self.login}@example.com"


@dataclass
class FakeRef:
    """Fake implementation of the Ref class from PyGithub."""
    ref: str
    sha: str = "fake-sha"
    repo: Optional['FakeRepository'] = None
    
    # Allow dict-like access for compatibility
    def __getitem__(self, key):
        return getattr(self, key)


@dataclass
class FakeCommit:
    """Fake implementation of the Commit class from PyGithub."""
    sha: str
    message: str
    
    # Nested structure just like PyGithub
    @property
    def commit(self):
        return self


@dataclass
class FakeTeam:
    """Fake implementation of the Team class from PyGithub."""
    name: str
    slug: Optional[str] = None
    
    def __post_init__(self):
        if self.slug is None:
            self.slug = self.name.lower()


# Models for serialization
class RefModel(BaseModel):
    """Model for serializing/deserializing FakeRef."""
    ref: str
    sha: str = "fake-sha"


class PullRequestModel(BaseModel):
    """Model for serializing/deserializing FakePullRequest."""
    number: int
    title: str = ""
    body: str = ""
    state: str = "open"
    merged: bool = False
    user: str = "yang"  # Store just the username
    base_ref: str = "main"
    head_ref: str = ""
    head_sha: str = "fake-sha"
    reviewers: List[str] = Field(default_factory=list)
    labels: List[str] = Field(default_factory=list)
    auto_merge_enabled: bool = False
    auto_merge_method: Optional[str] = None


class RepositoryModel(BaseModel):
    """Model for serializing/deserializing FakeRepository."""
    owner: str
    name: str
    full_name: str
    next_pr_number: int = 1
    pull_requests: Dict[str, PullRequestModel] = Field(default_factory=dict)


class GithubStateModel(BaseModel):
    """Model for the entire GitHub state."""
    repositories: Dict[str, RepositoryModel] = Field(default_factory=dict)


class FakePullRequest:
    """Fake implementation of the PullRequest class from PyGithub."""
    
    def __init__(self, number: int, title: str = "", body: str = "", state: str = "open",
                merged: bool = False, user: FakeNamedUser = None, repository: 'FakeRepository' = None):
        self.number = number
        self.title = title
        self.body = body
        self.state = state
        self.merged = merged
        self.user = user or FakeNamedUser("yang")
        self.repository = repository
        self.base = FakeRef(ref="main")
        self.head = FakeRef(ref=f"pr-{number}-branch")
        self.mergeable = True
        self.mergeable_state = "clean"
        self.auto_merge = None
        self._commits: List[FakeCommit] = []
        self._reviewers: List[FakeNamedUser] = []
        self._review_teams: List[FakeTeam] = []
        self._labels: List[str] = []
    
    def edit(self, title: str = None, body: str = None, state: str = None, 
            base: str = None, maintainer_can_modify: bool = None):
        """Update pull request properties."""
        if title is not None:
            self.title = title
        if body is not None:
            self.body = body
        if state is not None:
            self.state = state
        if base is not None and self.base is not None:
            self.base.ref = base
    
    def create_issue_comment(self, body: str):
        """Add a comment to the pull request."""
        # We don't need to store comments for testing
        logger.info(f"PR #{self.number} comment: {body}")
    
    def add_to_labels(self, *labels):
        """Add labels to the pull request."""
        for label in labels:
            self._labels.append(str(label))
        logger.info(f"PR #{self.number} labels: {labels}")
    
    def get_commits(self):
        """Get commits in the pull request."""
        return self._commits
    
    def get_review_requests(self) -> Tuple[List[FakeNamedUser], List[FakeTeam]]:
        """Get users and teams requested for review."""
        return (self._reviewers, self._review_teams)
    
    def create_review_request(self, reviewers=None, team_reviewers=None):
        """Request reviews from users or teams."""
        if reviewers:
            for reviewer in reviewers:
                if isinstance(reviewer, str):
                    # Convert string to FakeNamedUser
                    self._reviewers.append(FakeNamedUser(reviewer))
                else:
                    self._reviewers.append(reviewer)
        
        if team_reviewers:
            for team in team_reviewers:
                if isinstance(team, str):
                    self._review_teams.append(FakeTeam(team))
                else:
                    self._review_teams.append(team)
    
    def merge(self, commit_title: str = None, commit_message: str = None, 
             sha: str = None, merge_method: str = "merge"):
        """Merge the pull request."""
        self.merged = True
        self.state = "closed"
    
    def enable_automerge(self, merge_method: str = "merge"):
        """Enable auto-merge for the pull request."""
        self.auto_merge = {"enabled": True, "method": merge_method}
        logger.info(f"PR #{self.number} auto-merge enabled with method: {merge_method}")
    
    def to_model(self) -> PullRequestModel:
        """Convert to serializable model."""
        return PullRequestModel(
            number=self.number,
            title=self.title,
            body=self.body,
            state=self.state,
            merged=self.merged,
            user=self.user.login,
            base_ref=self.base.ref if self.base else "main",
            head_ref=self.head.ref if self.head else f"pr-{self.number}-branch",
            head_sha=self.head.sha if self.head else "fake-sha",
            reviewers=[reviewer.login for reviewer in self._reviewers],
            labels=self._labels,
            auto_merge_enabled=self.auto_merge is not None,
            auto_merge_method=self.auto_merge["method"] if self.auto_merge else None
        )
    
    @classmethod
    def from_model(cls, model: PullRequestModel, repository: 'FakeRepository') -> 'FakePullRequest':
        """Create from model."""
        pr = cls(
            number=model.number,
            title=model.title,
            body=model.body,
            state=model.state,
            merged=model.merged,
            user=FakeNamedUser(model.user),
            repository=repository
        )
        
        # Set base and head
        pr.base = FakeRef(ref=model.base_ref, repo=repository)
        pr.head = FakeRef(ref=model.head_ref, sha=model.head_sha, repo=repository)
        
        # Set reviewers
        for reviewer in model.reviewers:
            pr._reviewers.append(FakeNamedUser(reviewer))
        
        # Set labels
        pr._labels = model.labels
        
        # Set auto merge
        if model.auto_merge_enabled and model.auto_merge_method:
            pr.auto_merge = {"enabled": True, "method": model.auto_merge_method}
        
        return pr


class FakeRepository:
    """Fake implementation of the Repository class from PyGithub."""
    
    def __init__(self, owner: FakeNamedUser, name: str, full_name: str = None):
        self.owner = owner
        self.name = name
        self.full_name = full_name or f"{owner.login}/{name}"
        self._pulls: Dict[int, FakePullRequest] = {}
        self._next_pr_number = 1
        self._assignees = [
            FakeNamedUser("yang"),
            FakeNamedUser("testluser")
        ]
    
    def get_pull(self, number: int) -> FakePullRequest:
        """Get pull request by number."""
        if number not in self._pulls:
            raise ValueError(f"Pull request #{number} not found")
        return self._pulls[number]
    
    def get_pulls(self, state: str = "open", sort: str = None, 
                 direction: str = None, head: str = None, base: str = None) -> List[FakePullRequest]:
        """Get pull requests with optional filtering."""
        result = []
        for pr in self._pulls.values():
            if state and pr.state != state:
                continue
            if head and pr.head.ref != head:
                continue
            if base and pr.base.ref != base:
                continue
            result.append(pr)
        return result
    
    def create_pull(self, title: str, body: str, base: str, head: str, 
                   maintainer_can_modify: bool = True, draft: bool = False) -> FakePullRequest:
        """Create a new pull request."""
        pr = FakePullRequest(
            number=self._next_pr_number,
            title=title,
            body=body,
            state="open",
            merged=False,
            user=FakeNamedUser("yang"),
            repository=self
        )
        pr.base = FakeRef(ref=base, repo=self)
        pr.head = FakeRef(ref=head, repo=self)
        
        self._pulls[pr.number] = pr
        self._next_pr_number += 1
        
        return pr
    
    def get_assignees(self) -> List[FakeNamedUser]:
        """Get assignable users for repository."""
        return self._assignees
    
    def to_model(self) -> RepositoryModel:
        """Convert to serializable model."""
        pulls = {}
        for pr_num, pr in self._pulls.items():
            pulls[str(pr_num)] = pr.to_model()
        
        return RepositoryModel(
            owner=self.owner.login,
            name=self.name,
            full_name=self.full_name,
            next_pr_number=self._next_pr_number,
            pull_requests=pulls
        )
    
    @classmethod
    def from_model(cls, model: RepositoryModel) -> 'FakeRepository':
        """Create from model."""
        owner = FakeNamedUser(model.owner)
        repo = cls(owner=owner, name=model.name, full_name=model.full_name)
        repo._next_pr_number = model.next_pr_number
        
        # Create pull requests
        for pr_num_str, pr_model in model.pull_requests.items():
            pr_num = int(pr_num_str)
            repo._pulls[pr_num] = FakePullRequest.from_model(pr_model, repo)
        
        return repo


class FakeRequester:
    """Fake implementation of requester for GraphQL."""
    
    def __init__(self, github: 'FakeGithub'):
        self.github = github
    
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
        
        # Get all repos and their PRs
        for repo_name, repo in self.github._repos.items():
            for pr_num, pr in repo._pulls.items():
                if pr.state == "open":
                    # Build PR node for response
                    pr_node = {
                        "id": f"pr_{pr_num}",
                        "number": pr.number,
                        "title": pr.title,
                        "body": pr.body,
                        "baseRefName": pr.base.ref if pr.base else "main",
                        "headRefName": pr.head.ref if pr.head else f"pr-{pr_num}-branch",
                        "mergeable": "MERGEABLE",
                        "reviewDecision": None,
                        "repository": {
                            "id": f"repo_{repo_name}"
                        },
                        "commits": {
                            "nodes": [
                                {
                                    "commit": {
                                        "oid": pr.head.sha if pr.head else "fake-sha",
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
        response["data"]["viewer"]["pullRequests"]["nodes"] = pr_nodes
        
        # Return tuple of (headers, data)
        return {}, response


class FakeGithub:
    """Fake implementation of the Github class from PyGithub."""
    
    def __init__(self, token: str = None, *args, **kwargs):
        """Initialize with token, but don't actually use it."""
        self.token = token
        self._user = FakeNamedUser("yang")  # Default user
        self._repos: Dict[str, FakeRepository] = {}
        
        # State storage for PRs, etc.
        self.data_dir = os.path.join(os.getcwd(), ".git", "fake_github")
        os.makedirs(self.data_dir, exist_ok=True)
        
        # Load state if it exists
        self._load_state()
    
    def _load_state(self):
        """Load state from file storage using Pydantic."""
        state_file = os.path.join(self.data_dir, "fake_github_state.json")
        if os.path.exists(state_file):
            try:
                with open(state_file, "r") as f:
                    state_json = f.read()
                
                # Parse using Pydantic
                state_adapter = TypeAdapter(GithubStateModel)
                state = state_adapter.validate_json(state_json)
                
                # Create repositories
                for repo_name, repo_model in state.repositories.items():
                    self._repos[repo_name] = FakeRepository.from_model(repo_model)
                
                logger.info(f"Loaded fake GitHub state with {len(self._repos)} repositories")
            except Exception as e:
                logger.error(f"Error loading fake GitHub state: {e}")
                # Create empty state
                self._repos = {}
    
    def _save_state(self):
        """Save state to file storage using Pydantic."""
        state_file = os.path.join(self.data_dir, "fake_github_state.json")
        
        # Build state using models
        repos = {}
        for repo_name, repo in self._repos.items():
            repos[repo_name] = repo.to_model()
        
        state = GithubStateModel(repositories=repos)
        
        try:
            # Convert to JSON with Pydantic
            state_adapter = TypeAdapter(GithubStateModel)
            state_json = state_adapter.dump_json(state, indent=2)
            
            with open(state_file, "w") as f:
                f.write(state_json.decode('utf-8'))
        except Exception as e:
            logger.error(f"Error saving fake GitHub state: {e}")
    
    def get_user(self) -> FakeNamedUser:
        """Get current authenticated user."""
        return self._user
    
    def get_repo(self, full_name_or_id: str) -> FakeRepository:
        """Get repository by full name."""
        if full_name_or_id not in self._repos:
            # Create new repository if it doesn't exist
            owner_name, repo_name = full_name_or_id.split("/")
            self._repos[full_name_or_id] = FakeRepository(
                owner=FakeNamedUser(owner_name),
                name=repo_name,
                full_name=full_name_or_id
            )
            logger.info(f"Created repository: {full_name_or_id}")
            self._save_state()
        
        return self._repos[full_name_or_id]
    
    # Add requester for GraphQL - also acts as a proxy
    @property
    def _Github__requester(self):
        """Fake requester for GraphQL."""
        return FakeRequester(self)