"""Common types used across the codebase."""

from typing import Any, Dict, Optional, Protocol, Type, TypeVar, Union, NewType

T = TypeVar('T', bound='StackedPRContextProtocol')

# Create NewTypes for commit identifiers
CommitID = NewType('CommitID', str)
CommitHash = NewType('CommitHash', str)

class StackedPRContextProtocol(Protocol):
    """Protocol for what StackedPR expects from a context."""
    obj: Optional[Dict[str, Any]]

# Allow Optional[StackedPRContextProtocol] to be used where StackedPRContextProtocol is expected
StackedPRContextType = Optional[Union[StackedPRContextProtocol, Type[None]]]