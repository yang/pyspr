"""Pretty formatting utilities for CLI output."""

import json
import shutil
import sys
from typing import IO, Optional

def get_term_width() -> int:
    """Get terminal width, default to 80 if can't detect."""
    try:
        return shutil.get_terminal_size().columns
    except Exception:
        return 80


def header(text: str, use_emoji: bool = True) -> str:
    """Create a header with optional emoji."""
    width = get_term_width()
    
    # In Go version, these are configurable - hardcoding for now
    h_line = "─" * (width - 2)  
    v_line = "│"
    emoji = "🎯 " if use_emoji else ""
    
    result = [
        f"┌{h_line}┐",
        f"{v_line}{' ' * (width - 2)}{v_line}",
        f"{v_line} {emoji}{text}{' ' * (width - len(text) - len(emoji) - 3)}{v_line}",
        f"{v_line}{' ' * (width - 2)}{v_line}",
        f"└{h_line}┘"
    ]
    
    return "\n".join(result)


def pretty_json(data: object, prefix: str = "") -> str:
    """Format JSON data with optional prefix."""
    raw = json.dumps(data, indent=2)
    if prefix:
        lines = raw.split("\n")
        return "\n".join(f"{prefix}{line}" for line in lines)
    return raw


def print_json(data: object, prefix: str = "", file: Optional[IO[str]] = None) -> None:
    """Print JSON data to file (default stdout)."""
    if file is None:
        file = sys.stdout
    print(pretty_json(data, prefix), file=file)


def print_header(text: str, use_emoji: bool = True, file: Optional[IO[str]] = None) -> None:
    """Print a header to file (default stdout)."""
    if file is None:
        file = sys.stdout
    print(header(text, use_emoji), file=file)