"""Adapter classes to wrap PyGithub objects with our protocol interfaces."""

from typing import List, Optional, Tuple, Dict, Union
import logging

from github import Github
from github.Repository import Repository  
from github.PullRequest import PullRequest as PyGithubPullRequest
from github.NamedUser import NamedUser
from github.AuthenticatedUser import AuthenticatedUser
from github.GithubObject import NotSet

from . import (
    PyGithubProtocol, 
    GitHubRepoProtocol, 
    GitHubPullRequestProtocol,
    GitHubUserProtocol,
    GitHubRequester,
    GraphQLResponseType,
    GitHubRefProtocol,
    GitHubCommitProtocol
)
from .types import PyGithubRequesterInternal

logger = logging.getLogger(__name__)


class PyGithubUserAdapter(GitHubUserProtocol):
    """Adapter for PyGithub NamedUser or AuthenticatedUser objects."""
    
    def __init__(self, user: Union[NamedUser, AuthenticatedUser]) -> None:
        self._user = user
    
    @property
    def login(self) -> str:
        """Get the user's login name."""
        return self._user.login


class PyGithubPullRequestAdapter(GitHubPullRequestProtocol):
    """Adapter for PyGithub PullRequest objects."""
    
    def __init__(self, pr: PyGithubPullRequest) -> None:
        self._pr = pr
    
    @property
    def number(self) -> int:
        return self._pr.number
    
    @property
    def title(self) -> str:
        return self._pr.title
    
    @property
    def body(self) -> str:
        return self._pr.body
    
    @property
    def state(self) -> str:
        return self._pr.state
    
    @property
    def base(self) -> GitHubRefProtocol:
        return self._pr.base
    
    @property
    def head(self) -> GitHubRefProtocol:
        return self._pr.head
    
    @property
    def user(self) -> GitHubUserProtocol:
        return PyGithubUserAdapter(self._pr.user)
    
    @property
    def mergeable(self) -> Optional[bool]:
        return self._pr.mergeable
    
    @property
    def mergeable_state(self) -> str:
        return self._pr.mergeable_state
        
    @property
    def merged(self) -> bool:
        return self._pr.merged
    
    def edit(self, title: Optional[str] = None, body: Optional[str] = None, 
             state: Optional[str] = None, base: Optional[str] = None, **kwargs: object) -> None:
        """Edit the pull request."""
        # Convert None to NotSet for PyGithub
        self._pr.edit(
            title=title if title is not None else NotSet,
            body=body if body is not None else NotSet,
            state=state if state is not None else NotSet,
            base=base if base is not None else NotSet
        )
    
    def create_issue_comment(self, body: str) -> None:
        """Add a comment to the pull request."""
        self._pr.create_issue_comment(body)
    
    def add_to_labels(self, *labels: str) -> None:
        """Add labels to the pull request."""
        self._pr.add_to_labels(*labels)
    
    def get_commits(self) -> List[GitHubCommitProtocol]:
        """Get commits in the pull request."""
        return list(self._pr.get_commits())
    
    def get_review_requests(self) -> Tuple[List[object], List[object]]:
        """Get users and teams requested for review."""
        users, teams = self._pr.get_review_requests()
        # Convert PaginatedList to List
        return (list(users), list(teams))
    
    def merge(self, commit_title: str = "", commit_message: str = "", 
             sha: str = "", merge_method: str = "merge") -> None:
        """Merge the pull request."""
        # PyGithub expects NotSet for empty strings
        self._pr.merge(
            commit_title=commit_title if commit_title else NotSet,
            commit_message=commit_message if commit_message else NotSet,
            sha=sha if sha else NotSet,
            merge_method=merge_method
        )
    
    def enable_automerge(self, merge_method: str = "merge") -> None:
        """Enable auto-merge for the pull request."""
        self._pr.enable_automerge(merge_method=merge_method)

    def create_review_request(self, reviewers: List[str]) -> None:
        """Create review request with reviewers."""
        # PyGithub's method name is different and uses different parameter names
        self._pr.create_review_request(reviewers=reviewers)


