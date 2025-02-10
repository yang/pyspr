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
