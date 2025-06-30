#!/bin/bash
# Run tests with pytest-xdist for parallel execution
rye run pytest -p xdist -n auto "$@"