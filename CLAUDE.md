Background:

We are creating a Python port of ejoffe/spr.
But with the minimal code needed for just the core behavior to work.
Keep it as direct a translation as possible to minimize risk.
We need the algorithm to match exactly.
We use rye, so e.g. `rye run pyspr update -v`.
Test against yang/teststack for normal merges and yangenttest1/teststack for merge queue (always do SSH clone for pushes to
not hang! And use a subdir of the current project so you still can access rye run pyspr.).
GH API token is in ~/.config/gh/hosts.yml.
The most important debugging technique is to add temp debug logging.
`gh` is installed if useful.
Always run pytest with -vsx to see output. We use rye so do rye run.
Review the spr readme to understand expected behavior. As well as our current e2e tests.
