"""GitHub interfaces and implementation."""

import os
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol, Literal
from github import Github
from github.Repository import Repository
import re

# Define merge method type
MergeMethod = Literal['merge', 'squash', 'rebase']

# Get module logger
logger = logging.getLogger(__name__)

# Import GraphQL response types from dedicated module
from .types import (
    GraphQLResponseType, GitHubRequester,
    parse_graphql_response
)

from ..git import Commit, GitInterface, ConfigProtocol
from ..typing import StackedPRContextProtocol, StackedPRContextType

@dataclass
class PullRequest:
    """Pull request info."""
    number: int
    commit: Commit
    commits: List[Commit]
    base_ref: Optional[str] = None
    from_branch: Optional[str] = None  # Added to match Go version's field name
    in_queue: bool = False
    body: str = ""
    title: str = ""
    merged: bool = False  # Added to track merge status

    def mergeable(self, config: ConfigProtocol) -> bool:
        """Check if PR is mergeable."""
        return True # Simplified for minimal port

    def __str__(self) -> str:
        """Convert to string."""
        queue_status = "⏳ in merge queue" if self.in_queue else ""
        return f"PR #{self.number} - {self.commit.subject} {queue_status}"

@dataclass
class GitHubInfo:
    """GitHub repository info."""
    local_branch: str
    pull_requests: List[PullRequest]

    def key(self) -> str:
        """Get unique key for this info."""
        return self.local_branch

class GitHubInterface(Protocol):
    """GitHub interface."""
    def get_info(self, ctx: StackedPRContextType, git_cmd: GitInterface) -> Optional[GitHubInfo]:
        """Get GitHub info."""
        ...

    def create_pull_request(self, ctx: StackedPRContextType, git_cmd: GitInterface, 
                           info: GitHubInfo, commit: Commit, prev_commit: Optional[Commit], 
                           labels: Optional[List[str]] = None, use_breakup_branch: bool = False) -> PullRequest:
        """Create pull request."""
        ...

    def update_pull_request(self, ctx: StackedPRContextType, git_cmd: GitInterface, 
                           prs: List[PullRequest], pr: PullRequest, commit: Optional[Commit], 
                           prev_commit: Optional[Commit], labels: Optional[List[str]] = None) -> None:
        """Update pull request."""
        ...

    def add_reviewers(self, ctx: StackedPRContextType, pr: PullRequest, user_ids: List[str]) -> None:
        """Add reviewers to pull request."""
        ...

    def comment_pull_request(self, ctx: StackedPRContextType, pr: PullRequest, comment: str) -> None:
        """Comment on pull request."""
        ...

    def close_pull_request(self, ctx: StackedPRContextType, pr: PullRequest) -> None:
        """Close pull request."""
        ...

    def get_assignable_users(self, ctx: StackedPRContextType) -> List[Dict[str, str]]:
        """Get assignable users."""
        ...
        
    def merge_pull_request(self, ctx: StackedPRContextType, pr: PullRequest, merge_method: MergeMethod) -> None:
        """Merge pull request."""
        ...
    
    def get_pull_request_for_branch(self, ctx: StackedPRContextType, branch_name: str) -> Optional[PullRequest]:
        """Get pull request for a specific branch."""
        ...

