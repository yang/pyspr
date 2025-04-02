from typing import Optional

from pyspr.github import T


def ensure(value: Optional[T]) -> T:
    """Ensure a value is not None, raising RuntimeError if it is.

    Args:
        value: The value to check

    Returns:
        The value if it is not None

    Raises:
        RuntimeError: If the value is None
    """
    if value is None:
        raise RuntimeError("Value is None")
    return value
