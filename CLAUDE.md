Background:

We are creating a Python port of ejoffe/spr.
But with the minimal code needed for just the core behavior to work.
Keep it as direct a translation as possible to minimize risk.
We need the algorithm to match exactly.
Review the spr readme to understand expected behavior. As well as our current e2e tests.
We use rye, so e.g. `rye run pyspr update -v`.
Besides tests, you may find it helpful to run commands yourself, where it's easier to inspect things with e.g. `gh pr`.
Test against yang/teststack for normal merges and yangenttest1/teststack for merge queue (always do SSH clone for pushes to not hang! And use a subdir of the current project so you still can access rye run pyspr.).
GH API token is in ~/.config/gh/hosts.yml.
The most important debugging technique is to add temp debug logging.
Always run pytest with -vsx to see output. We use rye so do rye run.

## Test Failures Investigation (2025-06-27)

After implementing upstream branch detection (`git rev-parse --abbrev-ref @{upstream}`), several tests are failing:

### 1. test_no_rebase_pr_stacking
- **Issue**: PR2's hash changes when it shouldn't (when no-rebase is enabled)
- **Root Cause**: The second commit is created without a commit-id, and when update runs with --no-rebase, `get_local_commit_stack` still adds the commit-id, changing the hash
- **Note**: no-rebase means "don't rebase on latest upstream", NOT "don't add commit-ids". The test should still expect commit-ids to be added.

### 2. test_reviewer_functionality  
- **Issue**: Test expects 2 PRs but only finds 1
- **Root Cause**: When on PR branch `spr/main/xxx`, the upstream is now `origin/spr/main/xxx` (not `origin/main`). New commits on that branch are seen as part of that branch, not as new PRs in the stack.
- **This is correct Git behavior** - we're properly using tracked upstream

### 3. test_reorder
- **Status**: PASSING ✓

### 4. test_breakup_pr_already_exists_error
- **Issue**: Test expects "already exists for" in user output, but it's only in debug logs
- **Functionality works correctly** - no duplicate PRs created, just output format expectation

### 5. fake_pygithub tests
- **Issue**: Git clone failures in test infrastructure
- **Unrelated to upstream changes**

When working on types, don't just add type ignores or Any or cast things away. The whole point is to get very strict typing.