class GitHubClient:
    """GitHub client implementation."""
    def __init__(self, ctx: Optional[StackedPRContextProtocol], config: ConfigProtocol):
        """Initialize with config."""
        self.config = config
        self.token = self._find_token()
        if not self.token:
            logger.error("No GitHub token found. Try one of:\n1. Set GITHUB_TOKEN env var\n2. Log in with 'gh auth login'\n3. Put token in /home/ubuntu/code/pyspr/token file")
            return
        self.client = Github(self.token)
        self._repo: Optional[Repository] = None

    def _find_token(self) -> Optional[str]:
        """Find GitHub token from env var, gh CLI config, or token file."""
        import yaml
        from pathlib import Path

        # First try environment variable
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            return token

        # Then try gh CLI config at ~/.config/gh/hosts.yml
        try:
            gh_config_path = Path.home() / ".config" / "gh" / "hosts.yml"
            if gh_config_path.exists():
                with open(gh_config_path, "r") as f:
                    gh_config = yaml.safe_load(f)
                    if gh_config and "github.com" in gh_config:
                        github_config: Dict[str, Any] = gh_config["github.com"]
                        if "oauth_token" in github_config:
                            return github_config["oauth_token"]
        except Exception as e:
            logger.error(f"Error reading gh CLI config: {e}")

        # Finally try token file
        token_file = "/home/ubuntu/code/pyspr/token"
        try:
            if os.path.exists(token_file):
                with open(token_file, "r") as f:
                    token = f.read().strip()
                    if token:
                        return token
        except Exception as e:
            logger.error(f"Error reading token file: {e}")
        return None

    @property
    def repo(self) -> Optional[Repository]:
        """Get GitHub repository."""
        if self._repo is None:
            owner = self.config.repo.get('github_repo_owner')
            name = self.config.repo.get('github_repo_name') 
            if owner and name:
                self._repo = self.client.get_repo(f"{owner}/{name}")
        return self._repo

    def get_info(self, ctx: StackedPRContextType, git_cmd: GitInterface) -> Optional[GitHubInfo]:
        """Get GitHub info."""
        from ..git import get_local_commit_stack
        
        local_branch = git_cmd.must_git("rev-parse --abbrev-ref HEAD").strip()
        
        # Get local commits first to filter PRs
        local_commits = get_local_commit_stack(self.config, git_cmd)
        # Keep list of commit IDs for future filtering
        _ = {commit.commit_id for commit in local_commits}  # intentionally unused
        
        logger.debug("Local commit IDs:")
        for commit in local_commits:
            logger.debug(f"  {commit.commit_hash[:8]}: id={commit.commit_id}")
        
        if not self.repo:
            return GitHubInfo(local_branch, [])
            
        # Use GraphQL to efficiently get all data in one query, matching Go behavior
        query = """
        query Query($searchQuery: String!) {
          search(type:ISSUE,first:100,query:$searchQuery){
            pageInfo {
              hasNextPage
              endCursor
            }
            nodes{
              __typename
              ... on PullRequest {
                id
                number
                title
                body
                baseRefName
                headRefName
                mergeable
                reviewDecision
                repository {
                  id
                }
                commits(first: 100) {
                  nodes {
                    commit {
                      oid
                      messageHeadline
                      messageBody
                      statusCheckRollup {
                        state
                      }
                    }
                  }
                }
              }
            }
          }
        }
        """
        # Match both spr/main/ID and pyspr/cp/main/ID patterns
        spr_branch_pattern = r'^(?:spr|pyspr/cp)/[^/]+/([a-f0-9]{8})'
        
        logger.info(f"> github fetch pull requests")
        
        # Build PR map keyed by commit ID, like Go version
        pull_request_map: Dict[str, PullRequest] = {}
            
        try:
            # Execute GraphQL query directly like Go version does
            owner = self.config.repo.get('github_repo_owner')
            name = self.config.repo.get('github_repo_name')
            current_user = self.client.get_user().login.lower()
            search_query = f"author:{current_user} is:pr is:open repo:{owner}/{name} sort:updated-desc"
            target_branch = self.config.repo.get('github_branch_target', 'main')
            
            # Variables for GraphQL query
            variables = {
                "searchQuery": search_query
            }

            # Single query (no pagination for now)
            from typing import cast
            # Safely access private requester with typings
            any_client = cast(Any, self.client)  # First cast to Any to bypass attribute check
            req = cast(GitHubRequester, any_client._Github__requester)  # Then cast to our protocol
            
            # Use protocol-defined response type
            result: GraphQLResponseType = req.requestJsonAndCheck(
                "POST",
                "https://api.github.com/graphql", 
                input={
                    "query": query,
                    "variables": variables
                }
            )
            
            # Handle response - it's always a tuple of (headers, data)
            _headers, resp = result  # The response is always a tuple
            
            # Use Pydantic to parse and validate the response
            graphql_resp = parse_graphql_response(resp)
            
            # Get PR nodes using the validated model
            pr_nodes = graphql_resp.data.search.nodes
            
            logger.info(f"GraphQL returned {len(pr_nodes)} open PRs (newest first)")

            logger.debug("PRs returned by GraphQL:")
            for pr in pr_nodes:
                num = str(pr.number)
                base = pr.baseRefName
                head = pr.headRefName
                logger.debug(f"  PR #{num}: base={base} head={head}")
        
            # Process PRs into map
            for pr_data in pr_nodes:
                    branch_match = re.match(spr_branch_pattern, pr_data.headRefName)
                    if branch_match:
                        logger.debug(f"Processing PR #{pr_data.number} with branch {pr_data.headRefName}")
                        commit_id = branch_match.group(1)
                        
                        # Get commit info
                        commit_nodes = pr_data.commits.nodes
                        all_commits: List[Commit] = []  # Will be populated if there are commits 
                        commit: Optional[Commit] = None

                        if commit_nodes:
                            last_commit_data = commit_nodes[0]
                            last_commit = last_commit_data.commit
                            commit_hash = last_commit.oid
                            commit_msg = last_commit.messageBody
                            # Try to get commit ID from message
                            logger.debug(f"PR #{pr_data.number} last commit message:\n{commit_msg}")
                            msg_commit_id = re.search(r'commit-id:([a-f0-9]{8})', commit_msg)
                                
                            headline = last_commit.messageHeadline
                            commit = Commit.from_strings(commit_id, commit_hash, headline)

                            # Process all commits
                            for node in commit_nodes:
                                c = node.commit
                                c_msg = c.messageBody
                                c_id_match = re.search(r'commit-id:([a-f0-9]{8})', c_msg)
                                if c_id_match:
                                    c_id = c_id_match.group(1)
                                    c_oid = c.oid
                                    c_headline = c.messageHeadline
                                    all_commits.append(Commit.from_strings(c_id, c_oid, c_headline))
                            
                            # Get basic PR info from Pydantic model
                            number = pr_data.number
                            base_ref = pr_data.baseRefName
                            title = pr_data.title
                            body = pr_data.body
                            
                            in_queue = False  # Auto merge info not critical
                            
                            from_branch = pr_data.headRefName

                            # We know commit is initialized because we're in the commit_nodes block
                            assert commit is not None
                            pr = PullRequest(number, commit, all_commits,
                                          base_ref=base_ref, from_branch=from_branch,
                                          in_queue=in_queue, title=title, body=body)
                                          
                            # Add PR to map regardless of local commits to follow chain
                            logger.debug(f"Adding PR #{number} to map with commit ID {commit_id}")
                            pull_request_map[commit_id] = pr
            
        except Exception as e:
            logger.error(f"GraphQL query failed: {e}")
            logger.info("Falling back to REST API")
            
            # Fallback to REST API if GraphQL fails
            current_user = self.client.get_user().login
            repo = self.repo
            if not repo:
                return GitHubInfo(local_branch, [])
            open_prs = list(repo.get_pulls(state='open'))
            user_prs = [pr for pr in open_prs if pr.user.login == current_user]
            logger.info(f"Found {len(user_prs)} open PRs by {current_user} out of {len(open_prs)} total")
            
            for pr in user_prs:
                branch_match = re.match(spr_branch_pattern, str(pr.head.ref))
                if branch_match:
                    logger.debug(f"Processing PR #{pr.number} with branch {pr.head.ref}")
                    commit_id = branch_match.group(2)
                    commit_hash = pr.head.sha
                    all_commits: List[Commit] = []
                    try:
                        commits_in_pr = list(pr.get_commits())
                        if commits_in_pr:
                            last_commit = commits_in_pr[-1]
                            msg_commit_id = re.search(r'commit-id:([a-f0-9]{8})', str(last_commit.commit.message))
                            if msg_commit_id:
                                commit_id = msg_commit_id.group(1)
                            
                            # Get all commit IDs
                            for c in commits_in_pr:
                                c_msg = str(c.commit.message)
                                c_id_match = re.search(r'commit-id:([a-f0-9]{8})', c_msg)
                                if c_id_match:
                                    c_id = c_id_match.group(1)
                                    all_commits.append(Commit.from_strings(c_id, c.sha, c.commit.message.split('\n')[0]))
                    except Exception as e:
                        logger.error(f"Error getting commits for PR #{pr.number}: {e}")
                        pass

                    commit = Commit.from_strings(commit_id, commit_hash, pr.title)
                    try:
                        in_queue = False
                    except:
                        in_queue = False
                        
                    new_pr = PullRequest(pr.number, commit, all_commits,
                                     base_ref=pr.base.ref, from_branch=pr.head.ref,
                                     in_queue=in_queue,
                                     title=pr.title, body=pr.body)
                                     
                    # Add PR to map regardless of local commits to follow chain
                    logger.debug(f"Adding PR #{pr.number} to map with commit ID {commit_id}")
                    pull_request_map[commit_id] = new_pr

        # Build PR stack like Go version
        pull_requests: List[PullRequest] = []

        # Find top PR
        for commit in reversed(local_commits):
            curr_pr = pull_request_map.get(commit.commit_id)
            if curr_pr:
                logger.debug(f"Found PR #{curr_pr.number} with commit ID {commit.commit_id}")
                pull_requests.insert(0, curr_pr)

        logger.debug(f"Final PR stack has {len(pull_requests)} PRs")
        final_prs = list(pull_requests)  # Make copy to avoid type issues
        for pr in final_prs:
            logger.debug(f"  PR #{pr.number}: commit={pr.commit.commit_id} base={pr.base_ref}")
                
        return GitHubInfo(local_branch, final_prs)

    def create_pull_request(self, ctx: StackedPRContextType, git_cmd: GitInterface, info: GitHubInfo,
                         commit: Commit, prev_commit: Optional[Commit], 
                         labels: Optional[List[str]] = None, use_breakup_branch: bool = False) -> PullRequest:
        """Create pull request."""
        if not self.repo:
            raise Exception("GitHub repo not initialized - check token and repo owner/name config")
        
        # Use breakup branch name if requested
        if use_breakup_branch:
            remote_branch = self.config.repo.get('github_branch', 'main')
            branch_name = f"pyspr/cp/{remote_branch}/{commit.commit_id}"
        else:
            branch_name = self.branch_name_from_commit(commit)
        
        # Find base branch - use prev_commit's branch if exists
        if prev_commit:
            base = self.branch_name_from_commit(prev_commit) 
        else:
            base = self.config.repo.get('github_branch_target', 'main')
            
        logger.info(f"> github create #{info.pull_requests[-1].number + 1 if info.pull_requests else 1} : {commit.subject}")
        
        # Get full commit message including test tags
        commit_msg = git_cmd.must_git(f"show -s --format=%B {commit.commit_hash}").strip()
        title = commit.subject
        commit.body = commit_msg  # Preserve full commit message
        
        # Get current PR stack for interlinking
        current_prs: List[PullRequest] = info.pull_requests[:] if info and info.pull_requests else []
        logger.debug(f"Create PR - Current stack has {len(current_prs)} PRs")
        for i, pr in enumerate(current_prs):
            logger.debug(f"  #{i}: PR#{pr.number} - {pr.commit.commit_id}")
        new_pr = PullRequest(0, commit, [commit], base_ref=base, title=title)
        current_prs.append(new_pr)  # Add new PR to stack for proper linking
        logger.debug(f"Added new PR, stack now has {len(current_prs)} PRs")
        
        # Create PR first to get number
        try:
            pr = self.repo.create_pull(title=title, body="Creating...", head=branch_name, base=base)
            new_pr.number = pr.number  # Update number in stack
        except Exception as e:
            # Check if this is a "PR already exists" error
            error_msg = str(e)
            if "A pull request already exists" in error_msg:
                logger.warning(f"PR already exists for branch {branch_name}, attempting to find it")
                # Try to find the existing PR
                existing_pr = self.get_pull_request_for_branch(ctx, branch_name)
                if existing_pr:
                    logger.info(f"Found existing PR #{existing_pr.number} for branch {branch_name}")
                    # Remove the temp PR we added to current_prs
                    current_prs.pop()
                    # Update the existing PR instead
                    return self.update_pull_request(ctx, git_cmd, current_prs, existing_pr, commit, None)
                else:
                    logger.error(f"Could not find existing PR for branch {branch_name} even though GitHub says it exists")
                    raise
            else:
                # Re-raise other errors
                raise
        
        # Now format body with correct PR numbers
        body = self.format_body(commit, current_prs, is_breakup=use_breakup_branch)
        logger.debug(f"Formatted body:\n{body}")
        
        # Update PR with proper body
        pr.edit(body=body)

        # Add labels if provided
        if labels:
            logger.debug(f"Adding labels to PR #{pr.number}: {labels}")
            try:
                pr.add_to_labels(*labels)
                logger.info(f"> github add labels #{pr.number} : {labels}")
            except Exception as e:
                logger.error(f"Failed to add labels to PR #{pr.number}: {e}")
            
        return PullRequest(pr.number, commit, [commit], base_ref=base, title=title, body=body)

    def update_pull_request(self, ctx: StackedPRContextType, git_cmd: GitInterface, 
                           prs: List[PullRequest], pr: PullRequest,
                           commit: Optional[Commit], prev_commit: Optional[Commit], 
                           labels: Optional[List[str]] = None) -> None:
        """Update pull request."""
        if not self.repo:
            return
            
        logger.info(f"> github update #{pr.number} : {pr.title}")
            
        gh_pr = self.repo.get_pull(pr.number)
        
        # Debug info
        logger.debug(f"PR #{pr.number}:")
        logger.debug(f"  Title: {gh_pr.title}")
        logger.debug(f"  Current base: {gh_pr.base.ref}")
        
        # Get fresh info from PR
        pr.title = gh_pr.title
        
        # Update title if needed and commit is provided 
        if commit:
            # Get full commit message including test tags
            commit_msg = git_cmd.must_git(f"show -s --format=%B {commit.commit_hash}").strip()
            commit.body = commit_msg  # Preserve full commit message
            if gh_pr.title != commit.subject:
                gh_pr.edit(title=commit.subject)
                pr.title = commit.subject
        
        # Always update body with current stack info 
        if commit:
            # Check if this is a breakup PR by looking at the branch name
            is_breakup = pr.from_branch and pr.from_branch.startswith('pyspr/cp/')
            body = self.format_body(commit, prs, is_breakup=is_breakup)
            logger.debug(f"Updating body for PR #{pr.number}:\n{body}")
            gh_pr.edit(body=body)
            pr.body = body

        # Add labels if provided 
        if labels:
            logger.debug(f"Adding labels to PR #{pr.number}: {labels}")
            try:
                gh_pr.add_to_labels(*labels)
                logger.info(f"> github add labels #{pr.number} : {labels}")
            except Exception as e:
                logger.error(f"Failed to add labels to PR #{pr.number}: {e}")

        # Update base branch to maintain stack, but not if in merge queue or for breakup PRs
        # PyGithub typing is wrong; auto_merge exists but isn't in stubs
        try:
            # auto_merge is a GraphQL-only feature, use getattr
            in_queue = getattr(gh_pr, 'auto_merge', None) is not None
        except:
            in_queue = False

        # Check if this is a breakup PR by looking at the branch name
        is_breakup = pr.from_branch and pr.from_branch.startswith('pyspr/cp/')
        
        if not in_queue and not is_breakup:
            current_base = gh_pr.base.ref
            desired_base = None
            
            if prev_commit:
                desired_base = self.branch_name_from_commit(prev_commit)
                logger.debug(f"  Should target: {desired_base} (prev commit: {prev_commit.commit_hash[:8]})")
            else:
                desired_base = self.config.repo.get('github_branch_target', 'main')
                logger.debug(f"  Should target: {desired_base} (no prev commit)")
                
            if current_base != desired_base:
                logger.info(f"  Updating base from {current_base} to {desired_base}")
                gh_pr.edit(base=desired_base)

    def add_reviewers(self, ctx: StackedPRContextType, pr: PullRequest, user_ids: List[str]) -> None:
        """Add reviewers to pull request, filtering out self-reviews."""
        if not self.repo:
            return
            
        logger.info(f"> github add reviewers #{pr.number} : {pr.title} - {user_ids}")
            
        gh_pr = self.repo.get_pull(pr.number)
        current_user = self.client.get_user().login.lower()
        filtered_reviewers = [uid for uid in user_ids if uid.lower() != current_user]
        
        if not filtered_reviewers:
            logger.debug(f"No valid reviewers for PR #{pr.number} after filtering self-review")
            return
        
        try:
            # PyGithub typing is wrong; it actually accepts a list of strings
            gh_pr.create_review_request(reviewers=filtered_reviewers)  # type: ignore
        except Exception as e:
            logger.error(f"Failed to add reviewers to PR #{pr.number}: {e}")
            raise

    def comment_pull_request(self, ctx: StackedPRContextType, pr: PullRequest, comment: str) -> None:
        """Comment on pull request."""
        if not self.repo:
            return
            
        logger.info(f"> github add comment #{pr.number} : {pr.title}")
            
        gh_pr = self.repo.get_pull(pr.number)
        gh_pr.create_issue_comment(comment)

    def close_pull_request(self, ctx: StackedPRContextType, pr: PullRequest) -> None:
        """Close pull request."""
        if not self.repo:
            return
            
        logger.info(f"> github close #{pr.number} : {pr.title}")
            
        gh_pr = self.repo.get_pull(pr.number)
        # PyGithub's edit method accepts state parameter
        gh_pr.edit(state="closed")

    def get_assignable_users(self, ctx: StackedPRContextType) -> List[Dict[str, str]]:
        """Get assignable users."""
        if not self.repo:
            return []
            
        logger.info(f"> github get assignable users")
            
        users = self.repo.get_assignees()
        return [{"login": u.login, "id": u.login} for u in users]

    def merge_pull_request(self, ctx: StackedPRContextType, pr: PullRequest, merge_method: MergeMethod) -> None:
        """Merge pull request using merge queue if configured."""
        if not self.repo:
            return
        gh_pr = self.repo.get_pull(pr.number)
        
        # Check if merge queue is enabled and supported for this repo
        merge_queue_enabled = self.config.repo.get('merge_queue', False)
        logger.info(f"Merge queue enabled in config: {merge_queue_enabled}")
        
        if merge_queue_enabled:
            try:
                # Debug API info
                logger.debug("Pull request attributes available:")
                logger.debug(f"  auto_merge: {getattr(gh_pr, 'auto_merge', None)}")
                logger.debug(f"  mergeable: {gh_pr.mergeable}")
                logger.debug(f"  mergeable_state: {gh_pr.mergeable_state}")
                # Convert merge method to uppercase for PyGithub
                gh_method = merge_method.upper()
                # Try to enable auto-merge (merge queue)
                gh_pr.enable_automerge(merge_method=gh_method)
                msg = f"PR #{pr.number} added to merge queue"
                logger.info(msg)
                # For test compatibility
                print(msg)
                return  # Success, we're done
            except Exception as e:
                logger.warning(f"Merge queue not supported or error: {e}")
                logger.debug(f"Error type: {type(e)}")
                # If repository requires merge queue, don't fall back
                if "Changes must be made through the merge queue" in str(e):
                    raise Exception("Repository requires merge queue but failed to add PR to queue") from e
                # Fall back to regular merge only if merge queue is optional
                if merge_method == 'squash':
                    gh_pr.merge(merge_method='squash')
                elif merge_method == 'rebase':
                    gh_pr.merge(merge_method='rebase')
                else:
                    gh_pr.merge(merge_method='merge')
        else:
            # Regular merge
            if merge_method == 'squash':
                gh_pr.merge(merge_method='squash')
            elif merge_method == 'rebase':
                gh_pr.merge(merge_method='rebase')
            else:
                gh_pr.merge(merge_method='merge')

    def branch_name_from_commit(self, commit: Commit) -> str:
        """Generate branch name from commit. Matches Go implementation."""
        remote_branch = self.config.repo.get('github_branch', 'main')
        return f"spr/{remote_branch}/{commit.commit_id}"
        
    def format_stack_markdown(self, commit: Commit, stack: List[PullRequest]) -> str:
        """Format stack of PRs as markdown."""
        show_pr_titles = self.config.repo.get('show_pr_titles_in_stack', False)
        lines: List[str] = []
        # Reverse stack to match Go implementation (top to bottom)
        for pr in reversed(stack):
            is_current = pr.commit.commit_id == commit.commit_id
            suffix = " ⬅" if is_current else ""
            title_part = f"{pr.title} " if show_pr_titles and pr.title else ""
            lines.append(f"- {title_part}#{pr.number}{suffix}")
        return "\n".join(lines)

    def format_body(self, commit: Commit, stack: List[PullRequest], is_breakup: bool = False) -> str:
        """Format PR body with stack info."""
        body = commit.body if hasattr(commit, 'body') and commit.body else ""
        body = body.strip()

        # For breakup PRs, just return the body without stack info
        if is_breakup:
            return body

        if len(stack) <= 1:
            return body

        stack_markdown = self.format_stack_markdown(commit, stack)
        warning = ("\n\n⚠️ *Part of a stack created by [yang/pyspr](https://github.com/yang/pyspr), mostly written by Claude. " +
                  "Do not merge manually using the UI - doing so may have unexpected results.*")

        if not body:
            return f"**Stack**:\n{stack_markdown}{warning}"
        else:
            return f"{body}\n\n---\n\n**Stack**:\n{stack_markdown}{warning}"
    
    def get_pull_request_for_branch(self, ctx: StackedPRContextType, branch_name: str) -> Optional[PullRequest]:
        """Get pull request for a specific branch."""
        if not self.repo:
            logger.debug(f"No repo available for finding PR for branch {branch_name}")
            return None
            
        try:
            # Use the head filter to find PRs for this specific branch
            owner = self.config.repo.get('github_repo_owner')
            logger.debug(f"Searching for PR with branch {branch_name}, owner={owner}")
            
            # Search for open PRs with this branch as head
            head_filter = f"{owner}:{branch_name}"
            logger.debug(f"Using head filter: {head_filter}")
            pulls = self.repo.get_pulls(state='open', head=head_filter)
            
            # Convert to list to see all results
            pulls_list = list(pulls)
            logger.debug(f"GitHub API returned {len(pulls_list)} PRs for head filter {head_filter}")
            
            for pr in pulls_list:
                logger.debug(f"Checking PR #{pr.number}: head.ref={pr.head.ref}, base.ref={pr.base.ref}, state={pr.state}")
                if pr.head.ref == branch_name:
                    logger.debug(f"Found matching PR #{pr.number} for branch {branch_name}")
                    # Convert to our PullRequest type
                    commit = Commit(
                        commit_hash=pr.head.sha,
                        commit_id=pr.head.sha[:8],  # Use first 8 chars as ID
                        subject=pr.title,
                        body=pr.body or "",
                        wip=False
                    )
                    return PullRequest(
                        number=pr.number,
                        commit=commit,
                        commits=[commit],
                        base_ref=pr.base.ref,
                        from_branch=pr.head.ref,
                        in_queue=False,
                        body=pr.body or "",
                        title=pr.title,
                        merged=pr.merged
                    )
            logger.debug(f"No PR found for branch {branch_name} after checking {len(pulls_list)} PRs")
            return None
        except Exception as e:
            logger.error(f"Error getting PR for branch {branch_name}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None
