"""Type definitions for GitHub API responses."""

from typing import Dict, List, Any, TypeVar, Protocol, Optional, Tuple, Union
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

class GraphQLViewer(BaseModel):
    login: str
    pullRequests: PRNodes

class GraphQLRepository(BaseModel):
    pullRequests: PRNodes

class GraphQLData(BaseModel):
    viewer: GraphQLViewer
    # repository: GraphQLRepository

class GraphQLErrorLocation(BaseModel):
    line: int
    column: int

class GraphQLError(BaseModel):
    message: str
    locations: Optional[List[GraphQLErrorLocation]] = None
    path: Optional[List[Union[str, int]]] = None
    extensions: Optional[Dict[str, Any]] = None

class GraphQLResponse(BaseModel):
    data: GraphQLData
    errors: Optional[List[GraphQLError]] = None

# Type for PyGithub GraphQL response
GraphQLResponseType = Tuple[Dict[str, Any], Any]

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

def parse_graphql_response(response: Any) -> GraphQLResponse:
    """Parse GraphQL response into Pydantic model."""
    try:
        return GraphQLResponse.model_validate(response)
    except Exception as e:
        raise TypeError(f"Invalid GraphQL response: {e}")

def parse_pr_node(node: Any) -> Optional[PRNode]:
    """Parse a PR node into Pydantic model."""
    try:
        return PRNode.model_validate(node)
    except Exception:
        return None

def parse_pr_nodes(nodes: Any) -> List[PRNode]:
    """Parse nodes to List[PRNode] with validation."""
    valid_nodes: List[PRNode] = []
    if not isinstance(nodes, list):
        return valid_nodes
    nodes_list: List[Any] = nodes
    for node in nodes_list:
        try:
            parsed = parse_pr_node(node)
            if parsed:
                valid_nodes.append(parsed)
        except Exception:
            continue
    return valid_nodes

class GitHubRequester(Protocol):
    """Type for PyGithub requester to handle GraphQL calls.
    
    This types the internal _Github__requester that's needed for GraphQL.
    We use a Protocol since the requester is a private implementation detail.
    """
    def requestJsonAndCheck(
        self,
        verb: str,
        url: str, 
        parameters: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        input: Optional[Any] = None
    ) -> GraphQLResponseType:
        ...