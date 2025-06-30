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
        # Only consider spr/main/* PRs as direct matches, not pyspr/cp/* PRs
        direct_matches: List[PullRequest] = []
        for commit in local_commits:
            logger.debug(f"  Checking commit {commit.commit_hash[:8]} with ID {commit.commit_id}")
            if commit.commit_id in pull_request_map:
                pr = pull_request_map[commit.commit_id]
                # Only include spr PRs in direct matches, not breakup PRs
                if pr.from_branch and not pr.from_branch.startswith('pyspr/cp/'):
                    direct_matches.append(pr)
                    logger.debug(f"  Found direct PR match #{pr.number} for commit {commit.commit_id}")
                else:
                    logger.debug(f"  Found breakup PR #{pr.number} for commit {commit.commit_id}, skipping")
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

    def breakup_pull_requests(self, ctx: StackedPRContextProtocol, reviewers: Optional[List[str]] = None, count: Optional[int] = None, commit_ids: Optional[List[str]] = None, stacks: bool = False) -> None:
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
                    # Update the PR if needed
                    if existing_pr.base_ref != base_branch:
                        if self.pretend:
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
        import os
        
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
            
        print_header("Commit Stack Analysis", use_emoji=True)
        print(f"\nAnalyzing {len(non_wip_commits)} commits for independent submission...")
        
        # Get the base branch from config
        base_branch = self.config.repo.github_branch
        remote = self.config.repo.github_remote
        
        # Results tracking
        independent_commits: List[Commit] = []
        dependent_commits: List[Tuple[Commit, str]] = []  # (commit, reason)
        
        # Create a temporary directory for patch testing
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            # Process each commit
            for i, commit in enumerate(non_wip_commits):
                logger.debug(f"Analyzing commit {i+1}/{len(non_wip_commits)}: {commit.commit_hash[:8]} {commit.subject}")
                
                # Create a subdirectory for this commit's patch
                commit_patch_dir = os.path.join(tmpdir, commit.commit_id)
                os.makedirs(commit_patch_dir, exist_ok=True)
                
                # Save current branch before testing patches
                current_branch = self.git_cmd.must_git("rev-parse --abbrev-ref HEAD").strip()
                
                # Generate patch for this commit
                try:
                    # Create patch from the commit
                    self.git_cmd.must_git(f"format-patch -1 {commit.commit_hash} -o {commit_patch_dir}")
                    
                    # Find the generated patch file (git format-patch creates numbered files)
                    patch_files = [f for f in os.listdir(commit_patch_dir) if f.endswith('.patch')]
                    if not patch_files:
                        dependent_commits.append((commit, "Failed to generate patch"))
                        continue
                    
                    # Test if patch applies cleanly to base branch
                    try:
                        
                        # Create a temporary test branch
                        test_branch = f"pyspr-analyze-test-{commit.commit_id}"
                        try:
                            self.git_cmd.must_git(f"branch -D {test_branch}")
                        except Exception:
                            pass  # Branch doesn't exist
                        
                        # Checkout base branch in detached HEAD to avoid modifying it
                        self.git_cmd.must_git(f"checkout -q {remote}/{base_branch}")
                        
                        # Try to apply the patch
                        patch_path = os.path.join(commit_patch_dir, patch_files[0])
                        try:
                            # Use --check to test without actually applying
                            self.git_cmd.must_git(f"apply --check {patch_path}")
                            independent_commits.append(commit)
                        except Exception as e:
                            # Patch doesn't apply cleanly
                            error_msg = str(e)
                            if "patch does not apply" in error_msg:
                                dependent_commits.append((commit, "Conflicts with base branch"))
                            else:
                                dependent_commits.append((commit, f"Cannot apply: {error_msg}"))
                        
                        # Return to original branch
                        self.git_cmd.must_git(f"checkout -q {current_branch}")
                        
                    except Exception as e:
                        logger.debug(f"Error testing patch: {e}")
                        dependent_commits.append((commit, f"Test failed: {str(e)}"))
                        # Make sure we're back on the original branch
                        try:
                            self.git_cmd.must_git(f"checkout -q {current_branch}")
                        except Exception:
                            pass
                        
                except Exception as e:
                    logger.debug(f"Error generating patch: {e}")
                    dependent_commits.append((commit, f"Failed to generate patch: {str(e)}"))
        
        # Print results
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
            for commit, reason in dependent_commits:
                print(f"   - {commit.commit_hash[:8]} {commit.subject}")
                print(f"     Reason: {reason}")
        else:
            print("   None")
        
        # Summary
        print("\nSummary:")
        print(f"  Total commits: {len(non_wip_commits)}")
        print(f"  Independent: {len(independent_commits)} ({len(independent_commits)*100//len(non_wip_commits) if non_wip_commits else 0}%)")
        print(f"  Dependent: {len(dependent_commits)} ({len(dependent_commits)*100//len(non_wip_commits) if non_wip_commits else 0}%)")
        
        if independent_commits:
            print(f"\nTip: You can use 'pyspr breakup' to create independent PRs for the {len(independent_commits)} independent commits.")
    
    def _analyze_commit_dependencies(self, commits: List[Commit]) -> Dict[str, List[str]]:
        """Analyze which commits depend on which other commits.
        
        Returns a dict mapping commit hash to list of commit hashes it depends on.
        """
        import tempfile
        import os
        
        dependencies: Dict[str, List[str]] = {}
        base_branch = self.config.repo.github_branch
        remote = self.config.repo.github_remote
        
        # Save current branch
        current_branch = self.git_cmd.must_git("rev-parse --abbrev-ref HEAD").strip()
        
        with tempfile.TemporaryDirectory() as tmpdir:
            for i, commit in enumerate(commits):
                dependencies[commit.commit_hash] = []
                
                # Generate patch for this commit
                patch_dir = os.path.join(tmpdir, commit.commit_id)
                os.makedirs(patch_dir, exist_ok=True)
                
                try:
                    self.git_cmd.must_git(f"format-patch -1 {commit.commit_hash} -o {patch_dir}")
                    patch_files = [f for f in os.listdir(patch_dir) if f.endswith('.patch')]
                    if not patch_files:
                        continue
                    
                    patch_path = os.path.join(patch_dir, patch_files[0])
                    
                    # Test if it applies to base
                    try:
                        self.git_cmd.must_git(f"checkout -q {remote}/{base_branch}")
                        self.git_cmd.must_git(f"apply --check {patch_path}")
                        # If it applies to base, it has no dependencies from our commits
                    except Exception:
                        # Doesn't apply to base, check against earlier commits
                        for j in range(i):
                            earlier_commit = commits[j]
                            try:
                                self.git_cmd.must_git(f"checkout -q {earlier_commit.commit_hash}")
                                self.git_cmd.must_git(f"apply --check {patch_path}")
                                # If it applies after this commit, it depends on it
                                dependencies[commit.commit_hash].append(earlier_commit.commit_hash)
                            except Exception:
                                pass
                            
                finally:
                    # Return to original branch
                    try:
                        self.git_cmd.must_git(f"checkout -q {current_branch}")
                    except Exception:
                        pass
                        
        return dependencies
    
    def _find_strongly_connected_components(self, commits: List[Commit], dependencies: Dict[str, List[str]]) -> List[List[Commit]]:
        """Find strongly connected components using Tarjan's algorithm."""
        # Build reverse dependencies (who depends on me)
        reverse_deps: Dict[str, List[str]] = {c.commit_hash: [] for c in commits}
        for commit_hash, deps in dependencies.items():
            for dep in deps:
                if dep in reverse_deps:
                    reverse_deps[dep].append(commit_hash)
        
        # Tarjan's algorithm
        index_counter = [0]
        stack: List[str] = []
        lowlinks: Dict[str, int] = {}
        index: Dict[str, int] = {}
        on_stack: Dict[str, bool] = {}
        sccs: List[List[str]] = []
        
        def strongconnect(v: str) -> None:
            index[v] = index_counter[0]
            lowlinks[v] = index_counter[0]
            index_counter[0] += 1
            stack.append(v)
            on_stack[v] = True
            
            # Check both forward and reverse dependencies for strong connectivity
            neighbors = set(dependencies.get(v, []) + reverse_deps.get(v, []))
            
            for w in neighbors:
                if w not in index:
                    strongconnect(w)
                    lowlinks[v] = min(lowlinks[v], lowlinks[w])
                elif on_stack.get(w, False):
                    lowlinks[v] = min(lowlinks[v], index[w])
                    
            if lowlinks[v] == index[v]:
                scc: List[str] = []
                while True:
                    w = stack.pop()
                    on_stack[w] = False
                    scc.append(w)
                    if w == v:
                        break
                sccs.append(scc)
                
        # Find all SCCs
        for commit in commits:
            if commit.commit_hash not in index:
                strongconnect(commit.commit_hash)
                
        # Convert back to commits and maintain order
        commit_map = {c.commit_hash: c for c in commits}
        result: List[List[Commit]] = []
        
        for scc in sccs:
            component: List[Commit] = []
            for hash in scc:
                if hash in commit_map:
                    component.append(commit_map[hash])
            
            if component:
                # Sort by original order
                component.sort(key=lambda c: next(i for i, x in enumerate(commits) if x.commit_hash == c.commit_hash))
                result.append(component)
                
        # Sort components by first commit position
        result.sort(key=lambda comp: next(i for i, x in enumerate(commits) if x.commit_hash == comp[0].commit_hash))
        
        return result
    
    def _breakup_into_stacks(self, ctx: StackedPRContextProtocol, commits: List[Commit], reviewers: Optional[List[str]] = None) -> None:
        """Break up commits into multiple PR stacks based on dependencies."""
        from ..pretty import print_header
        
        print_header("Multi-Stack Breakup Analysis", use_emoji=True)
        print(f"\nAnalyzing {len(commits)} commits for dependencies...")
        
        # Analyze dependencies
        dependencies = self._analyze_commit_dependencies(commits)
        
        # Find strongly connected components
        components = self._find_strongly_connected_components(commits, dependencies)
        
        print(f"\nFound {len(components)} component(s):")
        
        # Display components
        for i, component in enumerate(components):
            print(f"\nComponent {i+1} ({len(component)} commits):")
            for commit in component:
                deps = dependencies.get(commit.commit_hash, [])
                # Only show deps within the component
                internal_deps = [c.commit_id for c in component if c.commit_hash in deps]
                if internal_deps:
                    print(f"  - {commit.commit_hash[:8]} {commit.subject} (depends on: {', '.join(internal_deps)})")
                else:
                    print(f"  - {commit.commit_hash[:8]} {commit.subject}")
        
        # Get current branch
        current_branch = self.git_cmd.must_git("rev-parse --abbrev-ref HEAD").strip()
        
        # Process each component
        single_commit_branches: List[str] = []
        multi_commit_stacks: List[Tuple[str, List[Commit]]] = []  # (stack_branch, commits)
        
        for i, component in enumerate(components):
            if len(component) == 1:
                # Single commit - use regular breakup
                commit = component[0]
                branch_name = breakup_branch_name_from_commit(self.config, commit)
                print(f"\nProcessing single-commit component {i+1}: {commit.subject}")
                
                if self._create_breakup_branch(commit, branch_name):
                    single_commit_branches.append(branch_name)
            else:
                # Multiple commits - create a stack
                stack_name = f"pyspr/stack/{self.config.repo.github_branch}/component-{i+1}"
                print(f"\nProcessing multi-commit component {i+1} with {len(component)} commits")
                print(f"  Stack branch: {stack_name}")
                
                if self._create_stack_branch(component, stack_name):
                    multi_commit_stacks.append((stack_name, component))
        
        # Push branches and create PRs
        print(f"\n{'[PRETEND] Would push' if self.pretend else 'Pushing'} branches...")
        
        if not self.pretend:
            # Push single-commit branches
            if single_commit_branches:
                self._push_branches(single_commit_branches)
            
            # Push stack branches
            if multi_commit_stacks:
                stack_branches = [name for name, _ in multi_commit_stacks]
                self._push_branches(stack_branches)
        
        # Create PRs
        print(f"\n{'[PRETEND] Would create' if self.pretend else 'Creating'} pull requests...")
        
        if not self.pretend:
            # Create PRs for single commits
            if single_commit_branches:
                self._create_breakup_prs(ctx, single_commit_branches, commits, reviewers)
            
            # Create stacked PRs for multi-commit components
            for stack_branch, stack_commits in multi_commit_stacks:
                print(f"\nCreating PR stack for {stack_branch}...")
                # Switch to the stack branch and run update logic
                self.git_cmd.must_git(f"checkout {stack_branch}")
                try:
                    # Before running update, we need to check if there are existing PRs
                    # on pyspr/cp/ branches that we should reuse
                    existing_prs: Dict[str, PullRequest] = {}
                    for commit in stack_commits:
                        # Check for existing PR on breakup branch
                        breakup_branch = breakup_branch_name_from_commit(self.config, commit)
                        pr = self.github.get_pull_request_for_branch(ctx, breakup_branch)
                        if pr:
                            existing_prs[commit.commit_id] = pr
                            logger.info(f"Found existing PR #{pr.number} for commit {commit.commit_id} on branch {breakup_branch}")
                    
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
        
        # Summary
        print_header("Multi-Stack Breakup Complete", use_emoji=True)
        print(f"\nProcessed {len(components)} components:")
        print(f"  - Single-commit PRs: {len(single_commit_branches)}")
        print(f"  - Multi-commit stacks: {len(multi_commit_stacks)}")
        
        if multi_commit_stacks:
            print(f"\nCreated {len(multi_commit_stacks)} PR stack(s):")
            for i, (stack_branch, stack_commits) in enumerate(multi_commit_stacks):
                print(f"  Stack {i+1}: {stack_branch} ({len(stack_commits)} PRs)")
    
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
                for commit in commits:
                    try:
                        self.git_cmd.must_git(f"cherry-pick {commit.commit_hash}")
                        logger.info(f"  Added {commit.commit_hash[:8]} to stack")
                    except Exception as e:
                        logger.error(f"  Failed to cherry-pick {commit.commit_hash[:8]}: {e}")
                        # Try to continue with remaining commits
                        try:
                            self.git_cmd.run_cmd("cherry-pick --abort")
                        except Exception:
                            pass
                            
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
            
            # Check if PR already exists
            existing_pr = self.github.get_pull_request_for_branch(ctx, branch)
            
            if existing_pr:
                logger.info(f"  PR #{existing_pr.number} already exists for {branch}")
            else:
                # Create new PR
                if github_info:
                    pr = self.github.create_pull_request(ctx, self.git_cmd, github_info, 
                                                       commit, None, use_breakup_branch=True)
                    logger.info(f"  Created PR #{pr.number} for {branch}")
                    
                    # Add reviewers
                    if reviewers:
                        try:
                            self.github.add_reviewers(ctx, pr, reviewers)
                        except Exception as e:
                            logger.error(f"  Failed to add reviewers: {e}")
                else:
                    logger.error("  Cannot create PR - GitHub info not available")