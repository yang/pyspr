#!/bin/bash

# Run tests with a message about expected duration
echo "Running tests before push..."
echo "Note: Full test suite takes approximately 7-10 minutes to complete."
echo ""

# Run tests
./run_tests.sh

# Return the exit code
exit $?