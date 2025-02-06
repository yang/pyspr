"""Common types used across the codebase."""

from typing import Any, Dict, Optional, Protocol, Type, TypeVar, Union

T = TypeVar('T', bound='StackedPRContextProtocol')

class StackedPRContextProtocol(Protocol):
    """Protocol for what StackedPR expects from a context."""
    obj: Optional[Dict[str, Any]]

# Allow Optional[StackedPRContextProtocol] to be used where StackedPRContextProtocol is expected
StackedPRContextType = Optional[Union[StackedPRContextProtocol, Type[None]]]