class PyGithubRepoAdapter(GitHubRepoProtocol):
    """Adapter for PyGithub Repository objects."""
    
    def __init__(self, repo: Repository) -> None:
        self._repo = repo
    
    @property
    def owner(self) -> GitHubUserProtocol:
        """Get the owner of this repository."""
        return PyGithubUserAdapter(self._repo.owner)
    
    def get_pull(self, number: int) -> GitHubPullRequestProtocol:
        """Get a pull request by number."""
        return PyGithubPullRequestAdapter(self._repo.get_pull(number))
    
    def get_pulls(self, state: str = "open", sort: str = "", 
                 direction: str = "", head: str = "", base: str = "") -> List[GitHubPullRequestProtocol]:
        """Get pull requests with optional filtering."""
        # Convert empty strings to NotSet for PyGithub
        pulls = self._repo.get_pulls(
            state=state,
            sort=sort if sort else NotSet,
            direction=direction if direction else NotSet,
            head=head if head else NotSet,
            base=base if base else NotSet
        )
        return [PyGithubPullRequestAdapter(pr) for pr in pulls]
    
    def create_pull(self, title: str, body: str, base: str, head: str, 
                   maintainer_can_modify: bool = True, draft: bool = False) -> GitHubPullRequestProtocol:
        """Create a new pull request."""
        pr = self._repo.create_pull(
            title=title,
            body=body,
            base=base,
            head=head,
            maintainer_can_modify=maintainer_can_modify,
            draft=draft
        )
        return PyGithubPullRequestAdapter(pr)
    
    def get_assignees(self) -> List[GitHubUserProtocol]:
        """Get assignable users for repository."""
        return [PyGithubUserAdapter(user) for user in self._repo.get_assignees()]


class PyGithubRequesterAdapter(GitHubRequester):
    """Adapter for PyGithub's requester to handle GraphQL."""
    
    def __init__(self, requester: PyGithubRequesterInternal) -> None:
        self._requester = requester
    
    def requestJsonAndCheck(
        self, verb: str, url: str, parameters: Optional[Dict[str, object]] = None,
        headers: Optional[Dict[str, str]] = None, input: Optional[Dict[str, object]] = None
    ) -> GraphQLResponseType:
        """Make a request and return the response."""
        # PyGithub's requestJsonAndCheck returns (status, headers, data)
        # We need to return (headers, data) to match our protocol
        _status, response_headers, data = self._requester.requestJsonAndCheck(
            verb, url, parameters=parameters, headers=headers, input=input
        )
        # Ensure headers is never None
        return (response_headers or {}, data)


class PyGithubAdapter(PyGithubProtocol):
    """Adapter for the main PyGithub object."""
    
    def __init__(self, github: Github) -> None:
        self._github = github
        # Cache the requester adapter
        self._requester_adapter: Optional[PyGithubRequesterAdapter] = None
    
    def get_repo(self, full_name_or_id: str) -> GitHubRepoProtocol:
        """Get a repository by full name."""
        # We only use string names, not IDs
        return PyGithubRepoAdapter(self._github.get_repo(full_name_or_id))
    
    def get_user(self, login: Optional[str] = None, **kwargs: Dict[str, object]) -> Optional[GitHubUserProtocol]:
        """Get a user by login or the authenticated user if login is None."""
        # PyGithub uses NotSet instead of None
        if login is None:
            user = self._github.get_user()
        else:
            user = self._github.get_user(login)
        
        # PyGithub returns AuthenticatedUser or NamedUser, never None
        # But our protocol expects Optional
        return PyGithubUserAdapter(user) if user else None
    
    @property
    def _Github__requester(self) -> GitHubRequester:
        """Access the requester for GraphQL calls."""
        if self._requester_adapter is None:
            # Access the private attribute from the real PyGithub object
            # Use getattr to avoid type checker issues with private attributes
            real_requester = getattr(self._github, '_Github__requester')
            self._requester_adapter = PyGithubRequesterAdapter(real_requester)
        return self._requester_adapter