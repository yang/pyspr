"""Common types used across the codebase."""

from typing import Any, Dict, List, Optional, Protocol, Union
from click import Context

class StackedPRContextProtocol(Protocol):
    """Protocol for what StackedPR expects from a context."""
    obj: Optional[Dict[str, Any]]