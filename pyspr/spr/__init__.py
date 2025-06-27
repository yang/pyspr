"""Stacked PR implementation."""

import concurrent.futures
import sys
import re
import logging
from typing import Dict, List, Optional, TypedDict, cast, Sequence
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

from ..git import Commit, get_local_commit_stack, branch_name_from_commit, breakup_branch_name_from_commit, ConfigProtocol, GitInterface  
from ..github import GitHubInfo, PullRequest, GitHubInterface
from ..typing import StackedPRContextProtocol

class UpdateItem(TypedDict):
    """Type for update queue items."""
    pr: PullRequest
    commit: Optional[Commit]
    prev_commit: Optional[Commit]
    add_reviewers: Optional[List[str]]  # Track if reviewers should be added

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

            # Log config setting
            logger.debug(f"no_rebase config: {self.config.user.get('no_rebase', False)}")

            # Check for no-rebase from config
            no_rebase = self.config.user.get('no_rebase', False)
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

    def update_pull_requests(self, ctx: StackedPRContextProtocol, 
                         reviewers: Optional[List[str]] = None, 
                         count: Optional[int] = None,
                         labels: Optional[List[str]] = None) -> None:
        """Update pull requests for commits."""
        # Combine CLI labels with config labels
        config_labels: List[str] = []  # Initialize with empty list
        raw_labels = self.config.repo.get('labels', [])
        # Both checks needed because config can contain str, list, or other types
        # pyright: ignore[reportUnnecessaryIsInstance]
        if isinstance(raw_labels, str):
            config_labels = [raw_labels]
        elif isinstance(raw_labels, list):
            config_labels = cast(List[str], raw_labels)  # Trust user config
        # else case handled by initialization
            
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

        local_commits = all_local_commits

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
            
            # Get repo info for PR URLs
            owner = self.config.repo.get('github_repo_owner')
            name = self.config.repo.get('github_repo_name')
            
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
        branch = self.config.repo.get('github_branch_target', 'main')
        for pr in github_info.pull_requests:
            if pr.base_ref == branch:
                base_pr = pr
                break

        if not base_pr:
            return

        # Build stack from bottom up
        current_pr: Optional[PullRequest] = base_pr
        # TODO temp measure needed until we switch over to target
        branch = self.config.repo.get('github_branch', 'main')
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
        main_branch = self.config.repo.get('github_branch_target', 'main')
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

    def breakup_pull_requests(self, ctx: StackedPRContextProtocol, reviewers: Optional[List[str]] = None, count: Optional[int] = None) -> None:
        """Break up current commit stack into independent branches/PRs."""
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
            
        logger.info(f"Breaking up {len(non_wip_commits)} commits into independent branches/PRs")
        
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
        base_branch = self.config.repo.get('github_branch_target', self.config.repo.get('github_branch', 'main'))
        remote = self.config.repo.get('github_remote', 'origin')
        
        # Process each commit
        for i, commit in enumerate(non_wip_commits):
            branch_name = breakup_branch_name_from_commit(self.config, commit)
            logger.info(f"\nProcessing commit {i+1}/{len(non_wip_commits)}: {commit.subject}")
            logger.debug(f"  Commit hash: {commit.commit_hash}")
            logger.debug(f"  Branch name: {branch_name}")
            
            # Try to cherry-pick the commit onto the base branch
            try:
                # Create a temporary branch from the base
                temp_branch = f"pyspr-temp-{commit.commit_id}"
                
                # Delete the temp branch if it already exists from a previous failed run
                try:
                    self.git_cmd.must_git(f"branch -D {temp_branch}")
                except:
                    pass  # Branch doesn't exist, which is fine
                
                no_rebase = self.config.user.get('no_rebase', False) or self.config.get('no_rebase', False)
                if no_rebase:
                    # Use local base branch instead of remote
                    # First check if the local base branch exists
                    try:
                        self.git_cmd.must_git(f"rev-parse --verify {base_branch}")
                        self.git_cmd.must_git(f"checkout -b {temp_branch} {base_branch}")
                    except:
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
                    except:
                        branch_exists = False
                        existing_hash = None
                    
                    # Compare trees instead of commit hashes to detect actual content changes
                    if branch_exists:
                        # Get tree SHAs to compare actual content
                        existing_tree = self.git_cmd.must_git(f"rev-parse {existing_hash}^{{tree}}").strip()
                        new_tree = self.git_cmd.must_git(f"rev-parse {new_commit_hash}^{{tree}}").strip()
                        
                        if existing_tree != new_tree:
                            # Content has changed, update the branch
                            if self.pretend:
                                logger.info(f"[PRETEND] Would update branch {branch_name} from {existing_hash[:8]} to {new_commit_hash[:8]}")
                            else:
                                self.git_cmd.must_git(f"branch -f {branch_name} {new_commit_hash}")
                                logger.info(f"  Updated branch {branch_name}")
                        else:
                            # Content is identical, keep existing commit
                            logger.info(f"  Branch {branch_name} already up to date (same content)")
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
                    except:
                        pass
                        
            finally:
                # Always go back to original branch and clean up temp branch
                # Use force checkout to handle any uncommitted changes from cherry-pick
                try:
                    # First try regular checkout
                    self.git_cmd.must_git(f"checkout {current_branch}")
                except:
                    # If that fails due to uncommitted changes, force it
                    try:
                        self.git_cmd.must_git(f"checkout -f {current_branch}")
                    except:
                        # As a last resort, reset and then checkout
                        self.git_cmd.must_git("reset --hard HEAD")
                        self.git_cmd.must_git(f"checkout {current_branch}")
                
                try:
                    self.git_cmd.must_git(f"branch -D {temp_branch}")
                except:
                    pass
        
        # Push all created branches
        successfully_pushed = []
        failed_pushes = []
        
        if created_branches and not self.pretend:
            logger.info(f"\nPushing {len(created_branches)} branches to remote...")
            ref_names = []
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
        
        # Summary
        print_header("Breakup Summary", use_emoji=True)
        print(f"\nProcessed {len(non_wip_commits)} commits:")
        print(f"  ✅ Successfully created/updated: {len(created_branches)} branches")
        print(f"  ⏭️  Skipped (dependent commits): {len(skipped_commits)}")
        
        # Show push failures if any
        if not self.pretend and 'failed_pushes' in locals() and failed_pushes:
            merge_queue_failures = [b for b, e in failed_pushes if "has been added to a merge queue" in e]
            other_failures = [b for b, e in failed_pushes if "has been added to a merge queue" not in e]
            
            if merge_queue_failures:
                print(f"  ⚠️  In merge queue (not updated): {len(merge_queue_failures)}")
            if other_failures:
                print(f"  ❌ Failed to push: {len(other_failures)}")
        
        if created_prs:
            print(f"\nCreated/updated {len(created_prs)} pull requests:")
            owner = self.config.repo.get('github_repo_owner')
            name = self.config.repo.get('github_repo_name')
            for pr in created_prs:
                print(f"  PR #{pr.number}: {pr.title}")
                if owner and name:
                    print(f"    https://github.com/{owner}/{name}/pull/{pr.number}")
                    
        if skipped_commits:
            print(f"\nSkipped {len(skipped_commits)} commits that depend on earlier commits:")
            for commit in skipped_commits:
                print(f"  {commit.commit_hash[:8]} {commit.subject}")