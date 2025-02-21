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

5. Create a `.spr.yaml` in the repo you care about.  Here's an example that for a repo that uses merge queue and `master` default branch, and where you want to auto-label all PRs with `run-monorepo-tests-on-push`:

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