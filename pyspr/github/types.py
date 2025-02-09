"""Type definitions for GitHub API responses."""

from typing import Dict, List, TypedDict, Any, Union, cast, TypeVar, Mapping, Sequence
from typing_extensions import NotRequired, Required

# GraphQL response types with complete type information
class PRCommitNode(TypedDict, total=False):
    oid: Required[str]
    messageHeadline: Required[str]
    messageBody: Required[str]

class PRCommitData(TypedDict, total=False):
    commit: Required[PRCommitNode]

class PRCommits(TypedDict, total=False):
    nodes: Required[List[PRCommitData]]

class PRNode(TypedDict, total=False):
    id: Required[str]
    number: Required[int]
    title: Required[str]
    body: Required[str]
    baseRefName: Required[str]
    headRefName: Required[str]
    mergeable: NotRequired[str]
    commits: Required[PRCommits]

class PageInfo(TypedDict, total=False):
    hasNextPage: Required[bool]
    endCursor: NotRequired[str]

class PRNodes(TypedDict, total=False):
    nodes: Required[List[PRNode]]
    pageInfo: Required[PageInfo]

class GraphQLViewer(TypedDict, total=False):
    login: Required[str]

class GraphQLRepository(TypedDict, total=False):
    pullRequests: Required[PRNodes]

class GraphQLData(TypedDict, total=False):
    viewer: Required[GraphQLViewer]
    repository: Required[GraphQLRepository]

class GraphQLResponse(TypedDict, total=False):
    data: Required[GraphQLData]
    errors: NotRequired[List[Dict[str, Any]]]

# Type for tuple response from PyGithub
GraphQLTupleResponse = "tuple[int, Dict[str, Any]]"

# Type alias for the combined response types
GraphQLResponseType = Union["tuple[int, Dict[str, Any]]", Dict[str, Any]]

class PRMapDict(TypedDict):
    """Type for PR dict keyed by commit ID."""
    pr_num: int 
    title: str
    body: str
    base_ref: str
    from_branch: str
    commit_id: str
    commit_hash: str
    commit_headline: str
    all_commits: List['PRCommitInfo']

class PRCommitInfo(TypedDict):
    """Type for commit info in a PR."""
    commit_id: str
    commit_hash: str
    commit_headline: str

T = TypeVar('T')

# Type guard helpers
def is_tuple_response(resp: GraphQLResponseType) -> bool:
    """Check if response is a tuple response."""
    return isinstance(resp, tuple) and len(resp) > 1

def is_dict_with_keys(obj: Any, *keys: str) -> bool:
    """Check if object is a dict with all specified keys."""
    return isinstance(obj, Mapping) and all(key in obj for key in keys)

def is_pr_node(node: Any) -> bool:
    """Check if node is a valid PR node."""
    return is_dict_with_keys(node, 'id', 'number', 'headRefName') and \
           isinstance(node.get('id'), str) and \
           isinstance(node.get('number'), int) and \
           isinstance(node.get('headRefName'), str)

def cast_pr_node(node: Any) -> PRNode:
    """Cast a node to PRNode after validation."""
    if not is_pr_node(node):
        raise TypeError("Not a valid PRNode")
    return cast(PRNode, node)

def cast_pr_nodes(nodes: Any) -> List[PRNode]:
    """Cast nodes to List[PRNode] after validation."""
    if not isinstance(nodes, Sequence):
        raise TypeError("Not a valid list of PRNodes")
    cast_nodes: List[PRNode] = []
    nodes_seq: Sequence[Any] = nodes  # Explicitly type the sequence
    try:
        nodes_list: List[Any] = list(nodes_seq)
        for item in nodes_list:
            try:
                node = safe_cast(item, dict)
                if is_pr_node(node):
                    cast_nodes.append(cast_pr_node(node))
            except (TypeError, ValueError):
                continue
    except Exception:
        pass  # Failed to convert to list
    return cast_nodes

def safe_cast(obj: Any, expected_type: type) -> Any:
    """Safely cast an object to expected type or raise TypeError."""
    if not isinstance(obj, expected_type):
        raise TypeError(f"Expected {expected_type.__name__}, got {type(obj).__name__}")
    return obj  # No need to cast since we've verified the type