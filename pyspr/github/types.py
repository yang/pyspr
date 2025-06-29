"""Type definitions for GitHub API responses."""

from typing import Dict, List, TypeVar, Protocol, Optional, Tuple, Union
from pydantic import BaseModel

# GraphQL response types with Pydantic models
class PRCommitNode(BaseModel):
    oid: str
    messageHeadline: str
    messageBody: str

class PRCommitData(BaseModel):
    commit: PRCommitNode

class PRCommits(BaseModel):
    nodes: List[PRCommitData]

class PRNode(BaseModel):
    id: str
    number: int
    title: str
    body: str
    baseRefName: str
    headRefName: str
    mergeable: Optional[str] = None
    commits: PRCommits

class PageInfo(BaseModel):
    hasNextPage: bool
    endCursor: Optional[str] = None

class PRNodes(BaseModel):
    nodes: List[PRNode]
    pageInfo: PageInfo

class GraphQLSearch(BaseModel):
    nodes: List[PRNode]
    pageInfo: PageInfo

class GraphQLData(BaseModel):
    search: GraphQLSearch

class GraphQLErrorLocation(BaseModel):
    line: int
    column: int

class GraphQLError(BaseModel):
    message: str
    locations: Optional[List[GraphQLErrorLocation]] = None
    path: Optional[List[Union[str, int]]] = None
    extensions: Optional[Dict[str, object]] = None

class GraphQLResponse(BaseModel):
    data: GraphQLData
    errors: Optional[List[GraphQLError]] = None

# Type for PyGithub GraphQL response
# First element is headers dict, second is the response data
GraphQLResponseType = Tuple[Dict[str, object], Dict[str, object]]

class PRCommitInfo(BaseModel):
    """Type for commit info in a PR."""
    commit_id: str
    commit_hash: str
    commit_headline: str

class PRMapDict(BaseModel):
    """Type for PR dict keyed by commit ID."""
    pr_num: int
    title: str
    body: str
    base_ref: str
    from_branch: str
    commit_id: str
    commit_hash: str
    commit_headline: str
    all_commits: List[PRCommitInfo]

T = TypeVar('T')

def parse_graphql_response(response: Dict[str, object]) -> GraphQLResponse:
    """Parse GraphQL response into Pydantic model."""
    try:
        return GraphQLResponse.model_validate(response)
    except Exception as e:
        raise TypeError(f"Invalid GraphQL response: {e}")

def parse_pr_node(node: Dict[str, object]) -> Optional[PRNode]:
    """Parse a PR node into Pydantic model."""
    try:
        return PRNode.model_validate(node)
    except Exception:
        return None

class PyGithubRequesterInternal(Protocol):
    """Protocol for PyGithub's internal requester object."""
    def requestJsonAndCheck(
        self,
        verb: str,
        url: str, 
        parameters: Optional[Dict[str, object]] = None,
        headers: Optional[Dict[str, str]] = None,
        input: Optional[Dict[str, object]] = None
    ) -> Tuple[Dict[str, object], Dict[str, object]]:
        """PyGithub's internal method returns (headers, data)."""
        ...

class GitHubRequester(Protocol):
    """Type for PyGithub requester to handle GraphQL calls.
    
    This types the internal _Github__requester that's needed for GraphQL.
    We use a Protocol since the requester is a private implementation detail.
    """
    def requestJsonAndCheck(
        self,
        verb: str,
        url: str, 
        parameters: Optional[Dict[str, object]] = None,
        headers: Optional[Dict[str, str]] = None,
        input: Optional[Dict[str, object]] = None
    ) -> GraphQLResponseType:
        ...