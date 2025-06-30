#!/bin/bash
# Test script to verify breakup --stacks handles both transitions:
# 1. Independent → Dependent (the fix we just made)
# 2. Dependent → Independent (also important to test)

set -e  # Exit on error

echo "=== Testing breakup --stacks bidirectional transitions ==="
echo ""

# Set up test directory
TEST_DIR="/tmp/pyspr_breakup_transitions_test"
rm -rf "$TEST_DIR"
mkdir -p "$TEST_DIR"
cd "$TEST_DIR"

# Clone the teststack repo
echo "Setting up test repository..."
git clone git@github.com:yang/teststack.git
cd teststack

# Create a test branch
TEST_BRANCH="test-transitions-$(date +%s)"
git checkout -b "$TEST_BRANCH"

# Configure git
git config user.name "Test User"
git config user.email "test@example.com"

echo ""
echo "=== PART 1: Test Independent → Dependent transition ==="
echo ""
echo "Step 1: Create 2 independent commits"
echo "content of A" > file_a_trans.txt
git add file_a_trans.txt
git commit -m "Add file A [trans-test]"

echo "content of B" > file_b_trans.txt
git add file_b_trans.txt
git commit -m "Add file B [trans-test]"

echo ""
echo "Step 2: Run breakup --stacks (should create 2 independent PRs)"
cd /Users/yang/code/pyspr && rye run pyspr breakup --stacks -v -C "$TEST_DIR/teststack" | grep -E "(Created PR|Component|single-commit)"

echo ""
echo "Step 3: Make B depend on A"
cd "$TEST_DIR/teststack"

# Get commit-id for B to preserve it
COMMIT_B_ID=$(git show -s --format=%B HEAD | grep -o 'commit-id:[a-f0-9]\{8\}' | cut -d: -f2)

# Modify file A in commit B
echo "modified by B" >> file_a_trans.txt
git add file_a_trans.txt
git commit --amend -m "Add file B (now depends on A) [trans-test]

commit-id:$COMMIT_B_ID"

echo ""
echo "Step 4: Run breakup --stacks again (should UPDATE existing PRs to be stacked)"
cd /Users/yang/code/pyspr && rye run pyspr breakup --stacks -v -C "$TEST_DIR/teststack" | grep -E "(Found existing PR|Updating PR|multi-commit|Found all|stack)"

# Save the current state
cd "$TEST_DIR/teststack"
STACKED_HASH_A=$(git rev-parse HEAD~1)
STACKED_HASH_B=$(git rev-parse HEAD)

echo ""
echo "=== PART 2: Test Dependent → Independent transition ==="
echo ""
echo "Step 5: Make commits independent again by removing B's dependency on A"

# Reset to commit A
git reset --hard HEAD~1

# Get commit-id for A
COMMIT_A_ID=$(git show -s --format=%B HEAD | grep -o 'commit-id:[a-f0-9]\{8\}' | cut -d: -f2)

# Cherry-pick B but without the file_a modification
git cherry-pick $STACKED_HASH_B --no-commit

# Reset file_a to remove the modification
git reset HEAD file_a_trans.txt
git checkout file_a_trans.txt

# Commit with preserved commit-id
git commit -m "Add file B (independent again) [trans-test]

commit-id:$COMMIT_B_ID"

echo ""
echo "Step 6: Run breakup --stacks again (should UPDATE PRs to be independent)"
cd /Users/yang/code/pyspr && rye run pyspr breakup --stacks -v -C "$TEST_DIR/teststack" | grep -E "(Found existing PR|Component|single-commit|Updating PR)"

echo ""
echo "=== Results Summary ==="
echo ""
echo "Check the PRs at https://github.com/yang/teststack/pulls"
echo ""
echo "Expected behavior:"
echo "1. After first transition (independent → dependent):"
echo "   - Same 2 PRs exist (not recreated)"
echo "   - BOTH PRs show 'Stack:' section"
echo "   - Bottom PR (A) has stack info (main fix)"
echo "   - Top PR (B) has base branch pointing to PR A"
echo ""
echo "2. After second transition (dependent → independent):"
echo "   - Same 2 PRs still exist"
echo "   - Stack info should be REMOVED from both PRs"
echo "   - Both PRs should target 'main' branch again"
echo ""
echo "Current PRs:"
cd "$TEST_DIR/teststack"
gh pr list --limit 5 | grep "trans-test" || echo "No PRs found with trans-test tag"

echo ""
echo "=== Cleanup ==="
echo "To clean up:"
echo "  1. Close the PRs: gh pr close <number>"
echo "  2. Delete the test branch: git push origin --delete $TEST_BRANCH"
echo "  3. Remove test directory: rm -rf $TEST_DIR"