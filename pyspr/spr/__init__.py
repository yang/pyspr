"""Stacked PR implementation."""

import concurrent.futures
import os
import sys
import re
from typing import Dict, List, Optional

from ..git import Commit, get_local_commit_stack, branch_name_from_commit
from ..github import GitHubInfo, PullRequest

class StackedPR:
    """StackedPR implementation."""

    def __init__(self, config, github, git_cmd):
        """Initialize with config, GitHub and git clients."""
        self.config = config
        self.github = github
        self.git_cmd = git_cmd
        self.output = sys.stdout
        self.input = sys.stdin

    def align_local_commits(self, commits: List[Commit], prs: List[PullRequest]) -> List[Commit]:
        """Align local commits with pull requests."""
        # Map commit IDs to determine if they are PR head commits
        remote_commits = {}
        for pr in prs:
            for c in pr.commits:
                is_head = c.commit_id == pr.commit.commit_id
                remote_commits[c.commit_id] = is_head

        result = []
        for commit in commits:
            # Keep commit if it's not in remote or if it's a PR head commit
            if commit.commit_id not in remote_commits or remote_commits[commit.commit_id]:
                result.append(commit)

        return result

    def commits_reordered(self, local_commits: List[Commit], pull_requests: List[PullRequest]) -> bool:
        """Check if commits have been reordered."""
        local_ids = []
        for commit in local_commits:
            if not commit.wip:
                local_ids.append(commit.commit_id)
                
        pr_ids = [pr.commit.commit_id for pr in pull_requests]
        
        if len(local_ids) != len(pr_ids):
            return True
            
        for local_id, pr_id in zip(local_ids, pr_ids):
            if local_id != pr_id:
                return True
        return False

    def sort_pull_requests_by_local_commit_order(self, pull_requests: List[PullRequest], 
                                                local_commits: List[Commit]) -> List[PullRequest]:
        """Sort PRs by local commit order."""
        pull_request_map = {pr.commit.commit_id: pr for pr in pull_requests}

        sorted_pull_requests = []
        for commit in local_commits:
            if not commit.wip and commit.commit_id in pull_request_map:
                sorted_pull_requests.append(pull_request_map[commit.commit_id])
        return sorted_pull_requests

    def fetch_and_get_github_info(self, ctx) -> Optional[GitHubInfo]:
        """Fetch from remote and get GitHub info."""
        # Basic fetch and validation
        remote = self.config.repo.get('github_remote', 'origin')
        branch = self.config.repo.get('github_branch', 'main')

        try:
            # Check if remote exists
            remotes = self.git_cmd.must_git("remote").split()
            if remote not in remotes:
                print(f"Remote '{remote}' not found. Available remotes: {', '.join(remotes)}")
                return None

            self.git_cmd.must_git("fetch")

            # Check if remote branch exists
            try:
                self.git_cmd.must_git(f"rev-parse --verify {remote}/{branch}")
            except Exception:
                print(f"Branch '{branch}' not found on remote '{remote}'. First push to the remote.")
                return None

            # Simple rebase
            self.git_cmd.must_git(f"rebase {remote}/{branch} --autostash")
        except Exception as e:
            print(f"Rebase failed: {e}")
            return None

        info = self.github.get_info(ctx, self.git_cmd)
        # Basic branch name validation
        branch_name_regex = r"pr_[0-9a-f]{8}"
        if re.search(branch_name_regex, info.local_branch):
            print("error: don't run spr in a remote pr branch")
            print(" this could lead to weird duplicate pull requests getting created")
            print(" in general there is no need to checkout remote branches used for prs")
            print(" instead use local branches and run spr update to sync your commit stack")
            print("  with your pull requests on github")
            print(f"branch name: {info.local_branch}")
            return None

        return info

    def sync_commit_stack_to_github(self, ctx, commits: List[Commit], 
                                  info: GitHubInfo) -> bool:
        """Sync commits to GitHub."""
        # Check for changes
        output = self.git_cmd.must_git("status --porcelain --untracked-files=no")
        if output:
            try:
                self.git_cmd.must_git("stash")
            except Exception as e:
                print(f"Stash failed: {e}")
                return False
            try:
                self._do_sync_commit_stack(commits, info)
            finally:
                self.git_cmd.must_git("stash pop")
        else:
            self._do_sync_commit_stack(commits, info)
        return True

    def _do_sync_commit_stack(self, commits: List[Commit], info: GitHubInfo):
        """Do the sync commit stack work."""
        def commit_updated(c: Commit, info: GitHubInfo) -> bool:
            for pr in info.pull_requests:
                if pr.commit.commit_id == c.commit_id:
                    return pr.commit.commit_hash != c.commit_hash
            return True

        updated_commits = []
        for commit in commits:
            if commit.wip:
                break
            if commit_updated(commit, info):
                updated_commits.append(commit)

        ref_names = []
        for commit in updated_commits:
            branch_name = branch_name_from_commit(self.config, commit)
            ref_names.append(f"{commit.commit_hash}:refs/heads/{branch_name}")

        if ref_names:
            remote = self.config.repo.get('github_remote', 'origin')
            if self.config.repo.get('branch_push_individually', False):
                for ref_name in ref_names:
                    self.git_cmd.must_git(f"push --force {remote} {ref_name}")
            else:
                cmd = f"push --force --atomic {remote} " + " ".join(ref_names)
                self.git_cmd.must_git(cmd)

    def update_pull_requests(self, ctx, reviewers: Optional[List[str]] = None, count: Optional[int] = None):
        """Update pull requests for commits."""
        github_info = self.fetch_and_get_github_info(ctx)
        if not github_info:
            return

        local_commits = self.align_local_commits(
            get_local_commit_stack(self.config, self.git_cmd), 
            github_info.pull_requests
        )

        # Close PRs for deleted commits
        valid_pull_requests = []
        local_commit_map = {commit.commit_id: commit for commit in local_commits}
        for pr in github_info.pull_requests:
            if pr.commit.commit_id not in local_commit_map:
                self.github.comment_pull_request(ctx, pr, "Closing pull request: commit has gone away")
                self.github.close_pull_request(ctx, pr)
            else:
                valid_pull_requests.append(pr)
        github_info.pull_requests = valid_pull_requests

        # Get non-WIP commits 
        non_wip_commits = []
        for commit in local_commits:
            if commit.wip:
                break
            non_wip_commits.append(commit)

        # First sort the PRs to match commit order
        github_info.pull_requests = self.sort_pull_requests_by_local_commit_order(
            github_info.pull_requests, non_wip_commits)
        if not self.sync_commit_stack_to_github(ctx, local_commits, github_info):
            return

        # Update PRs
        update_queue = []
        assignable = None

        # Process commits in order to rebuild PRs array in correct order
        github_info.pull_requests = []

        for commit_index, c in enumerate(non_wip_commits):
            if count is not None and commit_index == count:
                break
                
            prev_commit = non_wip_commits[commit_index-1] if commit_index > 0 else None

            pr_found = False
            for pr in valid_pull_requests:
                if c.commit_id == pr.commit.commit_id:
                    pr_found = True
                    update_queue.append({
                        'pr': pr, 
                        'commit': c,
                        'prev_commit': prev_commit
                    })
                    pr.commit = c
                    github_info.pull_requests.append(pr)
                    if reviewers:
                        print(f"warning: not updating reviewers for PR #{pr.number}")
                    break

            if not pr_found:
                pr = self.github.create_pull_request(ctx, self.git_cmd, github_info, c, prev_commit)
                github_info.pull_requests.append(pr)
                update_queue.append({
                    'pr': pr,
                    'commit': c,
                    'prev_commit': prev_commit
                })
                if reviewers:
                    if assignable is None:
                        assignable = self.github.get_assignable_users(ctx)
                    user_ids = []
                    for r in reviewers:
                        for u in assignable:
                            if r.lower() == u['login'].lower():
                                user_ids.append(u['id'])
                                break
                    if user_ids:
                        self.github.add_reviewers(ctx, pr, user_ids)

        # Update all PRs to have correct bases
        with concurrent.futures.ThreadPoolExecutor() as executor:
            futures = []
            for update in update_queue:
                futures.append(
                    executor.submit(self.github.update_pull_request,
                                   ctx, self.git_cmd, github_info.pull_requests,
                                   update['pr'], update['commit'], update['prev_commit'])
                )
            concurrent.futures.wait(futures)

        # Status
        self.status_pull_requests(ctx)

    def status_pull_requests(self, ctx):
        """Show status of pull requests."""
        github_info = self.github.get_info(ctx, self.git_cmd)
        if not github_info.pull_requests:
            print("pull request stack is empty")
        else:
            for pr in reversed(github_info.pull_requests):
                print(str(pr))