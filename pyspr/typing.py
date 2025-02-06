"""Common types used across the codebase."""

from typing import Any, Dict, Optional, Protocol 

class StackedPRContextProtocol(Protocol):
    """Protocol for what StackedPR expects from a context."""
    obj: Optional[Dict[str, Any]]