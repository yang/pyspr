"""
The main pyspr package.
"""
import logging
import sys

# Configure root logger
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# Get the root logger
logger = logging.getLogger()
# Remove any existing handlers
for handler in logger.handlers[:]:
    logger.removeHandler(handler)
# Add handler with our format
handler = logging.StreamHandler(sys.stderr)
handler.setFormatter(logging.Formatter(
    '%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s',
    '%Y-%m-%d %H:%M:%S'
))
logger.addHandler(handler)

# Create a named logger for external modules to use
def get_logger(name: str) -> logging.Logger:
    """Get a logger with the given name."""
    return logging.getLogger(name)