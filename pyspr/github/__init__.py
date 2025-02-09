"""GitHub interfaces and implementation."""

import os
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol
from github import Github
from github.Repository import Repository
import re

# Get module logger
logger = logging.getLogger(__name__)

# Import GraphQL response types from dedicated module
from .types import (
    GraphQLResponseType,
    cast_pr_nodes, safe_cast
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
                           info: GitHubInfo, commit: Commit, prev_commit: Optional[Commit]) -> PullRequest:
        """Create pull request."""
        ...

    def update_pull_request(self, ctx: StackedPRContextType, git_cmd: GitInterface, 
                           prs: List[PullRequest], pr: PullRequest, commit: Optional[Commit], 
                           prev_commit: Optional[Commit]) -> None:
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
        
    def merge_pull_request(self, ctx: StackedPRContextType, pr: PullRequest, merge_method: str) -> None:
        """Merge pull request."""
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
        query($owner: String!, $name: String!, $after: String) {
          viewer {
            login
          }
          repository(owner: $owner, name: $name) {
            pullRequests(first:100, states:[OPEN], orderBy: {field: CREATED_AT, direction: DESC}, after: $after) {
              pageInfo {
                hasNextPage
                endCursor
              }
              nodes {
                id
                number
                title
                body
                baseRefName
                headRefName
                mergeable
                commits(first:100) {
                  nodes {
                    commit {
                      oid
                      messageHeadline
                      messageBody
                    }
                  }
                }
              }
            }
          }
        }
        """
        
        spr_branch_pattern = r'^spr/[^/]+/([a-f0-9]{8})'
        
        logger.info(f"> github fetch pull requests")
        
        # Build PR map keyed by commit ID, like Go version
        pull_request_map: Dict[str, PullRequest] = {}
            
        try:
            # Execute GraphQL query directly like Go version does
            owner = self.config.repo.get('github_repo_owner')
            name = self.config.repo.get('github_repo_name')
            target_branch = self.config.repo.get('github_branch', 'main')
            
            # Variables for GraphQL query
            variables = {
                "owner": owner,
                "name": name,
            }

            # Single query (no pagination for now)
            result: GraphQLResponseType = self.client._Github__requester.requestJsonAndCheck(  # type: ignore
                "POST",
                "https://api.github.com/graphql",
                input={
                    "query": query, 
                    "variables": variables
                }
            )
            
            # Handle response structure correctly
            resp: Dict[str, Any]
            if isinstance(result, tuple):
                try:
                    # Directly unpack to avoid len() call
                    _, resp_data = result  # type: ignore
                    resp = safe_cast(resp_data, dict)
                except ValueError:
                    # Not a 2-tuple, try to cast whole result
                    resp = safe_cast(result, dict)
            else:
                resp = safe_cast(result, dict)
                
            # Check if we have any data at all
            if 'data' not in resp:
                raise Exception("No data in GraphQL response")
                
            data = safe_cast(resp.get('data', {}), dict)
            # Keep login for future user filtering
            _ = safe_cast(safe_cast(data.get('viewer', {}), dict).get('login', ''), str)  # intentionally unused
            pr_data = safe_cast(safe_cast(data.get('repository', {}), dict).get('pullRequests', {}), dict)
            pr_nodes = cast_pr_nodes(pr_data.get('nodes', []))
            
            logger.info(f"GraphQL returned {len(pr_nodes)} open PRs (newest first)")

            logger.debug("PRs returned by GraphQL:")
            for pr in safe_cast(pr_nodes, list):
                try:
                    num = str(pr.get('number', '?'))
                    base = str(pr.get('baseRefName', '?')) 
                    head = str(pr.get('headRefName', '?'))
                    logger.debug(f"  PR #{num}: base={base} head={head}")
                except Exception:
                    logger.debug("  [invalid PR data]")
            
            # Process PRs into map
            for pr_data in safe_cast(pr_nodes, list):
                branch_match = re.match(spr_branch_pattern, str(pr_data['headRefName']))
                if branch_match:
                    logger.debug(f"Processing PR #{pr_data['number']} with branch {pr_data['headRefName']}")
                    commit_id = branch_match.group(1)
                    
                    # Get commit info
                    pr_commits = safe_cast(pr_data['commits'], dict)
                    commit_nodes = safe_cast(pr_commits.get('nodes', []), list)
                    all_commits: List[Commit] = []  # Will be populated if there are commits 
                    commit: Optional[Commit] = None

                    if commit_nodes:
                        commit_data = safe_cast(commit_nodes[-1], dict)
                        last_commit = safe_cast(commit_data.get('commit', {}), dict)
                        commit_hash = safe_cast(last_commit.get('oid', ''), str)
                        commit_msg = safe_cast(last_commit.get('messageBody', ''), str)
                        # Try to get commit ID from message
                        logger.debug(f"PR #{pr_data['number']} last commit message:\n{commit_msg}")
                        msg_commit_id = re.search(r'commit-id:([a-f0-9]{8})', str(commit_msg))
                        if msg_commit_id:
                            message_id = msg_commit_id.group(1)
                            logger.debug(f"Found commit ID {message_id} in message")
                            logger.debug(f"Branch name commit ID: {commit_id}")
                            commit_id = message_id
                            
                        headline = safe_cast(last_commit.get('messageHeadline', ''), str)
                        commit = Commit(commit_id, commit_hash, headline)

                        # Process all commits
                        for node in commit_nodes:
                            c_data = safe_cast(node, dict)
                            c = safe_cast(c_data.get('commit', {}), dict)
                            c_msg = safe_cast(c.get('messageBody', ''), str)
                            c_id_match = re.search(r'commit-id:([a-f0-9]{8})', c_msg)
                            if c_id_match:
                                c_id = c_id_match.group(1)
                                c_oid = safe_cast(c.get('oid', ''), str)
                                c_headline = safe_cast(c.get('messageHeadline', ''), str)
                                all_commits.append(Commit(c_id, c_oid, c_headline))
                        
                        # Get basic PR info 
                        number = safe_cast(pr_data['number'], int)
                        base_ref = safe_cast(pr_data['baseRefName'], str)
                        title = safe_cast(pr_data['title'], str)
                        body = safe_cast(pr_data['body'], str)
                        
                        in_queue = False  # Auto merge info not critical
                        
                        from_branch = safe_cast(pr_data['headRefName'], str)

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
                    commit_id = branch_match.group(1)
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
                                    all_commits.append(Commit(c_id, c.sha, c.commit.message.split('\n')[0]))
                    except Exception as e:
                        logger.error(f"Error getting commits for PR #{pr.number}: {e}")
                        pass

                    commit = Commit(commit_id, commit_hash, pr.title)
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
        target_branch = self.config.repo.get('github_branch', 'main')
        
        # Find top PR
        curr_pr: Optional[PullRequest] = None
        for commit in reversed(local_commits):
            curr_pr = pull_request_map.get(commit.commit_id)
            if curr_pr:
                logger.debug(f"Found top PR #{curr_pr.number} with commit ID {commit.commit_id}")
                break
                
        # Build stack
        while curr_pr:
            curr = curr_pr  # Make a local copy that's definitely not None for insert
            pull_requests.insert(0, curr)  # Prepend like Go
            logger.debug(f"Added PR #{curr_pr.number} to stack - base: {curr_pr.base_ref}")
            if curr_pr.base_ref == target_branch:
                logger.debug("Reached target branch, stopping")
                break
                
            # Get next commit ID from base branch
            match = re.match(r'spr/[^/]+/([a-f0-9]{8})', str(curr_pr.base_ref))
            if not match:
                logger.debug(f"Base is {curr_pr.base_ref} which doesn't match pattern, stopping")
                break
            next_commit_id = match.group(1)
            curr_pr = pull_request_map.get(next_commit_id)
            if not curr_pr:
                logger.debug(f"No PR found for commit {next_commit_id}, stopping")
                break
                
        logger.debug(f"Final PR stack has {len(pull_requests)} PRs")
        final_prs = list(pull_requests)  # Make copy to avoid type issues
        for pr in final_prs:
            logger.debug(f"  PR #{pr.number}: commit={pr.commit.commit_id} base={pr.base_ref}")
                
        return GitHubInfo(local_branch, final_prs)

    def create_pull_request(self, ctx: StackedPRContextType, git_cmd: GitInterface, info: GitHubInfo,
                         commit: Commit, prev_commit: Optional[Commit]) -> PullRequest:
        """Create pull request."""
        if not self.repo:
            raise Exception("GitHub repo not initialized - check token and repo owner/name config")
        branch_name = self.branch_name_from_commit(commit)
        
        # Find base branch - use prev_commit's branch if exists
        if prev_commit:
            base = self.branch_name_from_commit(prev_commit) 
        else:
            base = self.config.repo.get('github_branch', 'main')
            
        logger.info(f"> github create #{info.pull_requests[-1].number + 1 if info.pull_requests else 1} : {commit.subject}")
        
        title = commit.subject
        commit.body = git_cmd.must_git(f"show -s --format=%b {commit.commit_hash}").strip()
        
        # Get current PR stack for interlinking
        current_prs: List[PullRequest] = info.pull_requests[:] if info and info.pull_requests else []
        logger.debug(f"Create PR - Current stack has {len(current_prs)} PRs")
        for i, pr in enumerate(current_prs):
            logger.debug(f"  #{i}: PR#{pr.number} - {pr.commit.commit_id}")
        new_pr = PullRequest(0, commit, [commit], base_ref=base, title=title)
        current_prs.append(new_pr)  # Add new PR to stack for proper linking
        logger.debug(f"Added new PR, stack now has {len(current_prs)} PRs")
        
        # Create PR first to get number
        pr = self.repo.create_pull(title=title, body="Creating...", head=branch_name, base=base)
        new_pr.number = pr.number  # Update number in stack
        
        # Now format body with correct PR numbers
        body = self.format_body(commit, current_prs)
        logger.debug(f"Formatted body:\n{body}")
        
        # Update PR with proper body
        pr.edit(body=body)
        return PullRequest(pr.number, commit, [commit], base_ref=base, title=title, body=body)

    def update_pull_request(self, ctx: StackedPRContextType, git_cmd: GitInterface, 
                           prs: List[PullRequest], pr: PullRequest,
                           commit: Optional[Commit], prev_commit: Optional[Commit]) -> None:
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
            commit.body = git_cmd.must_git(f"show -s --format=%b {commit.commit_hash}").strip()
            if gh_pr.title != commit.subject:
                gh_pr.edit(title=commit.subject)
                pr.title = commit.subject
        
        # Always update body with current stack info 
        if commit:
            body = self.format_body(commit, prs)
            logger.debug(f"Updating body for PR #{pr.number}:\n{body}")
            gh_pr.edit(body=body)
            pr.body = body

        # Update base branch to maintain stack, but not if in merge queue
        # PyGithub typing is wrong; auto_merge exists but isn't in stubs
        try:
            in_queue = getattr(gh_pr, 'auto_merge', None) is not None  # type: ignore
        except:
            in_queue = False

        if not in_queue:
            current_base = gh_pr.base.ref
            desired_base = None
            
            if prev_commit:
                desired_base = self.branch_name_from_commit(prev_commit)
                logger.debug(f"  Should target: {desired_base} (prev commit: {prev_commit.commit_hash[:8]})")
            else:
                desired_base = self.config.repo.get('github_branch', 'main')
                logger.debug("  Should target: main (no prev commit)")
                
            if current_base != desired_base:
                logger.info(f"  Updating base from {current_base} to {desired_base}")
                gh_pr.edit(base=desired_base)

    def add_reviewers(self, ctx: StackedPRContextType, pr: PullRequest, user_ids: List[str]) -> None:
        """Add reviewers to pull request."""
        if not self.repo:
            return
            
        logger.info(f"> github add reviewers #{pr.number} : {pr.title} - {user_ids}")
            
        gh_pr = self.repo.get_pull(pr.number)
        # PyGithub typing is wrong; it actually accepts a list of strings
        gh_pr.create_review_request(reviewers=user_ids)  # type: ignore
        logger.debug(f"Called add_reviewers for PR #{pr.number} with IDs: {user_ids}")

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
        gh_pr.edit(state="closed")  # type: ignore  # PyGithub typing wrong; state is valid

    def get_assignable_users(self, ctx: StackedPRContextType) -> List[Dict[str, str]]:
        """Get assignable users."""
        if not self.repo:
            return []
            
        logger.info(f"> github get assignable users")
            
        users = self.repo.get_assignees()
        return [{"login": u.login, "id": u.login} for u in users]

    def merge_pull_request(self, ctx: StackedPRContextType, pr: PullRequest, merge_method: str) -> None:
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
                logger.debug(f"  auto_merge: {getattr(gh_pr, 'auto_merge', None)}")  # type: ignore
                logger.debug(f"  mergeable: {gh_pr.mergeable}")
                logger.debug(f"  mergeable_state: {gh_pr.mergeable_state}")
                # Convert merge method to uppercase for PyGithub
                gh_method = merge_method.upper()
                # Try to enable auto-merge (merge queue)
                gh_pr.enable_automerge(merge_method=gh_method)  # type: ignore
                logger.info(f"PR #{pr.number} added to merge queue")
                return  # Success, we're done
            except Exception as e:
                logger.warning(f"Merge queue not supported or error: {e}")
                logger.debug(f"Error type: {type(e)}")
                # If repository requires merge queue, don't fall back
                if "Changes must be made through the merge queue" in str(e):
                    raise Exception("Repository requires merge queue but failed to add PR to queue") from e
                # Fall back to regular merge only if merge queue is optional
                if merge_method == 'squash':
                    gh_pr.merge(merge_method='squash')  # type: ignore
                elif merge_method == 'rebase':
                    gh_pr.merge(merge_method='rebase')  # type: ignore
                else:
                    gh_pr.merge(merge_method='merge')  # type: ignore
        else:
            # Regular merge
            if merge_method == 'squash':
                gh_pr.merge(merge_method='squash')  # type: ignore
            elif merge_method == 'rebase':
                gh_pr.merge(merge_method='rebase')  # type: ignore
            else:
                gh_pr.merge(merge_method='merge')  # type: ignore

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

    def format_body(self, commit: Commit, stack: List[PullRequest]) -> str:
        """Format PR body with stack info."""
        body = commit.body if hasattr(commit, 'body') and commit.body else ""
        body = body.strip()

        if len(stack) <= 1:
            return body

        stack_markdown = self.format_stack_markdown(commit, stack)
        warning = ("\n\n⚠️ *Part of a stack created by [spr](https://github.com/ejoffe/spr). " +
                  "Do not merge manually using the UI - doing so may have unexpected results.*")

        if not body:
            return f"**Stack**:\n{stack_markdown}{warning}"
        else:
            return f"{body}\n\n---\n\n**Stack**:\n{stack_markdown}{warning}"
