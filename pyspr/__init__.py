"""
The main pyspr package.
"""
import logging
import sys

# Default format for logs
LOG_FORMAT = '%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s'
LOG_DATE_FORMAT = '%Y-%m-%d %H:%M:%S'

def setup_logging(verbose: int = 0) -> None:
    """Setup logging with appropriate level based on verbosity.
    
    Args:
        verbose: Verbosity level
            0 = INFO and above (default to show git/github calls)
            1 = More verbose INFO
            2 = DEBUG and above
    """
    # Set log level based on verbosity
    if verbose >= 2:
        level = logging.DEBUG
    else:
        # Always show INFO by default for git/github calls
        level = logging.INFO

    # Configure root logger
    logging.basicConfig(
        level=level,
        format=LOG_FORMAT,
        datefmt=LOG_DATE_FORMAT
    )

    # Get the root logger and reconfigure handlers
    logger = logging.getLogger()
    
    # Remove any existing handlers
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        
    # Add handler with our format
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(LOG_FORMAT, LOG_DATE_FORMAT))
    logger.addHandler(handler)

# Initialize with default settings
setup_logging()

# Create a named logger for external modules to use
def get_logger(name: str) -> logging.Logger:
    """Get a logger with the given name."""
    return logging.getLogger(name)