<!-- @format -->

Background:

We use rye, so e.g. `rye run pyspr update -v`.
Besides tests, you may find it helpful to run commands yourself, where it's easier to inspect things with e.g. `gh pr`.
Test against yang/teststack for normal merges and yangenttest1/teststack for merge queue (always do SSH clone for pushes to not hang! And use a subdir of the current project so you still can access rye run pyspr.).
GH API token is in ~/.config/gh/hosts.yml.
Always run pytest with -vsx to see output. We use rye so do rye run. ./run_tests.sh to run all in parallel (may take 10min).
When working on types, don't just add type ignores or Any or cast things away. The whole point is to get very strict typing.
