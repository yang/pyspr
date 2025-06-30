<!-- @format -->

# pyspr: stacked pull requests for github

I found spr to be buggy and crashy, and I wanted something I could keep hacking in Python.

Written mostly by Claude!

```
alias pr=.../bin/pyspr

pr up
pr st
```

Minimal Python port of [spr](https://github.com/ejoffe/spr).
See its docs on the usage model.

## How to install

1. Install [rye](https://rye.astral.sh/). See that website for most up-to-date install instructions.

```
which rye || curl -sSf https://rye.astral.sh/get | bash
```

2. Clone and setup this repo.

```
git clone https://github.com/yang/pyspr.git
cd pyspr
rye sync
rye run pyspr --help  # try running it
```

3. Create this alias for your shell, so that you can run from anywhere - remember to add in you bashrc / zshrc.

```
alias pr=/PATH/TO/THE/REPO/.venv/bin/pyspr
```

4. Make sure you are able to use the `gh` command already, since the auth info is read from its auth file.

5. Create a `.spr.yaml` in the repo you care about. Here's an example that for a repo that uses merge queue and `master` default branch, and where you want to auto-label all PRs with `run-monorepo-tests-on-push`:

```
---
repo:
    github_repo_owner: anthropics
    github_repo_name: anthropic
    github_host: github.com
    github_remote: origin
    github_branch: master
    require_checks: true
    require_approval: true
    merge_queue: true
    branch_push_individually: true
    show_pr_titles_in_stack: false
    merge_method: rebase
    force_fetch_tags: false
    labels: [ 'run-monorepo-tests-on-push' ]
github_repo_owner: anthropics
github_repo_name: anthropic
github_host: github.com
github_remote: origin
github_branch: master
require_checks: true
require_approval: true
merge_queue: true
branch_push_individually: true
show_pr_titles_in_stack: false
merge_method: rebase
force_fetch_tags: false
```

## How to use

See the spr docs for better docs, but a tldr:

- PRs and commits are 1:1.
- Amend commits, rebase, etc. rather than just creating new ones or merging.
- Use `pr up` to update the PR stack to reflect your local commit stack.
- Use `pr merge` to merge a bunch of PRs (chosen based on your current local commit stack).

## Tips

Use `-r` to tag reviewers.

## Running tests

To run tests, use the provided script:

```bash
# Run all tests with auto-detected parallelization
./run_tests.sh

# Run with verbose output
./run_tests.sh -vsx

# Run specific tests
./run_tests.sh -k "test_analyze"

# Run with specific number of workers
./run_tests.sh -n 4
```

The `run_tests.sh` script uses pytest-xdist for parallel test execution:

```bash
# What run_tests.sh does:
rye run pytest -p xdist -n auto "$@"
```

Additional useful options:
- `--dist loadscope`: Groups tests by module/class for better test isolation
- `--dist worksteal`: Dynamic scheduling (better for uneven test durations)
- `-x/--maxfail=1`: Stop after first failure
- `--tb=short`: Shorter tracebacks

## Gotchas

(The fix for this is not implemented so for now please avoid reordering....)

When reordering PRs/commits, we had a bug.

Say you have 4 PRs:

<img width="1244" alt="Screenshot 2025-03-19 at 11 36 09 PM" src="https://github.com/user-attachments/assets/7c74c460-c5cf-4691-826a-a052bbdfd8b9" />

Now you reorder C2 and C3, force-pushing them.
We push the commits in order, so first C3'.

<img width="1291" alt="Screenshot 2025-03-19 at 11 36 16 PM" src="https://github.com/user-attachments/assets/c77f8c91-cb32-41af-82af-7641aca5706c" />

Now we push C2' next.

<img width="1203" alt="Screenshot 2025-03-19 at 11 36 20 PM" src="https://github.com/user-attachments/assets/8b3c24f2-ea6a-40e5-8b35-b34a75787184" />

Uh-oh! Now P3 is considered merged into P2, since its commit is in the history of P2!
So P3 just got closed as merged.

We also can't first point P2 to base on P3, then update their commits, since
that would result in P2 getting closed as merged,
since its commits C2 is in the history of P3.

Let's rewind.

<img width="1291" alt="Screenshot 2025-03-19 at 11 36 16 PM" src="https://github.com/user-attachments/assets/c77f8c91-cb32-41af-82af-7641aca5706c" />

The solution is to first update P3's commits, then point P3 to base on P2.
Prevent P3's commit from falling into P2's history, and prevent P2's commit from falling into P3's history.

<img width="671" alt="Screenshot 2025-03-19 at 11 36 25 PM" src="https://github.com/user-attachments/assets/cd28a67f-c958-4e6f-bc1b-5d175e84d6ba" />

<img width="874" alt="Screenshot 2025-03-19 at 11 36 28 PM" src="https://github.com/user-attachments/assets/a9b32528-7bf6-43c6-bba0-a0bf02119c26" />

Finally wrap it up with P4/C4'.

<img width="1182" alt="Screenshot 2025-03-19 at 11 36 32 PM" src="https://github.com/user-attachments/assets/9298395e-c22d-4d86-9d23-e33929fd3bc7" />
