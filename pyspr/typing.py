"""Common types used across the codebase."""

from typing import Dict, List, Optional, Protocol, Type, TypeVar, Union, NewType, Literal
from dataclasses import dataclass


class DuplicateCommitIDError(Exception):
    """Raised when multiple commits in the stack have the same commit-id."""

    def __init__(self, duplicates: Dict[str, List[str]]):
        """Initialize with a dict mapping commit-id to list of commit hashes that share it."""
        self.duplicates = duplicates
        # Build a descriptive message
        messages: List[str] = []
        for commit_id, hashes in duplicates.items():
            hash_list = ", ".join(h[:8] for h in hashes)
            messages.append(f"  commit-id:{commit_id} appears in commits: {hash_list}")
        super().__init__(
            f"Duplicate commit-id(s) detected in stack:\n" + "\n".join(messages) +
            "\n\nEach commit must have a unique commit-id. "
            "This can happen if commits were copied (e.g., via cherry-pick) "
            "without amending to get new commit-ids."
        )

# Shared TypeVars
T = TypeVar('T')
# No config type variables needed
GitT = TypeVar('GitT', bound='GitInterface')
ContextT = TypeVar('ContextT', bound='StackedPRContextProtocol')

# Literal types for string constants
MergeMethod = Literal['merge', 'squash', 'rebase']
LogLevel = Literal['DEBUG', 'INFO', 'WARNING', 'ERROR']
PRState = Literal['open', 'closed']
GitRemote = Literal['origin']  # Add more as needed
GitBranch = Literal['main', 'master']  # Add more as needed

# Create NewTypes for commit identifiers
CommitID = NewType('CommitID', str)
CommitHash = NewType('CommitHash', str)

@dataclass 
class Commit:
    """Git commit info.
    CommitID persists across amends, CommitHash changes with each amend."""
    commit_id: CommitID  # Persists across amends
    commit_hash: CommitHash  # Changes with each amend
    subject: str
    body: str = ""
    wip: bool = False

    @classmethod
    def from_strings(cls, commit_id: str, commit_hash: str, subject: str, body: str = "", wip: bool = False) -> 'Commit':
        """Create a Commit from string IDs. Use this factory method for easier creation."""
        return cls(CommitID(commit_id), CommitHash(commit_hash), subject, body, wip)

class StackedPRContextProtocol(Protocol):
    """Protocol for what StackedPR expects from a context."""
    obj: Dict[str, object]  # Click context object storage

# Allow Optional[StackedPRContextProtocol] to be used where StackedPRContextProtocol is expected
StackedPRContextType = Optional[Union[StackedPRContextProtocol, Type[None]]]

# Common Protocol definitions - move from their individual modules

# We're using PysprConfig directly instead of a protocol

class GitInterface(Protocol):
    """Git interface."""
    def run_cmd(self, command: str, output: Optional[str] = None) -> str:
        """Run git command and optionally capture output."""
        ...

    def must_git(self, command: str, output: Optional[str] = None) -> str:
        """Run git command, failing on error."""
        ...