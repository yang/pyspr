"""Stacked PR implementation."""

import concurrent.futures
import os
import sys
import re
import logging
from typing import Dict, List, Optional, TypedDict, cast
import time
from concurrent.futures import Future

# Set up logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
handler = logging.StreamHandler(sys.stderr)
handler.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.propagate = False  # Don't double log

from ..git import Commit, get_local_commit_stack, branch_name_from_commit, ConfigProtocol, GitInterface  
from ..github import GitHubInfo, PullRequest, GitHubInterface
from ..typing import StackedPRContextProtocol

class UpdateItem(TypedDict):
    """Type for update queue items."""
    pr: PullRequest
    commit: Optional[Commit]
    prev_commit: Optional[Commit]

class StackedPR:
    """StackedPR implementation."""

    def __init__(self, config: ConfigProtocol, github: GitHubInterface, git_cmd: GitInterface):
        """Initialize with config, GitHub and git clients."""
        self.config = config
        self.github = github
        self.git_cmd = git_cmd
        self.output = sys.stdout
        self.input = sys.stdin
        self.pretend = False  # Default to not pretend mode
        self.concurrency: int = cast(int, config.get('concurrency', 0))  # Get from tool.pyspr config

    def align_local_commits(self, commits: List[Commit], prs: List[PullRequest]) -> List[Commit]:
        """Align local commits with pull requests."""
        # Map commit IDs to determine if they are PR head commits 
        remote_commits: Dict[str, bool] = {}
        for pr in prs:
            for c in pr.commits:
                is_head = c.commit_id == pr.commit.commit_id
                remote_commits[c.commit_id] = is_head

        result: List[Commit] = []
        for commit in commits:
            # Keep commit if it's not in remote or if it's a PR head commit
            if commit.commit_id not in remote_commits or remote_commits[commit.commit_id]:
                result.append(commit)

        return result

    def commits_reordered(self, local_commits: List[Commit], pull_requests: List[PullRequest]) -> bool:
        """Check if commits have been reordered."""
        local_ids: List[str] = []
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

    def match_pull_request_stack(self, target_branch: str, local_commits: List[Commit], 
                           all_pull_requests: List[PullRequest]) -> List[PullRequest]:
        """Build connected stack of PRs following branch relationships."""
        if not local_commits or not all_pull_requests:
            return []
        
        logger.debug("match_pull_request_stack:")
        logger.debug(f"  Target branch: {target_branch}")
        logger.debug(f"  Local commits: {[c.commit_id for c in local_commits]}")
        logger.debug(f"  All PRs: {[(pr.number, pr.commit.commit_id, pr.base_ref) for pr in all_pull_requests]}")
            
        # Map PRs by commit ID
        pull_request_map: Dict[str, PullRequest] = {pr.commit.commit_id: pr for pr in all_pull_requests}
        logger.debug(f"  PR map has {len(pull_request_map)} entries:")
        for commit_id, pr in pull_request_map.items():
            logger.debug(f"    {commit_id}: PR #{pr.number}")
        
        # First pass: Find any PRs matching local commits by ID
        direct_matches: List[PullRequest] = []
        for commit in local_commits:
            logger.debug(f"  Checking commit {commit.commit_hash[:8]} with ID {commit.commit_id}")
            if commit.commit_id in pull_request_map:
                pr = pull_request_map[commit.commit_id]
                direct_matches.append(pr)
                logger.debug(f"  Found direct PR match #{pr.number} for commit {commit.commit_id}")
            else:
                logger.debug(f"  No PR found for commit ID {commit.commit_id}")
                
        if direct_matches:
            logger.debug(f"  Found {len(direct_matches)} direct PR matches, using those")
            return direct_matches
                
        # Second pass: Try to find stacked PRs if no direct matches
        pull_requests: List[PullRequest] = []
        
        # Find top PR in local commits
        curr_pr: Optional[PullRequest] = None
        for commit in reversed(local_commits):
            if commit.commit_id in pull_request_map:
                curr_pr = pull_request_map[commit.commit_id]
                logger.debug(f"  Found top PR #{curr_pr.number} for commit {commit.commit_id}")
                break
            
        # Build stack following branch relationships
        while curr_pr:
            pull_requests.insert(0, curr_pr)  # Prepend like Go
            logger.debug(f"  Added PR #{curr_pr.number} ({curr_pr.commit.commit_id}) to stack, base: {curr_pr.base_ref}")
            if curr_pr.base_ref == target_branch:
                logger.debug("  Reached target branch, stopping")
                break
            
            # Parse next commit ID from base branch
            if not curr_pr.base_ref:
                logger.error("  Error: Empty base branch")
                raise Exception("Empty base branch")
            match = re.match(r'spr/[^/]+/([a-f0-9]{8})', curr_pr.base_ref)
            if not match:
                logger.debug(f"  Base is {curr_pr.base_ref} which doesn't match pattern, stopping")
                break
            next_commit_id = match.group(1)
            curr_pr = pull_request_map.get(next_commit_id)
            if not curr_pr:
                logger.debug(f"  No PR found for commit {next_commit_id}, stopping")
                break
        
        logger.debug(f"  Final stack: {[pr.number for pr in pull_requests]}")
        return pull_requests

    def sort_pull_requests_by_local_commit_order(self, pull_requests: List[PullRequest], 
                                                local_commits: List[Commit]) -> List[PullRequest]:
        """Sort PRs by local commit order."""
        pull_request_map: Dict[str, PullRequest] = {pr.commit.commit_id: pr for pr in pull_requests}

        logger.debug("sort_pull_requests:")
        logger.debug(f"  Local commit IDs: {[c.commit_id for c in local_commits]}")
        logger.debug(f"  PR commit IDs: {[pr.commit.commit_id for pr in pull_requests]}")
        logger.debug(f"  PR map: {list(pull_request_map.keys())}")

        sorted_pull_requests: List[PullRequest] = []
        for commit in local_commits:
            if not commit.wip and commit.commit_id in pull_request_map:
                sorted_pull_requests.append(pull_request_map[commit.commit_id])
        logger.debug(f"  Sorted PRs: {[pr.commit.commit_id for pr in sorted_pull_requests]}")
        return sorted_pull_requests

    def fetch_and_get_github_info(self, ctx: StackedPRContextProtocol) -> Optional[GitHubInfo]:
        """Fetch from remote and get GitHub info."""
        # Basic fetch and validation
        remote = self.config.repo.get('github_remote', 'origin')
        branch = self.config.repo.get('github_branch', 'main')

        try:
            # Check if remote exists
            remotes = self.git_cmd.must_git("remote").split()
            if remote not in remotes:
                logger.error(f"Remote '{remote}' not found. Available remotes: {', '.join(remotes)}")
                return None

            self.git_cmd.must_git("fetch")

            # Check if remote branch exists
            try:
                self.git_cmd.must_git(f"rev-parse --verify {remote}/{branch}")
            except Exception:
                logger.error(f"Branch '{branch}' not found on remote '{remote}'. First push to the remote.")
                return None

            # Log env var and config before check
            logger.debug(f"SPR_NOREBASE env var: {os.environ.get('SPR_NOREBASE')}")
            logger.debug(f"noRebase config: {self.config.user.get('noRebase', False)}")

            # Check for no-rebase from env var or config
            no_rebase = (
                os.environ.get("SPR_NOREBASE") == "true" or 
                self.config.user.get('noRebase', False)
            )
            logger.debug(f"DEBUG: no_rebase={no_rebase}")
            
            if not no_rebase:
                # Simple rebase
                logger.debug("Will rebase since no_rebase is False")
                self.git_cmd.must_git(f"rebase {remote}/{branch} --autostash")
            else:
                logger.debug("Skipping rebase")
        except Exception as e:
            logger.error(f"Rebase failed: {e}")
            return None

        info = self.github.get_info(ctx, self.git_cmd)
        if info:
            # Basic branch name validation 
            branch_name_regex = r"pr_[0-9a-f]{8}"
            if re.search(branch_name_regex, info.local_branch):
                logger.error("error: don't run spr in a remote pr branch")
                logger.error(" this could lead to weird duplicate pull requests getting created")
                logger.error(" in general there is no need to checkout remote branches used for prs")
                logger.error(" instead use local branches and run spr update to sync your commit stack")
                logger.error("  with your pull requests on github")
                logger.error(f"branch name: {info.local_branch}")
                return None

        return info

    def sync_commit_stack_to_github(self, ctx: StackedPRContextProtocol, commits: List[Commit], 
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

    def _do_sync_commit_stack(self, commits: List[Commit], info: GitHubInfo) -> None:
        """Do the sync commit stack work."""
        def commit_updated(c: Commit, info: GitHubInfo) -> bool:
            for pr in info.pull_requests:
                if pr.commit.commit_id == c.commit_id:
                    return pr.commit.commit_hash != c.commit_hash
            return True

        # First filter out WIP and post-WIP commits, exactly like Go version
        non_wip_commits: List[Commit] = []
        for commit in commits:
            if commit.wip:
                break
            non_wip_commits.append(commit)

        # Then check which need updating
        updated_commits: List[Commit] = []
        for commit in non_wip_commits:
            if commit_updated(commit, info):
                updated_commits.append(commit)
                
        ref_names: List[str] = []
        for commit in updated_commits:
            branch_name = branch_name_from_commit(self.config, commit)
            ref_names.append(f"{commit.commit_hash}:refs/heads/{branch_name}")

        if ref_names:
            remote = self.config.repo.get('github_remote', 'origin')
            if self.pretend:
                logger.info("\n[PRETEND] Would push the following branches:")
                for ref_name in ref_names:
                    commit_hash, branch = ref_name.split(':refs/heads/')
                    logger.info(f"  {branch} ({commit_hash[:8]})")
            else:
                start_time = time.time()
                if self.config.repo.get('branch_push_individually', False) or self.concurrency > 0:
                    if self.concurrency > 0 and len(ref_names) > 1:
                        # Push branches in parallel with specified concurrency
                        with concurrent.futures.ThreadPoolExecutor(max_workers=self.concurrency) as executor:
                            futures: List[Future[str]] = []
                            for ref_name in ref_names:
                                futures.append(
                                    executor.submit(self.git_cmd.must_git, f"push --force {remote} {ref_name}")
                                )
                            concurrent.futures.wait(futures)
                            # Check for errors
                            for future in futures:
                                try:
                                    future.result()  # This will raise any exceptions from the thread
                                except Exception as e:
                                    logger.error(f"Push failed: {e}")
                                    raise
                    else:
                        # Sequential push
                        for ref_name in ref_names:
                            self.git_cmd.must_git(f"push --force {remote} {ref_name}")
                else:
                    cmd = f"push --force --atomic {remote} " + " ".join(ref_names)
                    self.git_cmd.must_git(cmd)
                end_time = time.time()
                logger.debug(f"Push operation took {end_time - start_time:.2f} seconds")

    def update_pull_requests(self, ctx: StackedPRContextProtocol, 
                         reviewers: Optional[List[str]] = None, 
                         count: Optional[int] = None,
                         labels: Optional[List[str]] = None) -> None:
        """Update pull requests for commits."""
        # Combine CLI labels with config labels
        config_labels: List[str] = self.config.repo.get('labels', [])
        # pyright: reportUnnecessaryIsInstance=false
        # This check is needed because config can contain str or list
        if isinstance(config_labels, str):
            config_labels = [config_labels]
        elif not isinstance(config_labels, list):
            config_labels = []
            
        all_labels = list(config_labels)
        if labels:
            all_labels.extend(labels)
        github_info = self.fetch_and_get_github_info(ctx)
        if not github_info:
            return

        # Log all pull requests from GitHub
        logger.debug("All PRs from GitHub BEFORE any filtering:")
        for pr in github_info.pull_requests:
            logger.debug(f"  PR #{pr.number}: commit_id={pr.commit.commit_id}, branch={pr.from_branch}")

        all_local_commits = get_local_commit_stack(self.config, self.git_cmd)
        logger.debug("All local commits:")
        for commit in all_local_commits:
            logger.debug(f"  {commit.commit_hash[:8]}: id={commit.commit_id} subject='{commit.subject}'")

        local_commits = self.align_local_commits(all_local_commits, github_info.pull_requests)
        logger.debug("Aligned local commits:")
        for commit in local_commits:
            logger.debug(f"  {commit.commit_hash[:8]}: id={commit.commit_id}")

        # Build connected stack like Go version
        target_branch = self.config.repo.get('github_branch', 'main')
        all_prs = github_info.pull_requests[:]
        github_info.pull_requests = self.match_pull_request_stack(
            target_branch, local_commits, all_prs
        )

        # Log matched stack
        logger.debug("Matched PR stack:")
        for pr in github_info.pull_requests:
            logger.debug(f"  PR #{pr.number}: commit_id={pr.commit.commit_id}, branch={pr.from_branch}")

        # Close PRs for deleted commits, but only if auto_close_prs is enabled
        valid_pull_requests: List[PullRequest] = []
        local_commit_map: Dict[str, Commit] = {commit.commit_id: commit for commit in local_commits}
        auto_close = self.config.repo.get('auto_close_prs', False)
        for pr in github_info.pull_requests:
            if pr.commit.commit_id not in local_commit_map:
                if auto_close:
                    if self.pretend:
                        logger.info(f"[PRETEND] Would close PR #{pr.number} - commit {pr.commit.commit_id} has gone away")
                    else:
                        logger.info(f"Closing PR #{pr.number} - commit {pr.commit.commit_id} has gone away")
                        self.github.comment_pull_request(ctx, pr, "Closing pull request: commit has gone away")
                        self.github.close_pull_request(ctx, pr)
                else:
                    logger.debug(f"Not closing PR #{pr.number} - auto_close_prs is disabled")
                    valid_pull_requests.append(pr)
            else:
                valid_pull_requests.append(pr)
        github_info.pull_requests = valid_pull_requests
        
        # Log valid PRs
        logger.debug("Valid PRs after filtering:")
        for pr in valid_pull_requests:
            logger.debug(f"  PR #{pr.number}: commit_id={pr.commit.commit_id}, branch={pr.from_branch}")

        # Get non-WIP commits 
        non_wip_commits: List[Commit] = []
        for commit in local_commits:
            if commit.wip:
                break
            non_wip_commits.append(commit)

        if not self.sync_commit_stack_to_github(ctx, local_commits, github_info):
            return

        # Update PRs
        update_queue: List[UpdateItem] = []
        assignable = None

        # Process commits in order to rebuild PRs array in correct order
        github_info.pull_requests = []

        # Match commits to PRs first by ID if possible
        logger.debug("\nProcessing commits to update/create PRs:")
        for commit_index, commit in enumerate(non_wip_commits):
            if count is not None and commit_index == count:
                break
                
            prev_commit: Optional[Commit] = non_wip_commits[commit_index-1] if commit_index > 0 else None
            logger.debug(f"\n  Processing commit {commit.commit_hash[:8]}: id={commit.commit_id}")
            logger.debug(f"  Valid PRs to match against:")
            for vpr in valid_pull_requests:
                logger.debug(f"    PR #{vpr.number}: commit_id={vpr.commit.commit_id}, branch={vpr.from_branch}")

            pr_found = False
            for pr in valid_pull_requests:
                if commit.commit_id == pr.commit.commit_id:
                    # Found matching PR - update it
                    pr_found = True
                    logger.debug(f"  Found matching PR #{pr.number}")
                    update_queue.append({
                        'pr': pr, 
                        'commit': commit,
                        'prev_commit': prev_commit
                    })
                    pr.commit = commit
                    github_info.pull_requests.append(pr)
                    if reviewers:
                        logger.warning(f"Not updating reviewers for PR #{pr.number}")
                    valid_pull_requests.remove(pr)  # Remove to avoid matching again
                    break

            if not pr_found:
                # If no match by ID, create new PR (matching Go behavior)
                if self.pretend:
                    logger.info(f"\n[PRETEND] Would create new PR for commit {commit.commit_hash[:8]}")
                    logger.info(f"  Title: {commit.subject}")
                    branch_name = branch_name_from_commit(self.config, commit)
                    base_branch = self.config.repo.get('github_branch', 'main')
                    if prev_commit:
                        base_branch = f"spr/{base_branch}/{prev_commit.commit_id}"
                    logger.info(f"  Branch: {branch_name}")
                    logger.info(f"  Base branch: {base_branch}")
                    # Create dummy PR object for the update queue
                    from ..github import PullRequest as GHPullRequest
                    from ..github import Commit as GitHubCommit
                    pr = GHPullRequest(
                        number=-1,  # Dummy number 
                        title=commit.subject,
                        commit=GitHubCommit(commit.commit_id, commit.commit_hash, commit.subject),
                        commits=[GitHubCommit(commit.commit_id, commit.commit_hash, commit.subject)],
                        body="",
                        from_branch=branch_name,
                        base_ref=base_branch
                    )
                else:
                    logger.debug(f"  No matching PR found, creating new PR")
                    pr = self.github.create_pull_request(ctx, self.git_cmd, github_info, commit, prev_commit, labels=all_labels)
                github_info.pull_requests.append(pr)
                update_queue.append({
                    'pr': pr,
                    'commit': commit,
                    'prev_commit': prev_commit
                })
                if reviewers and not self.pretend:
                    # Get list of assignable users for matching
                    if assignable is None:
                        assignable = self.github.get_assignable_users(ctx)
                    # For PyGithub we need to pass logins, not IDs
                    user_logins: List[str] = [] 
                    logger.debug(f"Trying to add reviewers: {reviewers}")
                    logger.debug(f"Assignable users: {assignable}")
                    for r in reviewers:
                        for u in assignable:
                            if r.lower() == u['login'].lower():
                                user_logins.append(r)  # Use original login for case preservation
                                break
                    if user_logins:
                        logger.debug(f"Adding reviewers {user_logins} to PR #{pr.number}")
                        self.github.add_reviewers(ctx, pr, user_logins)

        # Update all PRs to have correct bases
        if not self.pretend:
            start_time = time.time()
            if self.concurrency > 0:
                with concurrent.futures.ThreadPoolExecutor(max_workers=self.concurrency) as executor:
                    from concurrent.futures import Future
                    futures: List[Future[None]] = []
                    for update in update_queue:
                        futures.append(
                            executor.submit(self.github.update_pull_request,
                                        ctx, self.git_cmd, github_info.pull_requests,
                                        update['pr'], update['commit'], update['prev_commit'],
                                        labels=all_labels
                            )
                        )
                    concurrent.futures.wait(futures)
                    # Check for errors
                    for future in futures:
                        try:
                            future.result()  # This will raise any exceptions from the thread
                        except Exception as e:
                            logger.error(f"PR update failed: {e}")
                            raise
            else:
                for update in update_queue:
                    self.github.update_pull_request(
                        ctx, self.git_cmd, github_info.pull_requests,
                        update['pr'], update['commit'], update['prev_commit'],
                        labels=all_labels
                    )
            end_time = time.time()
            logger.debug(f"PR update operation took {end_time - start_time:.2f} seconds")
        else:
            logger.info("\n[PRETEND] Would update the following PRs:")
            for update in update_queue:
                pr = update['pr']
                commit = update['commit']
                prev_commit = update['prev_commit']
                if pr.number == -1:  # Skip dummy PRs we created above
                    continue
                base_branch = self.config.repo.get('github_branch', 'main')
                if prev_commit:
                    base_branch = f"spr/{base_branch}/{prev_commit.commit_id}"
                logger.info(f"  PR #{pr.number}: Update base branch to {base_branch}")

        # Status
        self.status_pull_requests(ctx)

    def status_pull_requests(self, ctx: StackedPRContextProtocol) -> None:
        """Show status of pull requests."""
        from ..pretty import print_header
        github_info = self.github.get_info(ctx, self.git_cmd)
        
        if not github_info or not github_info.pull_requests:
            print_header("Pull Requests", use_emoji=True)
            print("\npull request stack is empty\n")
        else:
            print_header("Pull Requests", use_emoji=True)
            print("")  # Empty line after header
            for pr in reversed(github_info.pull_requests):
                status = "✅ merged" if getattr(pr, 'merged', False) else ""
                # Space padding to match Go version
                print(f"   {str(pr)} {status}")
            print("")  # Empty line after list

    def merge_pull_requests(self, ctx: StackedPRContextProtocol, count: Optional[int] = None) -> None:
        """Merge all mergeable pull requests."""
        github_info = self.fetch_and_get_github_info(ctx)
        if not github_info:
            return

        # MergeCheck handling
        if self.config.repo.get('merge_check'):
            local_commits = get_local_commit_stack(self.config, self.git_cmd)
            if local_commits:
                last_commit = local_commits[-1]
                config_state = getattr(self.config, 'state', {})
                checked_commit: Optional[str] = config_state.get('merge_check_commit', {}).get(github_info.key()) if config_state else None
                
                if not checked_commit:
                    logger.warning("Need to run merge check 'spr check' before merging")
                    return
                elif checked_commit != "SKIP" and last_commit.commit_hash != checked_commit:
                    logger.warning("Need to run merge check 'spr check' before merging")
                    return

        if not github_info.pull_requests:
            return

        # Sort PRs in stack order (bottom to top)
        prs_in_order: List[PullRequest] = []
        
        # Find base PR (the one targeting main)
        base_pr: Optional[PullRequest] = None
        branch = self.config.repo.get('github_branch', 'main')
        for pr in github_info.pull_requests:
            if pr.base_ref == branch:
                base_pr = pr
                break

        if not base_pr:
            return

        # Build stack from bottom up
        current_pr: Optional[PullRequest] = base_pr
        while current_pr:
            prs_in_order.append(current_pr)
            next_pr = None
            for pr in github_info.pull_requests:
                # If this PR targets current PR's branch
                if pr.base_ref == f"spr/{branch}/{current_pr.commit.commit_id}":
                    next_pr = pr
                    break
            current_pr = next_pr

        # Now find highest mergeable PR in the stack
        pr_index = len(prs_in_order) - 1  # Start from top
        while pr_index >= 0:
            if prs_in_order[pr_index].mergeable(self.config):
                if count is not None and pr_index + 1 > count:
                    pr_index -= 1
                    continue
                break
            pr_index -= 1

        if pr_index < 0:
            return

        github_info.pull_requests = prs_in_order  # Update list to be in stack order
        pr_to_merge = prs_in_order[pr_index]

        # Update base of merging PR to target branch
        main_branch = self.config.repo.get('github_branch', 'main')
        from ..pretty import print_header
        
        # Nice header and status for merge
        print_header("Merging Pull Requests", use_emoji=True)
        print("")  # Empty line after header
        print(f"   Merging PR #{pr_to_merge.number} to {main_branch}")
        print(f"   This will merge {pr_index + 1} PR{'s' if pr_index > 0 else ''}")
        print("")  # Empty line after status

        # Update the base of the PR to merge to main branch
        self.github.update_pull_request(ctx, self.git_cmd, github_info.pull_requests, 
                                       pr_to_merge, None, None)

        # Merge the PR
        merge_method = self.config.repo.get('merge_method', 'squash')
        self.github.merge_pull_request(ctx, pr_to_merge, merge_method)

        # Close PRs below the merged one
        for i in range(pr_index):
            pr = github_info.pull_requests[i]
            comment = (
                f"✓ Commit merged in pull request "
                f"[#{pr_to_merge.number}](https://{self.config.repo.get('github_host', 'github.com')}/"
                f"{self.config.repo.get('github_repo_owner')}/{self.config.repo.get('github_repo_name')}"
                f"/pull/{pr_to_merge.number})"
            )
            self.github.comment_pull_request(ctx, pr, comment)
            self.github.close_pull_request(ctx, pr)

        # Print status of merged PRs
        for i in range(pr_index + 1):
            pr = github_info.pull_requests[i]
            pr.merged = True
            print(str(pr))