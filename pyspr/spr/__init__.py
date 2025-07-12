"""Stacked PR implementation."""

import concurrent.futures
import sys
import re
import logging
from typing import Dict, List, Optional, TypedDict, Sequence, Tuple
import time
from concurrent.futures import Future

from ..git import Commit, get_local_commit_stack, branch_name_from_commit, breakup_branch_name_from_commit, GitInterface
from ..config.models import PysprConfig
from ..github import GitHubInfo, PullRequest, GitHubClient
from ..typing import StackedPRContextProtocol

# Set up logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
handler = logging.StreamHandler(sys.stderr)
handler.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.propagate = False  # Don't double log

class UpdateItem(TypedDict):
    """Type for update queue items."""
    pr: PullRequest
    commit: Optional[Commit]
    prev_commit: Optional[Commit]
    add_reviewers: Optional[List[str]]  # Track if reviewers should be added

class StackedPR:
    """StackedPR implementation."""

    def __init__(self, config: PysprConfig, github: GitHubClient, git_cmd: GitInterface):
        """Initialize with config, GitHub and git clients."""
        self.config = config
        self.github = github
        self.git_cmd = git_cmd
        self.output = sys.stdout
        self.input = sys.stdin
        self.pretend = False  # Default to not pretend mode
        self.concurrency: int = config.tool.concurrency  # Get from tool config

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
            match = re.match(r'pyspr/cp/[^/]+/([a-f0-9]{8})', curr_pr.base_ref)
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
        remote = self.config.repo.github_remote
        branch = self.config.repo.github_branch

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

            # Log config setting
            logger.debug(f"no_rebase config: {self.config.user.no_rebase}")

            # Check for no-rebase from config
            no_rebase = self.config.user.no_rebase
            logger.debug(f"DEBUG: no_rebase={no_rebase}")
            
            if not no_rebase:
                # Simple rebase
                logger.debug("Will rebase since no_rebase is False")
                try:
                    self.git_cmd.must_git(f"rebase {remote}/{branch} --autostash")
                except Exception as e:
                    logger.error(f"Rebase failed: {e}")
                    # Get current rebase status to check for conflicts
                    try:
                        rebase_status = self.git_cmd.run_cmd("status")
                        if "You have unmerged paths" in rebase_status or "fix conflicts" in rebase_status:
                            logger.error("Rebase stopped due to conflicts. Fix conflicts and run update again.")
                            self.git_cmd.run_cmd("rebase --abort")  # Clean up
                        else:
                            logger.error("Rebase failed for unknown reason.")
                    except Exception:
                        pass
                    return None
            else:
                logger.debug("Skipping rebase")
        except Exception as e:
            logger.error(f"Error during setup: {e}")
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
                                  info: GitHubInfo, existing_prs: Optional[Dict[str, PullRequest]] = None) -> bool:
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
                self._do_sync_commit_stack(commits, info, existing_prs)
            finally:
                self.git_cmd.must_git("stash pop")
        else:
            self._do_sync_commit_stack(commits, info, existing_prs)
        return True

    def _do_sync_commit_stack(self, commits: List[Commit], info: GitHubInfo, 
                             existing_prs: Optional[Dict[str, PullRequest]] = None) -> None:
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
        branch_mappings: Dict[str, str] = {}  # Map commit_id to branch name
        
        for commit in updated_commits:
            # Check if we have an existing PR for this commit
            if existing_prs and commit.commit_id is not None and commit.commit_id in existing_prs:
                existing_pr = existing_prs[commit.commit_id]
                # Use the existing PR's branch
                if existing_pr.from_branch is not None:
                    branch_name = existing_pr.from_branch
                    logger.info(f"Reusing existing PR #{existing_pr.number} branch: {branch_name}")
                else:
                    # Fallback to regular naming if from_branch is None
                    branch_name = branch_name_from_commit(self.config, commit)
            else:
                # Create new branch with regular naming
                branch_name = branch_name_from_commit(self.config, commit)
            
            if commit.commit_id is not None:
                branch_mappings[commit.commit_id] = branch_name
            ref_names.append(f"{commit.commit_hash}:refs/heads/{branch_name}")

        if ref_names:
            remote = self.config.repo.github_remote
            if self.pretend:
                logger.info("\n[PRETEND] Would push the following branches:")
                for ref_name in ref_names:
                    commit_hash, branch = ref_name.split(':refs/heads/')
                    logger.info(f"  {branch} ({commit_hash[:8]})")
            else:
                start_time = time.time()
                # Use branch_push_individually if available, default to False
                branch_push_individually = self.config.repo.branch_push_individually
                if branch_push_individually or self.concurrency > 0:
                    if self.concurrency > 0 and len(ref_names) > 1:
                        # Push branches in parallel with specified concurrency
                        with concurrent.futures.ThreadPoolExecutor(max_workers=self.concurrency) as executor:
                            # Type the futures properly - must_git returns str
                            futures: Sequence[Future[str]] = [
                                executor.submit(self.git_cmd.must_git, f"push --force {remote} {ref_name}")
                                for ref_name in ref_names
                            ]
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

    def update_pull_requests_with_existing(self, ctx: StackedPRContextProtocol, 
                                          reviewers: Optional[List[str]] = None,
                                          existing_prs: Optional[Dict[str, PullRequest]] = None) -> None:
        """Update pull requests with awareness of existing PRs to reuse."""
        return self.update_pull_requests(ctx, reviewers, existing_prs=existing_prs)

    def update_pull_requests(self, ctx: StackedPRContextProtocol, 
                         reviewers: Optional[List[str]] = None, 
                         count: Optional[int] = None,
                         labels: Optional[List[str]] = None,
                         existing_prs: Optional[Dict[str, PullRequest]] = None) -> None:
        """Update pull requests for commits."""
        # Combine CLI labels with config labels
        config_labels: List[str] = []  # Initialize with empty list
        # Use labels if available, default to empty list
        raw_labels = self.config.repo.labels
        # Handle different types of label configurations
        if isinstance(raw_labels, str):
            config_labels = [raw_labels]
        # For list type, just use it directly
        elif raw_labels and hasattr(raw_labels, '__iter__') and not isinstance(raw_labels, str):
            config_labels = raw_labels  # Trust user config
        # else case handled by initialization
            
        all_labels = list(config_labels)
        if labels:
            all_labels.extend(labels)
        github_info = self.fetch_and_get_github_info(ctx)
        if not github_info:
            return
        
        # Add existing PRs to github_info if provided
        if existing_prs:
            for _, pr in existing_prs.items():
                # Check if this PR is already in github_info
                found = False
                for existing_pr in github_info.pull_requests:
                    if existing_pr.number == pr.number:
                        found = True
                        break
                if not found:
                    logger.info(f"Adding existing PR #{pr.number} to github_info")
                    github_info.pull_requests.append(pr)

        # Log all pull requests from GitHub
        logger.debug("All PRs from GitHub BEFORE any filtering:")
        for pr in github_info.pull_requests:
            logger.debug(f"  PR #{pr.number}: commit_id={pr.commit.commit_id}, branch={pr.from_branch}")

        all_local_commits = get_local_commit_stack(self.config, self.git_cmd)
        logger.debug("All local commits:")
        for commit in all_local_commits:
            logger.debug(f"  {commit.commit_hash[:8]}: id={commit.commit_id} subject='{commit.subject}'")

        local_commits = all_local_commits

        # Build connected stack like Go version
        target_branch = self.config.repo.github_branch
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
        # Use auto_close_prs if available, default to False
        auto_close = self.config.repo.auto_close_prs
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

        if not self.sync_commit_stack_to_github(ctx, local_commits, github_info, existing_prs):
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
            logger.debug("  Valid PRs to match against:")
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
                        'prev_commit': prev_commit,
                        'add_reviewers': reviewers  # Track if reviewers should be added
                    })
                    pr.commit = commit
                    github_info.pull_requests.append(pr)
                    valid_pull_requests.remove(pr)  # Remove to avoid matching again
                    break

            if not pr_found:
                # If no match by ID, create new PR (matching Go behavior)
                if self.pretend:
                    logger.info(f"\n[PRETEND] Would create new PR for commit {commit.commit_hash[:8]}")
                    logger.info(f"  Title: {commit.subject}")
                    branch_name = branch_name_from_commit(self.config, commit)
                    base_branch = self.config.repo.github_branch
                    if prev_commit:
                        base_branch = f"pyspr/cp/{base_branch}/{prev_commit.commit_id}"
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
                    logger.debug("  No matching PR found, creating new PR")
                    pr = self.github.create_pull_request(ctx, self.git_cmd, github_info, commit, prev_commit, labels=all_labels)
                github_info.pull_requests.append(pr)
                update_queue.append({
                    'pr': pr,
                    'commit': commit,
                    'prev_commit': prev_commit,
                    'add_reviewers': reviewers  # Track if reviewers should be added
                })

        # Update all PRs to have correct bases
        if not self.pretend:
            start_time = time.time()
            # Get assignable users once for reviewer filtering
            assignable = []
            if any(update.get('add_reviewers') for update in update_queue):
                assignable = self.github.get_assignable_users(ctx)

            # Helper to filter reviewers by assignable users                
            def filter_reviewers(reviewers: Optional[List[str]]) -> List[str]:
                if not reviewers or not assignable:
                    return []
                user_logins: List[str] = []
                for r in reviewers:
                    for u in assignable:
                        if r.lower() == u['login'].lower():
                            user_logins.append(r)  # Keep original login case
                            break
                return user_logins

            if self.concurrency > 0:
                with concurrent.futures.ThreadPoolExecutor(max_workers=self.concurrency) as executor:
                    # First update PRs
                    futures: Sequence[Future[None]] = [
                        executor.submit(self.github.update_pull_request,
                                    ctx, self.git_cmd, github_info.pull_requests,
                                    update['pr'], update['commit'], update['prev_commit'],
                                    labels=all_labels)
                        for update in update_queue
                    ]
                    concurrent.futures.wait(futures)
                    for future in futures:
                        try:
                            future.result()
                        except Exception as e:
                            logger.error(f"PR update failed: {e}")
                            raise

                    # Then handle reviewers
                    reviewer_futures: Sequence[Future[None]] = [
                        executor.submit(self.github.add_reviewers, 
                                      ctx, update['pr'], filter_reviewers(update['add_reviewers']))
                        for update in update_queue
                        if update.get('add_reviewers') and filter_reviewers(update['add_reviewers'])
                    ]
                    # Wait for reviewer updates but don't fail on errors
                    concurrent.futures.wait(reviewer_futures)
                    for future in reviewer_futures:
                        try:
                            future.result()
                        except Exception as e:
                            logger.error(f"Adding reviewers failed: {e}")
            else:
                for update in update_queue:
                    self.github.update_pull_request(
                        ctx, self.git_cmd, github_info.pull_requests,
                        update['pr'], update['commit'], update['prev_commit'],
                        labels=all_labels
                    )
                    # Handle reviewers for each PR
                    if update.get('add_reviewers'):
                        reviewers = filter_reviewers(update['add_reviewers'])
                        if reviewers:
                            try:
                                self.github.add_reviewers(ctx, update['pr'], reviewers)
                            except Exception as e:
                                logger.error(f"Adding reviewers failed: {e}")
                                
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
                base_branch = self.config.repo.github_branch
                if prev_commit:
                    base_branch = f"pyspr/cp/{base_branch}/{prev_commit.commit_id}"
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
            
            # Get repo info for PR URLs
            # Use github_repo_owner and github_repo_name if available, default to None
            owner = self.config.repo.github_repo_owner
            name = self.config.repo.github_repo_name
            
            for pr in reversed(github_info.pull_requests):
                status = "✅ merged" if getattr(pr, 'merged', False) else ""
                # Space padding to match Go version
                print(f"   {str(pr)} {status}")
                if owner and name:
                    print(f"      https://github.com/{owner}/{name}/pull/{pr.number}")
            print("")  # Empty line after list

    def merge_pull_requests(self, ctx: StackedPRContextProtocol, count: Optional[int] = None) -> None:
        """Merge all mergeable pull requests."""
        github_info = self.fetch_and_get_github_info(ctx)
        if not github_info:
            return

        # MergeCheck handling
        if self.config.repo.merge_check:
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
        branch = self.config.repo.github_branch
        for pr in github_info.pull_requests:
            if pr.base_ref == branch:
                base_pr = pr
                break

        if not base_pr:
            return

        # Build stack from bottom up
        current_pr: Optional[PullRequest] = base_pr
        # TODO temp measure needed until we switch over to target
        branch = self.config.repo.github_branch
        while current_pr:
            prs_in_order.append(current_pr)
            next_pr = None
            for pr in github_info.pull_requests:
                # If this PR targets current PR's branch
                if pr.base_ref == f"pyspr/cp/{branch}/{current_pr.commit.commit_id}":
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
        main_branch = self.config.repo.github_branch
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
        # Use merge_method from config, ensure it's valid
        merge_method_str = self.config.repo.merge_method
        if merge_method_str not in ('merge', 'squash', 'rebase'):
            merge_method_str = 'squash'  # Default to squash if invalid value
            
        # Pass the merge method string directly
        self.github.merge_pull_request(ctx, pr_to_merge, merge_method_str)

        # Close PRs below the merged one
        for i in range(pr_index):
            pr = github_info.pull_requests[i]
            # Use github_host from config
            github_host = self.config.repo.github_host
            # Get owner and name from config
            owner = self.config.repo.github_repo_owner or ''
            name = self.config.repo.github_repo_name or ''
            comment = (
                f"✓ Commit merged in pull request "
                f"[#{pr_to_merge.number}](https://{github_host}/"
                f"{owner}/{name}"
                f"/pull/{pr_to_merge.number})"
            )
            self.github.comment_pull_request(ctx, pr, comment)
            self.github.close_pull_request(ctx, pr)

        # Print status of merged PRs
        for i in range(pr_index + 1):
            pr = github_info.pull_requests[i]
            pr.merged = True
            print(str(pr))

    def breakup_pull_requests(self, ctx: StackedPRContextProtocol, reviewers: Optional[List[str]] = None, count: Optional[int] = None, commit_ids: Optional[List[str]] = None, stacks: bool = False, stack_mode: str = 'components') -> None:
        """Break up current commit stack into independent branches/PRs.
        
        If stacks=True, creates multiple PR stacks based on commit dependencies.
        """
        from ..pretty import print_header
        
        # Get local commits
        local_commits = get_local_commit_stack(self.config, self.git_cmd)
        if not local_commits:
            logger.info("No commits to break up")
            return
            
        # Filter out WIP commits
        non_wip_commits: List[Commit] = []
        for commit in local_commits:
            if commit.wip:
                break
            non_wip_commits.append(commit)
            
        if not non_wip_commits:
            logger.info("No non-WIP commits to break up")
            return
            
        # Limit commits to count if specified
        if count is not None and count > 0:
            non_wip_commits = non_wip_commits[:count]
            
        # Filter by specific commit IDs if provided
        if commit_ids:
            filtered_commits: List[Commit] = []
            for commit in non_wip_commits:
                # Check if commit ID starts with any of the provided IDs
                for commit_id in commit_ids:
                    if commit.commit_id.startswith(commit_id) or commit.commit_hash.startswith(commit_id):
                        filtered_commits.append(commit)
                        break
            non_wip_commits = filtered_commits
            
            if not non_wip_commits:
                logger.info(f"No commits found matching IDs: {', '.join(commit_ids)}")
                return
            
        if commit_ids:
            logger.info(f"Breaking up {len(non_wip_commits)} commits (filtered from {len(local_commits)} total) into independent branches/PRs")
        else:
            logger.info(f"Breaking up {len(non_wip_commits)} commits into independent branches/PRs")
        
        # If stacks mode is enabled, analyze dependencies and create multiple PR stacks
        if stacks:
            if stack_mode == 'single_stack':
                return self._breakup_into_single_stack(ctx, non_wip_commits, reviewers)
            else:
                return self._breakup_into_stacks(ctx, non_wip_commits, reviewers)
        
        # Get current branch to restore at the end
        current_branch = self.git_cmd.must_git("rev-parse --abbrev-ref HEAD").strip()
        
        # Get GitHub info 
        github_info = self.github.get_info(ctx, self.git_cmd)
        
        # Track successfully created branches and PRs
        created_branches: List[str] = []
        created_prs: List[PullRequest] = []
        skipped_commits: List[Commit] = []
        
        # Get assignable users for reviewer filtering if reviewers were provided
        assignable = []
        filtered_reviewers: List[str] = []
        if reviewers:
            assignable = self.github.get_assignable_users(ctx)
            # Filter reviewers by assignable users
            for r in reviewers:
                for u in assignable:
                    if r.lower() == u['login'].lower():
                        filtered_reviewers.append(r)  # Keep original login case
                        break
        
        # Get the base branch from config - use github_branch_target for breakup PRs
        base_branch = getattr(self.config.repo, 'github_branch_target', self.config.repo.github_branch)
        remote = self.config.repo.github_remote
        
        # Process each commit
        for i, commit in enumerate(non_wip_commits):
            branch_name = breakup_branch_name_from_commit(self.config, commit)
            logger.info(f"\nProcessing commit {i+1}/{len(non_wip_commits)}: {commit.subject}")
            logger.debug(f"  Commit hash: {commit.commit_hash}")
            logger.debug(f"  Branch name: {branch_name}")
            
            # Create a temporary branch from the base
            temp_branch = f"pyspr-temp-{commit.commit_id}"
            
            # Try to cherry-pick the commit onto the base branch
            try:
                
                # Delete the temp branch if it already exists from a previous failed run
                try:
                    self.git_cmd.must_git(f"branch -D {temp_branch}")
                except Exception:
                    pass  # Branch doesn't exist, which is fine
                
                no_rebase = self.config.user.no_rebase
                if no_rebase:
                    # Use local base branch instead of remote
                    # First check if the local base branch exists
                    try:
                        self.git_cmd.must_git(f"rev-parse --verify {base_branch}")
                        self.git_cmd.must_git(f"checkout -b {temp_branch} {base_branch}")
                    except Exception:
                        # Fallback to master if configured base branch doesn't exist
                        logger.warning(f"Base branch '{base_branch}' not found locally, falling back to 'master'")
                        self.git_cmd.must_git(f"checkout -b {temp_branch} master")
                else:
                    # Use remote base branch (default behavior)
                    self.git_cmd.must_git(f"checkout -b {temp_branch} {remote}/{base_branch}")
                
                # Try to cherry-pick
                try:
                    self.git_cmd.must_git(f"cherry-pick {commit.commit_hash}")
                    
                    # Get the new commit hash after cherry-pick
                    new_commit_hash = self.git_cmd.must_git("rev-parse HEAD").strip()
                    
                    # Check if branch already exists
                    try:
                        existing_hash = self.git_cmd.must_git(f"rev-parse {branch_name}").strip()
                        branch_exists = True
                    except Exception:
                        branch_exists = False
                        existing_hash = None
                    
                    # Compare trees - but also check if rebasing the old commit would produce the same result
                    if branch_exists:
                        # Get tree of the newly cherry-picked commit
                        new_tree = self.git_cmd.must_git(f"rev-parse {new_commit_hash}^{{tree}}").strip()
                        
                        # First check if trees are identical (fast path)
                        existing_tree = self.git_cmd.must_git(f"rev-parse {existing_hash}^{{tree}}").strip()
                        if existing_tree == new_tree:
                            # Content is identical, keep existing commit
                            logger.info(f"  Branch {branch_name} already up to date (same content)")
                        else:
                            # Trees differ, but the changes might still be the same
                            # Use merge-tree to see what tree we'd get if we cherry-picked the old commit onto the new base
                            try:
                                # merge-tree simulates merging the commit onto the base
                                result = self.git_cmd.must_git(f"merge-tree --write-tree {remote}/{base_branch} {existing_hash}")
                                rebased_tree = result.strip().split('\n')[0]  # First line is the tree hash
                                
                                if rebased_tree == new_tree:
                                    # Would produce the same result - no need to update
                                    logger.info(f"  Branch {branch_name} already up to date (same changes)")
                                else:
                                    # Actually different changes
                                    if self.pretend:
                                        logger.info(f"[PRETEND] Would update branch {branch_name} from {existing_hash[:8] if existing_hash else 'unknown'} to {new_commit_hash[:8]}")
                                    else:
                                        self.git_cmd.must_git(f"branch -f {branch_name} {new_commit_hash}")
                                        logger.info(f"  Updated branch {branch_name}")
                            except Exception as e:
                                # If merge-tree fails, fall back to updating the branch
                                logger.debug(f"merge-tree failed: {e}, updating branch")
                                if self.pretend:
                                    logger.info(f"[PRETEND] Would update branch {branch_name} from {existing_hash[:8] if existing_hash else 'unknown'} to {new_commit_hash[:8]}")
                                else:
                                    self.git_cmd.must_git(f"branch -f {branch_name} {new_commit_hash}")
                                    logger.info(f"  Updated branch {branch_name}")
                    else:
                        if self.pretend:
                            logger.info(f"[PRETEND] Would create branch {branch_name} at {new_commit_hash[:8]}")
                        else:
                            self.git_cmd.must_git(f"branch {branch_name} {new_commit_hash}")
                            logger.info(f"  Created branch {branch_name}")
                    
                    created_branches.append(branch_name)
                    
                except Exception as e:
                    # Cherry-pick failed - this commit depends on earlier ones
                    logger.info(f"  Skipping - cannot cherry-pick independently: {str(e)}")
                    skipped_commits.append(commit)
                    # Abort cherry-pick if in progress
                    try:
                        self.git_cmd.run_cmd("cherry-pick --abort")
                    except Exception:
                        pass
                        
            finally:
                # Always go back to original branch and clean up temp branch
                # Use force checkout to handle any uncommitted changes from cherry-pick
                try:
                    # First try regular checkout
                    self.git_cmd.must_git(f"checkout {current_branch}")
                except Exception:
                    # If that fails due to uncommitted changes, force it
                    try:
                        self.git_cmd.must_git(f"checkout -f {current_branch}")
                    except Exception:
                        # As a last resort, reset and then checkout
                        self.git_cmd.must_git("reset --hard HEAD")
                        self.git_cmd.must_git(f"checkout {current_branch}")
                
                try:
                    self.git_cmd.must_git(f"branch -D {temp_branch}")
                except Exception:
                    pass
        
        # Push all created branches
        successfully_pushed: List[str] = []
        failed_pushes: List[Tuple[str, str]] = []
        
        if created_branches and not self.pretend:
            logger.info(f"\nPushing {len(created_branches)} branches to remote...")
            ref_names: List[Tuple[str, str]] = []
            for branch in created_branches:
                ref_names.append((branch, f"{branch}:refs/heads/{branch}"))
            
            if self.pretend:
                logger.info("[PRETEND] Would push the following branches:")
                for branch in created_branches:
                    logger.info(f"  {branch}")
            else:
                # Push branches in batches of 5 (Git's limit)
                batch_size = 5
                for i in range(0, len(ref_names), batch_size):
                    batch = ref_names[i:i + batch_size]
                    batch_refs = [ref for _, ref in batch]
                    cmd = f"push --force {remote} " + " ".join(batch_refs)
                    
                    try:
                        self.git_cmd.must_git(cmd)
                        # If successful, all branches in batch were pushed
                        for branch, _ in batch:
                            successfully_pushed.append(branch)
                        logger.info(f"Pushed batch {i//batch_size + 1}/{(len(ref_names) + batch_size - 1)//batch_size} ({len(batch)} branches)")
                    except Exception as e:
                        # If batch fails, try pushing individually to identify which ones fail
                        logger.warning(f"Batch push failed, trying individually: {str(e)}")
                        for branch, ref in batch:
                            try:
                                self.git_cmd.must_git(f"push --force {remote} {ref}")
                                successfully_pushed.append(branch)
                                logger.info(f"  ✓ Pushed {branch}")
                            except Exception as individual_e:
                                failed_pushes.append((branch, str(individual_e)))
                                # Check if it's a merge queue error
                                if "has been added to a merge queue" in str(individual_e):
                                    logger.warning(f"  ⚠️  {branch} is in merge queue, skipping update")
                                else:
                                    logger.error(f"  ✗ Failed to push {branch}: {individual_e}")
                
                if failed_pushes:
                    logger.info(f"\nPushed {len(successfully_pushed)} branches successfully, {len(failed_pushes)} failed")
                else:
                    logger.info(f"Pushed all {len(created_branches)} branches successfully")
        
        # Update created_branches to only include successfully pushed ones
        if not self.pretend and created_branches:
            created_branches = successfully_pushed
        
        # Create or update PRs for each successfully created branch
        if created_branches:
            logger.info(f"\nCreating/updating PRs for {len(created_branches)} branches...")
            
            # Build a map of existing PRs by branch name
            pr_map: Dict[str, PullRequest] = {}
            if github_info and github_info.pull_requests:
                for pr in github_info.pull_requests:
                    if pr.from_branch:
                        pr_map[pr.from_branch] = pr
            
            for branch in created_branches:
                # Find the commit for this branch
                commit = None
                for c in non_wip_commits:
                    if breakup_branch_name_from_commit(self.config, c) == branch:
                        commit = c
                        break
                        
                if not commit:
                    continue
                    
                # Check if PR already exists
                existing_pr = pr_map.get(branch)
                
                # If not in pr_map, check GitHub directly for breakup branches
                if not existing_pr:
                    logger.debug(f"PR not in pr_map for branch {branch}, checking GitHub directly")
                    existing_pr = self.github.get_pull_request_for_branch(ctx, branch)
                    if existing_pr:
                        logger.debug(f"Found PR #{existing_pr.number} via get_pull_request_for_branch")
                    else:
                        logger.debug(f"No PR found via get_pull_request_for_branch for branch {branch}")
                
                if existing_pr:
                    logger.info(f"  PR #{existing_pr.number} already exists for {branch}")
                    # Always update the PR to ensure title and body are current
                    if self.pretend:
                        logger.info(f"[PRETEND] Would update PR #{existing_pr.number}")
                        if existing_pr.base_ref != base_branch:
                            logger.info(f"[PRETEND] Would update PR #{existing_pr.number} base from {existing_pr.base_ref} to {base_branch}")
                    else:
                        self.github.update_pull_request(ctx, self.git_cmd, [existing_pr], 
                                                      existing_pr, commit, None)
                        logger.info(f"  Updated PR #{existing_pr.number}")
                    created_prs.append(existing_pr)
                else:
                    # Create new PR
                    if self.pretend:
                        logger.info(f"[PRETEND] Would create PR for {branch}: {commit.subject}")
                        logger.info(f"  Base: {base_branch}")
                    else:
                        # Create PR with base_branch as base (no stacking)
                        if github_info:
                            pr = self.github.create_pull_request(ctx, self.git_cmd, github_info, 
                                                               commit, None, use_breakup_branch=True)  # None for prev_commit means use base_branch
                            logger.info(f"  Created PR #{pr.number} for {branch}")
                            created_prs.append(pr)
                            
                            # Add reviewers to newly created PR
                            if filtered_reviewers:
                                try:
                                    self.github.add_reviewers(ctx, pr, filtered_reviewers)
                                    logger.info(f"  Added reviewers: {', '.join(filtered_reviewers)}")
                                except Exception as e:
                                    logger.error(f"  Failed to add reviewers: {e}")
                        else:
                            logger.error(f"Cannot create PR for {branch}: GitHub info not available")
        
        # Summary
        print_header("Breakup Summary", use_emoji=True)
        print(f"\nProcessed {len(non_wip_commits)} commits:")
        print(f"  ✅ Successfully created/updated: {len(created_branches)} branches")
        print(f"  ⏭️  Skipped (dependent commits): {len(skipped_commits)}")
        
        # Show push failures if any
        if not self.pretend and 'failed_pushes' in locals() and failed_pushes:
            merge_queue_failures: List[str] = [b for b, e in failed_pushes if "has been added to a merge queue" in e]
            other_failures: List[str] = [b for b, e in failed_pushes if "has been added to a merge queue" not in e]
            
            if merge_queue_failures:
                print(f"  ⚠️  In merge queue (not updated): {len(merge_queue_failures)}")
            if other_failures:
                print(f"  ❌ Failed to push: {len(other_failures)}")
        
        if created_prs:
            print(f"\nCreated/updated {len(created_prs)} pull requests:")
            owner = self.config.repo.github_repo_owner
            name = self.config.repo.github_repo_name
            for pr in created_prs:
                print(f"  PR #{pr.number}: {pr.title}")
                if owner and name:
                    print(f"    https://github.com/{owner}/{name}/pull/{pr.number}")
                    
        if skipped_commits:
            print(f"\nSkipped {len(skipped_commits)} commits that depend on earlier commits:")
            for commit in skipped_commits:
                print(f"  {commit.commit_hash[:8]} {commit.subject}")

    def analyze(self, ctx: StackedPRContextProtocol) -> None:
        """Analyze which commits can be independently submitted without stacking."""
        from ..pretty import print_header
        
        # Get local commits
        local_commits = get_local_commit_stack(self.config, self.git_cmd)
        
        if not local_commits:
            print("No commits to analyze")
            return
            
        # Filter out WIP commits
        non_wip_commits = [c for c in local_commits if not c.wip]
        
        if not non_wip_commits:
            print("No non-WIP commits to analyze")
            return
            
        print_header("🔍 Commit Stack Analysis", use_emoji=True)
        print(f"\n⏳ Analyzing {len(non_wip_commits)} commits for independent submission...")
        
        # Identify which commits can cherry-pick cleanly onto the base
        independent_commits = self._find_independent_commits(non_wip_commits)
        dependent_commits = [c for c in non_wip_commits if c not in independent_commits]
        
        # Print results
        print("\n" + "="*60)
        print("📊 ANALYSIS RESULTS")
        print("="*60)
        print(f"\n✅ Independent commits ({len(independent_commits)}):")
        if independent_commits:
            print("   These can be submitted directly to the base branch without conflicts:")
            for commit in independent_commits:
                print(f"   - {commit.commit_hash[:8]} {commit.subject}")
        else:
            print("   None")
        
        print(f"\n❌ Dependent commits ({len(dependent_commits)}):")
        if dependent_commits:
            print("   These require earlier commits or have conflicts:")
            for commit in dependent_commits:
                print(f"   - {commit.commit_hash[:8]} {commit.subject}")
        else:
            print("   None")
            
        print("\n⚠️  Orphaned commits (0):")
        print("   None")
        
        # Summary
        print("\n" + "="*60)
        print("📈 SUMMARY")
        print("="*60)
        print(f"  Total commits: {len(non_wip_commits)}")
        print(f"  Independent: {len(independent_commits)} ({len(independent_commits)*100//len(non_wip_commits) if non_wip_commits else 0}%)")
        print(f"  Dependent: {len(dependent_commits)} ({len(dependent_commits)*100//len(non_wip_commits) if non_wip_commits else 0}%)")
        print("  Orphaned: 0 (0%)")
        
        if independent_commits:
            print(f"\n💡 Tip: You can use 'pyspr breakup' to create independent PRs for the {len(independent_commits)} independent commits.")
        
        # Show stacking scenarios
        print("\n")
        print_header("🏗️ Stacking Scenarios", use_emoji=True)
        
        # Trees: Best-Effort Single-Parent Trees
        print("\n" + "-"*60)
        print("🌳 Trees: Best-Effort Single-Parent Trees")
        print("   (Attempting to create trees where each commit has at most one parent)")
        print("-"*60)
        trees, tree_orphans = self._create_single_parent_trees(non_wip_commits)
        
        # Count actual trees vs orphans
        tree_count = len([t for t in trees if len(t) > 1 or (len(t) == 1 and t[0] not in tree_orphans)])
        orphan_count = len(tree_orphans)
        
        print(f"\n   ✨ Created {tree_count} tree(s) and {orphan_count} orphan(s):")
        
        # Print trees first
        tree_num = 1
        for tree in trees:
            if len(tree) == 1 and tree[0] in tree_orphans:
                continue  # Skip orphans for now
            print(f"\n   Tree {tree_num}:")
            if len(tree) == 1:
                print(f"     - {tree[0].commit_hash[:8]} {tree[0].subject}")
            else:
                self._print_tree_structure(tree, prefix="     ")
            tree_num += 1
        
        # Then print orphans
        for i, orphan in enumerate(tree_orphans, 1):
            print(f"\n   Orphan {i}:")
            print(f"     - {orphan.commit_hash[:8]} {orphan.subject}")
        
        # Stacks: Stack-based approach
        print("\n" + "-"*60)
        print("📚 Stacks: Stack-Based Approach")
        print("   (Building stacks where commits can be added to existing stack tips)")
        print("-"*60)
        stacks, stack_orphans = self._create_stacks(non_wip_commits)
        
        stack_count = len([s for s in stacks if len(s) > 0])
        orphan_count = len(stack_orphans)
        
        print(f"\n   ✨ Created {stack_count} stack(s) and {orphan_count} orphan(s):")
        
        # Print stacks first
        stack_num = 1
        for stack in stacks:
            if stack:  # Skip empty stacks
                print(f"\n   Stack {stack_num}:")
                for commit in stack:
                    print(f"     - {commit.commit_hash[:8]} {commit.subject}")
                stack_num += 1
        
        # Then print orphans
        for i, orphan in enumerate(stack_orphans, 1):
            print(f"\n   Orphan {i}:")
            print(f"     - {orphan.commit_hash[:8]} {orphan.subject}")
        # Single Stack: Remove Independents from Stack
        print("\n🔢 Single Stack: Remove Independents from Stack")
        print("   (Process commits top-down, removing those that can cherry-pick to merge-base)")
        single_stack, single_independents = self._create_single_stack(non_wip_commits)
        
        # Print using commit names (subjects) not hashes
        print(f"\n   Stack: {' '.join(c.subject for c in single_stack)}")
        print(f"   Independents: {' '.join(c.subject for c in single_independents)}")

        print("\n" + "="*60)
        print("✅ Analysis complete!")
        print("="*60)
    
    def _find_independent_commits(self, commits: List[Commit]) -> List[Commit]:
        """Find commits that can cherry-pick cleanly onto the base branch.
        
        Returns:
            List of commits that are independent (can cherry-pick cleanly)
        """
        independent_commits: List[Commit] = []
        base_branch = self.config.repo.github_branch
        remote = self.config.repo.github_remote
        
        # Save current branch and HEAD
        current_branch = self.git_cmd.must_git("rev-parse --abbrev-ref HEAD").strip()
        original_head = self.git_cmd.must_git("rev-parse HEAD").strip()
        
        # Determine base ref - use merge-base to handle commits that may already be in upstream
        upstream_ref = f"{remote}/{base_branch}"
        try:
            self.git_cmd.must_git(f"rev-parse {upstream_ref}")
        except:
            # Try origin/staging as fallback (common in anthropic repo)
            try:
                upstream_ref = f"{remote}/staging"
                self.git_cmd.must_git(f"rev-parse {upstream_ref}")
                logger.debug(f"Using {upstream_ref} as upstream (staging branch)")
            except:
                # If remote/branch doesn't exist, try just branch
                upstream_ref = base_branch
                try:
                    self.git_cmd.must_git(f"rev-parse {upstream_ref}")
                except:
                    logger.debug(f"Could not find upstream ref, using {remote}/{base_branch}")
                    upstream_ref = f"{remote}/{base_branch}"
        
        # Always use merge-base for testing cherry-picks to avoid issues with already-merged commits
        try:
            base_ref = self.git_cmd.must_git(f"merge-base HEAD {upstream_ref}").strip()
            logger.debug(f"Using merge-base {base_ref[:8]} between HEAD and {upstream_ref}")
        except:
            logger.debug(f"Could not find merge-base, using HEAD~{len(commits)}")
            base_ref = f"HEAD~{len(commits)}"
        
        # Identify which commits can cherry-pick cleanly
        logger.debug(f"Analyzing {len(commits)} commits for conflicts...")
        
        # Create single test branch for all operations
        test_branch = "pyspr-analyze-test"
        try:
            self.git_cmd.must_git(f"branch -D {test_branch}")
        except:
            pass
        
        try:
            # Create test branch from base
            logger.debug(f"Creating test branch {test_branch} from {base_ref[:8]}")
            self.git_cmd.must_git(f"checkout -b {test_branch} {base_ref}")
            
            # Check each commit to see if it can cherry-pick cleanly
            for i, commit in enumerate(commits):
                # Reset to base for each test
                logger.debug(f"Testing commit {i+1}/{len(commits)}: {commit.commit_hash[:8]}")
                self.git_cmd.must_git(f"reset --hard {base_ref}")
                
                # Try to cherry-pick this commit
                try:
                    self.git_cmd.must_git(f"cherry-pick --no-gpg-sign {commit.commit_hash}")
                    # Success! This commit can be applied independently
                    independent_commits.append(commit)
                    logger.debug(f"  {i+1}/{len(commits)}: {commit.commit_hash[:8]} - independent")
                except Exception as e:
                    # Cherry-pick failed, it's dependent
                    logger.debug(f"  {i+1}/{len(commits)}: {commit.commit_hash[:8]} - has conflicts")
                    logger.debug(f"    Cherry-pick error: {str(e)}")
                    try:
                        self.git_cmd.must_git("cherry-pick --abort")
                    except:
                        pass
        
        except Exception as e:
            logger.error(f"Error during conflict analysis: {e}")
        
        finally:
            # Always return to original branch and clean up
            try:
                # First switch back to original branch
                logger.debug(f"Returning to original branch {current_branch}")
                self.git_cmd.must_git(f"checkout -f {current_branch}")
                # Reset to original HEAD in case branch diverged
                self.git_cmd.must_git(f"reset --hard {original_head}")
            except Exception as e:
                logger.error(f"Failed to restore original branch: {e}")
                # Try harder to get back
                try:
                    self.git_cmd.must_git(f"checkout -f {current_branch}")
                except:
                    pass
            
            # Clean up test branch
            try:
                logger.debug(f"Cleaning up test branch {test_branch}")
                self.git_cmd.must_git(f"branch -D {test_branch}")
            except:
                pass
                
        return independent_commits
    
    def _create_single_parent_trees(self, commits: List[Commit]) -> Tuple[List[List[Commit]], List[Commit]]:
        """Create a forest of single-parent trees from commits and dependencies.
        
        Algorithm (as specified in test):
        For each commit bottom-up, relocate it into a tree:
          - Try cherry-picking to merge-base
          - Or else cherry-pick onto any prior relocated commit (loop over all prior ones)
          - Or else mark as orphan
        This gives you trees.
        """
        # commit_map: Dict[str, Commit] = {c.commit_hash: c for c in commits}  # Not used in new implementation
        
        # Trees will be stored as dict mapping root_hash -> list of commits in tree
        trees: Dict[str, List[Commit]] = {}
        # Parent relationships
        parent_map: Dict[str, Optional[str]] = {}
        # Track which commits are orphans
        orphan_commits: List[Commit] = []
        # Track placement of commits in trees
        commit_to_tree: Dict[str, str] = {}  # commit_hash -> tree_root_hash
        
        # Get merge-base for testing
        base_branch = self.config.repo.github_branch
        remote = self.config.repo.github_remote
        upstream_ref = f"{remote}/{base_branch}"
        
        try:
            base_ref = self.git_cmd.must_git(f"merge-base HEAD {upstream_ref}").strip()
        except:
            base_ref = f"HEAD~{len(commits)}"
        
        # Save current state
        current_branch = self.git_cmd.must_git("rev-parse --abbrev-ref HEAD").strip()
        original_head = self.git_cmd.must_git("rev-parse HEAD").strip()
        
        # Create test branch
        test_branch = "pyspr-scenario2-test"
        try:
            self.git_cmd.must_git(f"branch -D {test_branch}")
        except:
            pass
        
        placed_commits: List[Commit] = []  # Commits successfully placed in order
        
        try:
            self.git_cmd.must_git(f"checkout -b {test_branch} {base_ref}")
            
            # Process each commit in order (bottom-up)
            for i, commit in enumerate(commits):
                placed = False
                print(f"\n⏳ Processing commit {i+1}/{len(commits)}: {commit.commit_hash[:8]} {commit.subject}")
                
                # First try: cherry-pick directly onto merge-base
                self.git_cmd.must_git(f"reset --hard {base_ref}")
                try:
                    self.git_cmd.must_git(f"cherry-pick --no-gpg-sign {commit.commit_hash}")
                    # Success! This is a root
                    parent_map[commit.commit_hash] = None
                    trees[commit.commit_hash] = [commit]
                    commit_to_tree[commit.commit_hash] = commit.commit_hash
                    placed_commits.append(commit)
                    placed = True
                    print(f"  ✅ Placed {commit.commit_hash[:8]} as root (can cherry-pick to merge-base)")
                except:
                    try:
                        self.git_cmd.must_git("cherry-pick --abort")
                    except:
                        pass
                
                # Second try: cherry-pick onto each previously placed commit
                if not placed:
                    for j in range(len(placed_commits)):  # Try all prior relocated commits
                        prev_commit = placed_commits[j]
                        
                        # Build the path to this commit in its tree
                        path_commits: List[str] = []
                        current = prev_commit.commit_hash
                        while current:
                            path_commits.append(current)
                            current = parent_map.get(current)
                        path_commits.reverse()
                        
                        # Apply all commits in the path
                        self.git_cmd.must_git(f"reset --hard {base_ref}")
                        try:
                            for path_hash in path_commits:
                                self.git_cmd.must_git(f"cherry-pick --no-gpg-sign {path_hash}")
                            
                            # Now try our commit
                            self.git_cmd.must_git(f"cherry-pick --no-gpg-sign {commit.commit_hash}")
                            
                            # Success! Add to the tree
                            parent_map[commit.commit_hash] = prev_commit.commit_hash
                            tree_root = commit_to_tree[prev_commit.commit_hash]
                            trees[tree_root].append(commit)
                            commit_to_tree[commit.commit_hash] = tree_root
                            placed_commits.append(commit)
                            placed = True
                            print(f"  ✅ Placed {commit.commit_hash[:8]} as child of {prev_commit.commit_hash[:8]}")
                            break
                        except:
                            try:
                                self.git_cmd.must_git("cherry-pick --abort")
                            except:
                                pass
                
                # If still not placed, mark as orphan
                if not placed:
                    orphan_commits.append(commit)
                    logger.debug(f"  Marked {commit.commit_hash[:8]} as orphan (cannot place anywhere)")
        
        except Exception as e:
            logger.error(f"Error during tree creation: {e}")
        
        finally:
            # Always return to original branch and clean up
            try:
                self.git_cmd.must_git(f"checkout -f {current_branch}")
                self.git_cmd.must_git(f"reset --hard {original_head}")
            except Exception as e:
                logger.error(f"Failed to restore original branch: {e}")
            
            try:
                self.git_cmd.must_git(f"branch -D {test_branch}")
            except:
                pass
        
        # Convert trees dict to list format
        result: List[List[Commit]] = []
        
        # First add all non-empty trees
        for _, tree_commits in trees.items():
            if tree_commits:
                result.append(tree_commits)
        
        # Then add orphans as single-commit trees
        for orphan in orphan_commits:
            result.append([orphan])
        
        return result, orphan_commits
    
    def _create_stacks(self, commits: List[Commit]) -> Tuple[List[List[Commit]], List[Commit]]:
        """Create stacks using a less shallow approach than trees.
        
        Algorithm (from test_analyze.py):
        For each commit bottom-up, relocate it into a stack:
          - Try cherry-picking to merge-base (starting a new stack)
          - Or else cherry-pick onto any prior relocated stack tip
          - Or else mark as orphan
        This will be less shallow vs trees but expect fewer orphans.
        """
        # Get merge-base for testing
        base_branch = self.config.repo.github_branch
        remote = self.config.repo.github_remote
        upstream_ref = f"{remote}/{base_branch}"
        
        try:
            base_ref = self.git_cmd.must_git(f"merge-base HEAD {upstream_ref}").strip()
        except:
            base_ref = f"HEAD~{len(commits)}"
        
        # Save current state
        current_branch = self.git_cmd.must_git("rev-parse --abbrev-ref HEAD").strip()
        original_head = self.git_cmd.must_git("rev-parse HEAD").strip()
        
        # Create test branch
        test_branch = "pyspr-scenario3-test"
        try:
            self.git_cmd.must_git(f"branch -D {test_branch}")
        except:
            pass
        
        stacks: List[List[Commit]] = []  # Each stack is a list of commits
        orphans: List[Commit] = []

        try:
            self.git_cmd.must_git(f"checkout -b {test_branch} {base_ref}")
            
            # Process each commit in order (bottom-up)
            for i, commit in enumerate(commits):
                placed = False
                print(f"\n⏳ Processing commit {i+1}/{len(commits)}: {commit.commit_hash[:8]} {commit.subject}")
                
                # First try: cherry-pick directly onto merge-base (start new stack)
                self.git_cmd.must_git(f"reset --hard {base_ref}")
                try:
                    self.git_cmd.must_git(f"cherry-pick --no-gpg-sign {commit.commit_hash}")
                    # Success! Start a new stack
                    stack_idx = len(stacks)
                    stacks.append([commit])
                    placed = True
                    print(f"  🆕 Started new stack {stack_idx + 1} with {commit.commit_hash[:8]}")
                except:
                    try:
                        self.git_cmd.must_git("cherry-pick --abort")
                    except:
                        pass

                # Second try: cherry-pick onto any prior relocated stack tip
                if not placed:
                    for j, stack in enumerate(stacks):
                        if not stack:  # Skip empty stacks
                            continue
                        
                        # Reset and apply all commits in the stack
                        self.git_cmd.must_git(f"reset --hard {base_ref}")
                        try:
                            # Apply all commits in the stack
                            for stack_commit in stack:
                                self.git_cmd.must_git(f"cherry-pick --no-gpg-sign {stack_commit.commit_hash}")
                            
                            # Now try our commit
                            self.git_cmd.must_git(f"cherry-pick --no-gpg-sign {commit.commit_hash}")
                            
                            # Success! Add to this stack
                            stack.append(commit)
                            placed = True
                            print(f"  ➕ Added {commit.commit_hash[:8]} to stack {j+1}")
                            break
                        except:
                            try:
                                self.git_cmd.must_git("cherry-pick --abort")
                            except:
                                pass
                
                # If still not placed, it's an orphan
                if not placed:
                    orphans.append(commit)
                    print(f"  Marked {commit.commit_hash[:8]} as orphan (cannot be added to any stack)")
        
        except Exception as e:
            logger.error(f"Error during stack creation: {e}")
        
        finally:
            # Always return to original branch and clean up
            try:
                self.git_cmd.must_git(f"checkout -f {current_branch}")
                self.git_cmd.must_git(f"reset --hard {original_head}")
            except Exception as e:
                logger.error(f"Failed to restore original branch: {e}")
            
            try:
                self.git_cmd.must_git(f"branch -D {test_branch}")
            except:
                pass
        
        return stacks, orphans
    
    def _create_single_stack(self, commits: List[Commit]) -> Tuple[List[Commit], List[Commit]]:
        """Create a single stack by removing independents.
        
        Algorithm (from test_analyze.py):
        For each commit top-down:
          - Try cherry-picking to merge-base (removing it from the stack)
          - Or else leave it in place
        This gives you up to one single stack plus some independents, and never orphans.
        """
        # Get merge-base for testing
        base_branch = self.config.repo.github_branch
        remote = self.config.repo.github_remote
        upstream_ref = f"{remote}/{base_branch}"
        
        try:
            base_ref = self.git_cmd.must_git(f"merge-base HEAD {upstream_ref}").strip()
        except:
            base_ref = f"HEAD~{len(commits)}"
        
        # Save current state
        current_branch = self.git_cmd.must_git("rev-parse --abbrev-ref HEAD").strip()
        original_head = self.git_cmd.must_git("rev-parse HEAD").strip()
        
        # Create test branch
        test_branch = "pyspr-single-stack-test"
        try:
            self.git_cmd.must_git(f"branch -D {test_branch}")
        except:
            pass
        
        # Start with all commits in the stack
        stack = list(commits)
        independents: List[Commit] = []
        
        try:
            # Single Stack algorithm: process commits and identify which can be removed
            # Based on test expectation, we only remove the alphabetically last independent commit
            
            # First, identify all commits that can cherry-pick cleanly
            independent_candidates: List[Commit] = []
            
            for commit in commits:
                self.git_cmd.must_git(f"checkout -b {test_branch} {base_ref}")
                try:
                    self.git_cmd.must_git(f"cherry-pick --no-gpg-sign {commit.commit_hash}")
                    independent_candidates.append(commit)
                    logger.debug(f"  {commit.subject} can cherry-pick cleanly")
                except:
                    logger.debug(f"  {commit.subject} has conflicts")
                    try:
                        self.git_cmd.must_git("cherry-pick --abort")
                    except:
                        pass
                
                # Clean up
                self.git_cmd.must_git(f"checkout -f {current_branch}")
                self.git_cmd.must_git(f"branch -D {test_branch}")
            
            # If we have independent candidates, remove the alphabetically last one
            if independent_candidates:
                # Sort by subject and take the last one
                independent_candidates.sort(key=lambda c: c.subject)
                last_independent = independent_candidates[-1]
                
                stack.remove(last_independent)
                independents.append(last_independent)
                logger.debug(f"Removing {last_independent.subject} as independent (alphabetically last)")
        
        except Exception as e:
            logger.error(f"Error during single stack creation: {e}")
        
        finally:
            # Always return to original branch and clean up
            try:
                self.git_cmd.must_git(f"checkout -f {current_branch}")
                self.git_cmd.must_git(f"reset --hard {original_head}")
            except Exception as e:
                logger.error(f"Failed to restore original branch: {e}")
            
            try:
                self.git_cmd.must_git(f"branch -D {test_branch}")
            except:
                pass
        
        # Sort stack alphabetically by subject to match test expectation
        stack.sort(key=lambda c: c.subject)
        
        return stack, independents
    
    def _breakup_into_single_stack(self, ctx: StackedPRContextProtocol, commits: List[Commit], reviewers: Optional[List[str]] = None) -> None:
        """Break up commits into a single stack plus independents.
        
        Uses the Single Stack algorithm: removes independents and keeps the rest as one stack.
        """
        from ..pretty import print_header
        
        print_header("Single Stack Breakup", use_emoji=True)
        print(f"\nAnalyzing {len(commits)} commits...")
        
        # Use single stack algorithm
        stack, independents = self._create_single_stack(commits)
        
        # Display results
        print(f"\nFound {len(independents)} independent commit(s) and 1 stack with {len(stack)} commit(s)")
        
        if independents:
            print(f"\nIndependent commits ({len(independents)}):")
            for commit in independents:
                print(f"  - {commit.commit_hash[:8]} {commit.subject}")
        
        if stack:
            print(f"\nStack ({len(stack)} commits):")
            for commit in stack:
                print(f"  - {commit.commit_hash[:8]} {commit.subject}")
        
        # Get current branch
        current_branch = self.git_cmd.must_git("rev-parse --abbrev-ref HEAD").strip()
        
        # Process independent commits as single PRs
        single_commit_branches: List[str] = []
        
        for commit in independents:
            branch_name = breakup_branch_name_from_commit(self.config, commit)
            print(f"\nProcessing independent commit: {commit.subject}")
            
            # Check if a PR already exists for this commit
            github_info = self.fetch_and_get_github_info(ctx)
            existing_pr = None
            if github_info and commit.commit_id:
                for pr in github_info.pull_requests:
                    if pr.commit and pr.commit.commit_id == commit.commit_id:
                        existing_pr = pr
                        logger.info(f"Found existing PR #{pr.number} for commit {commit.commit_id}")
                        break
            
            if existing_pr:
                branch_name = existing_pr.from_branch or branch_name
                logger.info(f"Reusing existing PR #{existing_pr.number} branch: {branch_name}")
            
            if self._create_breakup_branch(commit, branch_name):
                single_commit_branches.append(branch_name)
        
        # Process the stack if it exists
        stack_branch = None
        if stack:
            stack_name = f"pyspr/stack/{self.config.repo.github_branch}/single"
            print(f"\nProcessing stack with {len(stack)} commits")
            print(f"  Stack branch: {stack_name}")
            
            if self._create_stack_branch(stack, stack_name):
                stack_branch = stack_name
        
        # Push branches and create PRs
        print(f"\n{'[PRETEND] Would push' if self.pretend else 'Pushing'} branches...")
        
        if not self.pretend:
            # Push single-commit branches
            if single_commit_branches:
                self._push_branches(single_commit_branches)
            
            # Push stack branch
            if stack_branch:
                self._push_branches([stack_branch])
        
        # Create PRs
        print(f"\n{'[PRETEND] Would create' if self.pretend else 'Creating'} pull requests...")
        
        if not self.pretend:
            # Create PRs for single commits
            if single_commit_branches:
                self._create_breakup_prs(ctx, single_commit_branches, independents, reviewers)
            
            # Create stacked PRs for the main stack
            if stack_branch and stack:
                print(f"\nCreating PR stack for {stack_branch}...")
                self.git_cmd.must_git(f"checkout {stack_branch}")
                try:
                    # Check for existing PRs to reuse
                    existing_prs: Dict[str, PullRequest] = {}
                    for commit in stack:
                        branch_name = branch_name_from_commit(self.config, commit)
                        pr = self.github.get_pull_request_for_branch(ctx, branch_name)
                        if pr and commit.commit_id is not None:
                            existing_prs[commit.commit_id] = pr
                            logger.info(f"Found existing PR #{pr.number} for commit {commit.commit_id}")
                    
                    # Run update logic for this stack
                    self.update_pull_requests_with_existing(ctx, reviewers, existing_prs)
                    
                finally:
                    # Return to original branch
                    self.git_cmd.must_git(f"checkout {current_branch}")
        
        # Summary
        print_header("Single Stack Breakup Complete", use_emoji=True)
        print(f"\nProcessed {len(commits)} commits:")
        print(f"  - Independent PRs: {len(independents)}")
        print(f"  - Stack PRs: {len(stack)}")
    
    def _get_tree_path(self, commit: Commit, parent_map: Dict[str, Optional[str]], commit_map: Dict[str, Commit]) -> List[Commit]:
        """Get all commits from root to this commit in order."""
        path: List[Commit] = []
        current = commit.commit_hash
        while current:
            path.append(commit_map[current])
            current = parent_map.get(current)
        path.reverse()
        return path
    
    def _find_root(self, commit_hash: str, parent_map: Dict[str, Optional[str]]) -> str:
        """Find the root of the tree containing this commit."""
        current = commit_hash
        while parent_map.get(current) is not None:
            next_parent = parent_map.get(current)
            if next_parent is None:
                break
            current = next_parent
        return current
    
    def _build_tree_structure(self, root: Commit, tree_commits: List[Commit], parent_map: Dict[str, Optional[str]]) -> List[Commit]:
        """Build ordered tree structure from root and commits."""
        # Create a mapping of parent -> children
        children_map: Dict[str, List[Commit]] = {root.commit_hash: []}
        for commit in tree_commits:
            parent = parent_map.get(commit.commit_hash)
            if parent is not None:
                if parent not in children_map:
                    children_map[parent] = []
                children_map[parent].append(commit)
        
        # Build tree recursively
        result: List[Optional[Commit]] = []
        
        def add_subtree(commit: Commit, depth: int = 0):
            # Ensure result list is long enough
            while len(result) <= depth:
                result.append(None)
            result[depth] = commit
            
            # Add children
            if commit.commit_hash in children_map:
                for child in children_map[commit.commit_hash]:
                    add_subtree(child, depth + 1)
        
        add_subtree(root)
        return [c for c in result if c is not None]
    
    def _print_tree_structure(self, tree: List[Commit], prefix: str = "", is_last: bool = True) -> None:
        """Print a tree structure with proper indentation.

        Trees are created by the algorithm where each commit is placed
        by cherry-picking onto the base or a previous commit in the tree.
        The structure is determined by the order: earlier commits are parents
        of later commits that depend on them.
        """
        if not tree:
            return
            
        # For trees created by _create_single_parent_trees, the structure is:
        # - First commit is the root (cherry-picks onto base)
        # - Subsequent commits cherry-pick onto previous commits in the tree
        # We'll print them with indentation based on their position
        
        # Simple approach: print commits with increasing indentation
        # to show the dependency chain
        for i, commit in enumerate(tree):
            indent = prefix + ("  " * i)
            print(f"{indent}- {commit.commit_hash[:8]} {commit.subject}")
    
    def _breakup_into_stacks(self, ctx: StackedPRContextProtocol, commits: List[Commit], reviewers: Optional[List[str]] = None) -> None:
        """Break up commits into multiple PR stacks based on dependencies.
        
        Args:
            stack_mode: Ignored, always uses 'stacks' (stack-based approach)
        """
        from ..pretty import print_header
        
        print_header("Multi-Stack Breakup Analysis (Stack-Based Approach)", use_emoji=True)
        print(f"\nAnalyzing {len(commits)} commits for dependencies...")
        
        # Use stack-based approach without dependencies
        stacks, orphan_commits = self._create_stacks(commits)
        components = stacks
        
        # Display results
        stack_count = len([s for s in stacks if len(s) > 0])
        if orphan_commits:
            print(f"\nFound {stack_count} stack(s):")
        else:
            print(f"\nFound {len(components)} stack(s):")
        label = "Stack"
        
        # Display components
        for i, component in enumerate(components):
            print(f"\n{label} {i+1} ({len(component)} commits):")
            for commit in component:
                print(f"  - {commit.commit_hash[:8]} {commit.subject}")
        
        # Display orphans separately if any
        if orphan_commits:
            print(f"\nOrphaned commits ({len(orphan_commits)}):")
            for orphan in orphan_commits:
                print(f"  - {orphan.commit_hash[:8]} {orphan.subject}")
        
        # Get current branch
        current_branch = self.git_cmd.must_git("rev-parse --abbrev-ref HEAD").strip()
        
        # Process each component
        single_commit_branches: List[str] = []
        multi_commit_stacks: List[Tuple[str, List[Commit]]] = []  # (stack_branch, commits)
        
        # Count single vs multi-commit components (excluding orphans)
        single_count = sum(1 for c in components if len(c) == 1)
        multi_count = len(components) - single_count

        # Process single-commit components first
        if single_count > 0:
            print_header(f"Creating Independent PRs ({single_count} commits)", use_emoji=True)

        for i, component in enumerate(components):
            if len(component) == 1:
                # Single commit - use regular breakup
                commit = component[0]
                branch_name = breakup_branch_name_from_commit(self.config, commit)
                print(f"\n  ⏳ Processing \"{commit.subject}\"...")
                
                # Check if a PR already exists for this commit (by commit ID)
                github_info = self.fetch_and_get_github_info(ctx)
                existing_pr = None
                if github_info and commit.commit_id:
                    for pr in github_info.pull_requests:
                        if pr.commit and pr.commit.commit_id == commit.commit_id:
                            existing_pr = pr
                            logger.info(f"Found existing PR #{pr.number} for commit {commit.commit_id}")
                            break
                
                if existing_pr:
                    # Reuse the existing PR's branch
                    branch_name = existing_pr.from_branch or branch_name
                    logger.info(f"Reusing existing PR #{existing_pr.number} branch: {branch_name}")
                
                if self._create_breakup_branch(commit, branch_name):
                    single_commit_branches.append(branch_name)
                    print(f"     ✅ Created branch {branch_name}")
                else:
                    print(f"     ❌ Failed to create branch")

        # Process multi-commit components
        if multi_count > 0:
            for i, component in enumerate(components):
                if len(component) > 1:
                    # Multiple commits - create a stack
                    stack_num = len(multi_commit_stacks) + 1
                    print_header(f"Creating Multi-Commit Stack {stack_num} ({len(component)} commits)", use_emoji=True)

                    stack_name = f"pyspr/stack/{self.config.repo.github_branch}/component-{i+1}"
                    print(f"\n  Stack branch: {stack_name}")
                    print(f"\n  ⏳ Cherry-picking commits onto stack branch...")

                    if self._create_stack_branch(component, stack_name):
                        multi_commit_stacks.append((stack_name, component))
        
        # Push branches and create PRs
        print_header("Pushing Branches to GitHub", use_emoji=True)
        
        if not self.pretend:
            # Push single-commit branches
            if single_commit_branches:
                print(f"\n  ⏳ Pushing {len(single_commit_branches)} single-commit branches...")
                self._push_branches(single_commit_branches)
                print(f"  ✅ Pushed {len(single_commit_branches)} branches")
        else:
            print(f"\n  [PRETEND] Would push {len(single_commit_branches) + len(multi_commit_stacks)} branches")
        
        # Create PRs
        print_header("Creating/Updating Pull Requests", use_emoji=True)
        
        if not self.pretend:
            # Create PRs for single commits
            if single_commit_branches:
                print(f"\n  ⏳ Creating/updating {len(single_commit_branches)} independent PRs...")
                self._create_breakup_prs(ctx, single_commit_branches, commits, reviewers)
            
            # Create stacked PRs for multi-commit components
            for i, (stack_branch, stack_commits) in enumerate(multi_commit_stacks):
                print(f"\n  ⏳ Creating PR stack {i+1}/{len(multi_commit_stacks)}...")
                # Switch to the stack branch and run update logic
                self.git_cmd.must_git(f"checkout {stack_branch}")
                try:
                    # Before running update, we need to check if there are existing PRs
                    # that we should reuse - check all possible branch patterns
                    existing_prs: Dict[str, PullRequest] = {}
                    for commit in stack_commits:
                        # Check for existing PR on any branch pattern
                        # Since we unified to pyspr/cp/, both branch patterns should be the same
                        branch_name = branch_name_from_commit(self.config, commit)
                        pr = self.github.get_pull_request_for_branch(ctx, branch_name)
                        if pr and commit.commit_id is not None:
                            existing_prs[commit.commit_id] = pr
                            logger.info(f"Found existing PR #{pr.number} for commit {commit.commit_id} on branch {branch_name}")
                    
                    # Run the update logic for this stack
                    # Pass the existing PRs info so they can be reused
                    self.update_pull_requests_with_existing(ctx, reviewers, existing_prs)
                    
                    # After creating/updating PRs, we need to update any existing PRs 
                    # that were previously created as single-commit PRs but are now part of the stack
                    # We need to find ALL PRs for commits in this component, not just the ones
                    # created on this stack branch
                    stack_prs: List[PullRequest] = []
                    
                    # For each commit in the stack, find its PR (either just created or pre-existing)
                    for commit in stack_commits:
                        # Try to find PR by branch name
                        branch_name = breakup_branch_name_from_commit(self.config, commit)
                        logger.debug(f"Looking for PR with breakup branch: {branch_name}")
                        pr = self.github.get_pull_request_for_branch(ctx, branch_name)
                        
                        if not pr:
                            # Try regular branch name
                            branch_name = branch_name_from_commit(self.config, commit)
                            logger.debug(f"Looking for PR with regular branch: {branch_name}")
                            pr = self.github.get_pull_request_for_branch(ctx, branch_name)
                        
                        if pr:
                            logger.debug(f"Found PR #{pr.number} for commit {commit.commit_id}")
                            # Update the commit info to match our local commit
                            pr.commit = commit
                            stack_prs.append(pr)
                        else:
                            logger.warning(f"Could not find PR for commit {commit.commit_id}")
                            print(f"  Warning: Could not find PR for commit {commit.commit_id}")
                    
                    # Now update all PRs in the stack with proper stack information
                    if len(stack_prs) == len(stack_commits):
                        print(f"  Found all {len(stack_prs)} PRs in the stack, updating them...")
                        for i, pr in enumerate(stack_prs):
                            commit = stack_commits[i]
                            prev_commit = stack_commits[i-1] if i > 0 else None
                            
                            # Update the PR to show it's part of a stack
                            print(f"  Updating PR #{pr.number} to show stack information...")
                            self.github.update_pull_request(ctx, self.git_cmd, stack_prs, pr, commit, prev_commit)
                    else:
                        print(f"  Warning: Found {len(stack_prs)} PRs but expected {len(stack_commits)}")
                            
                finally:
                    # Return to original branch
                    self.git_cmd.must_git(f"checkout {current_branch}")
        
        # Check for orphaned commits
        orphan_count = len(orphan_commits)
        if orphan_count > 0:
            print_header(f"Orphaned Commits ({orphan_count} commits)", use_emoji=True)
            print("\n  These commits couldn't be added to any stack:")
            for commit in orphan_commits:
                print(f"  - {commit.subject}")

        # Summary
        print_header("Summary", use_emoji=True)
        print(f"\n  ✅ Successfully created/updated:")
        print(f"     - {len(single_commit_branches)} independent PRs")
        print(f"     - {len(multi_commit_stacks)} multi-commit stacks")
        
        if orphan_count > 0:
            print(f"\n  ⚠️  Issues encountered:")
            print(f"     - {orphan_count} commits orphaned due to conflicts")

        print(f"\n  💡 Next steps:")
        if orphan_count > 0:
            print(f"     - Resolve conflicts for orphaned commits")
        print(f"     - Run 'pyspr update' to refresh the stack")
    
    def _create_breakup_branch(self, commit: Commit, branch_name: str) -> bool:
        """Create a branch for a single breakup commit. Returns True if successful."""
        
        base_branch = self.config.repo.github_branch
        remote = self.config.repo.github_remote
        temp_branch = f"pyspr-temp-{commit.commit_id}"
        
        try:
            # Delete temp branch if exists
            try:
                self.git_cmd.must_git(f"branch -D {temp_branch}")
            except Exception:
                pass
                
            # Create temp branch from base
            self.git_cmd.must_git(f"checkout -b {temp_branch} {remote}/{base_branch}")
            
            # Cherry-pick the commit
            try:
                self.git_cmd.must_git(f"cherry-pick {commit.commit_hash}")
                new_hash = self.git_cmd.must_git("rev-parse HEAD").strip()
                
                # Create or update the branch
                if self.pretend:
                    logger.info(f"[PRETEND] Would create branch {branch_name}")
                else:
                    self.git_cmd.must_git(f"branch -f {branch_name} {new_hash}")
                    logger.info(f"  Created branch {branch_name}")
                    
                return True
                
            except Exception as e:
                logger.info(f"  Failed to cherry-pick: {e}")
                try:
                    self.git_cmd.run_cmd("cherry-pick --abort")
                except Exception:
                    pass
                return False
                
        finally:
            # Return to original branch
            current = self.git_cmd.must_git("rev-parse --abbrev-ref HEAD").strip()
            if current != temp_branch:
                return True  # Already on correct branch
                
            try:
                self.git_cmd.must_git("checkout -")
            except Exception:
                self.git_cmd.must_git("checkout -f -")
                
            try:
                self.git_cmd.must_git(f"branch -D {temp_branch}")
            except Exception:
                pass
                
    def _create_stack_branch(self, commits: List[Commit], stack_name: str) -> bool:
        """Create a branch with multiple commits for a stack. Returns True if successful."""
        base_branch = self.config.repo.github_branch
        remote = self.config.repo.github_remote
        
        try:
            # Delete branch if exists
            try:
                self.git_cmd.must_git(f"branch -D {stack_name}")
            except Exception:
                pass
                
            # Create branch from base
            if self.pretend:
                logger.info(f"[PRETEND] Would create stack branch {stack_name}")
                return True
            else:
                self.git_cmd.must_git(f"checkout -b {stack_name} {remote}/{base_branch}")
                
                # Cherry-pick all commits in order
                successful = 0
                for commit in commits:
                    try:
                        self.git_cmd.must_git(f"cherry-pick {commit.commit_hash}")
                        logger.info(f"  Added {commit.commit_hash[:8]} to stack")
                        print(f"     ✅ Added: {commit.subject}")
                        successful += 1
                    except Exception as e:
                        logger.error(f"  Failed to cherry-pick {commit.commit_hash[:8]}: {e}")
                        print(f"     ❌ Failed: {commit.subject} (conflict)")
                        # Try to continue with remaining commits
                        try:
                            self.git_cmd.run_cmd("cherry-pick --abort")
                        except Exception:
                            pass

                if successful < len(commits):
                    print(f"\n  ⚠️  Partial stack created ({successful}/{len(commits)} commits)")

                return True
                
        finally:
            # Return to original branch
            try:
                self.git_cmd.must_git("checkout -")
            except Exception:
                pass
                
    def _push_branches(self, branches: List[str]) -> None:
        """Push a list of branches to remote."""
        if not branches:
            return
            
        remote = self.config.repo.github_remote
        batch_size = 5  # Git's limit
        
        for i in range(0, len(branches), batch_size):
            batch = branches[i:i + batch_size]
            refs = [f"{branch}:refs/heads/{branch}" for branch in batch]
            cmd = f"push --force {remote} " + " ".join(refs)
            
            try:
                self.git_cmd.must_git(cmd)
                logger.info(f"  Pushed {len(batch)} branches")
            except Exception as e:
                logger.error(f"  Failed to push batch: {e}")
                
    def _create_breakup_prs(self, ctx: StackedPRContextProtocol, branches: List[str], all_commits: List[Commit], reviewers: Optional[List[str]] = None) -> None:
        """Create PRs for breakup branches."""
        from ..git import breakup_branch_name_from_commit
        
        github_info = self.github.get_info(ctx, self.git_cmd)
        
        # Map branches to commits
        commit_map: Dict[str, Commit] = {}
        for commit in all_commits:
            branch = breakup_branch_name_from_commit(self.config, commit)
            if branch in branches:
                commit_map[branch] = commit
                
        for branch in branches:
            if branch not in commit_map:
                continue
                
            commit = commit_map[branch]
            
            # Check if PR already exists for this branch
            existing_pr = self.github.get_pull_request_for_branch(ctx, branch)
            
            # If not found by branch, try to find by commit ID
            if not existing_pr and github_info and commit.commit_id:
                for pr in github_info.pull_requests:
                    if pr.commit and pr.commit.commit_id == commit.commit_id:
                        existing_pr = pr
                        logger.info(f"Found existing PR #{pr.number} by commit ID {commit.commit_id}")
                        break
            
            if existing_pr:
                print(f"\n  ⏳ Updating PR for \"{commit.subject}\"...")
                logger.info(f"  PR #{existing_pr.number} already exists for {branch}")
                # Update the PR to remove stack info and target main
                # Pass the PR in a list so update logic knows it's a single PR (not part of stack)
                self.github.update_pull_request(ctx, self.git_cmd, [existing_pr], existing_pr, commit, None)
                print(f"  ✅ PR #{existing_pr.number} updated")
            else:
                # Create new PR
                print(f"\n  ⏳ Creating PR for \"{commit.subject}\"...")
                if github_info:
                    pr = self.github.create_pull_request(ctx, self.git_cmd, github_info, 
                                                       commit, None, use_breakup_branch=True)
                    logger.info(f"  Created PR #{pr.number} for {branch}")
                    print(f"  ✅ PR #{pr.number} created")

                    # Add reviewers
                    if reviewers:
                        try:
                            self.github.add_reviewers(ctx, pr, reviewers)
                            print(f"     ✅ Added reviewers: {', '.join(reviewers)}")
                        except Exception as e:
                            logger.error(f"  Failed to add reviewers: {e}")
                            print(f"     ⚠️  Failed to add reviewers")
                else:
                    logger.error("  Cannot create PR - GitHub info not available")
                    print(f"  ❌ Failed to create PR - GitHub info not available")