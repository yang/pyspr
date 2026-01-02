#!/bin/bash
# Setup script for Claude Code web sessions
# This runs automatically via SessionStart hook when a web session begins

# Only run in Claude Code web environments
if [ "$CLAUDE_CODE_REMOTE" != "true" ]; then
  exit 0
fi

echo "Setting up pyspr development environment..."

# Install rye if not present
if ! command -v rye &> /dev/null; then
  echo "Installing rye..."
  curl -sSf https://rye.astral.sh/get | RYE_INSTALL_OPTION="--yes" bash
fi

# Source rye environment
source "$HOME/.rye/env"

# Sync dependencies
echo "Installing dependencies with rye sync..."
rye sync

# Install pre-commit hooks
echo "Installing pre-commit hooks..."
rye run pre-commit install

# Persist rye in PATH for the rest of the session
if [ -n "$CLAUDE_ENV_FILE" ]; then
  echo "export PATH=\"$HOME/.rye/shims:\$PATH\"" >> "$CLAUDE_ENV_FILE"
fi

echo "Setup complete!"
exit 0
