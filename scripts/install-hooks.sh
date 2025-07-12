#!/bin/bash

# Install git hooks

echo "Installing git hooks..."

# Install pre-commit hooks
rye run pre-commit install

# Install pre-push hook
cp scripts/pre-push .git/hooks/pre-push
chmod +x .git/hooks/pre-push

echo "✅ Git hooks installed successfully!"
echo ""
echo "Hooks installed:"
echo "  - pre-commit: ruff (linting) + pyright (type checking)"
echo "  - pre-push: test runner (with skip option)"