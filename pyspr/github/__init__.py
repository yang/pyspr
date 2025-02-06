"""GitHub interfaces and implementation."""

import os
from dataclasses import dataclass
from typing import Dict, List, Optional
from github import Github
import re

from ..git import Commit, GitInterface

@dataclass
class PullRequest:
    """Pull request info."""
    number: int
    commit: Commit
    commits: List[Commit]
    base_ref: Optional[str] = None
    in_queue: bool = False
    body: str = ""
    title: str = ""

    def mergeable(self, config) -> bool:
        """Check if PR is mergeable."""
        return True # Simplified for minimal port

    def __str__(self) -> str:
        """Convert to string."""
        return f"PR #{self.number} - {self.commit.subject}"

@dataclass
class GitHubInfo:
    """GitHub repository info."""
    local_branch: str
    pull_requests: List[PullRequest]

    def key(self) -> str:
        """Get unique key for this info."""
        return self.local_branch

class GitHubInterface:
    """GitHub interface."""
    def get_info(self, ctx, git_cmd) -> Optional[GitHubInfo]:
        """Get GitHub info."""
        raise NotImplementedError()

    def create_pull_request(self, ctx, git_cmd, info, commit: Commit, prev_commit: Optional[Commit]) -> PullRequest:
        """Create pull request."""
        raise NotImplementedError()

    def update_pull_request(self, ctx, git_cmd, prs: List[PullRequest], 
                           pr: PullRequest, commit: Commit, prev_commit: Optional[Commit]):
        """Update pull request."""
        raise NotImplementedError()

    def add_reviewers(self, ctx, pr: PullRequest, user_ids: List[str]):
        """Add reviewers to pull request."""
        raise NotImplementedError()

    def comment_pull_request(self, ctx, pr: PullRequest, comment: str):
        """Comment on pull request."""
        raise NotImplementedError()

    def close_pull_request(self, ctx, pr: PullRequest):
        """Close pull request."""
        raise NotImplementedError()

    def get_assignable_users(self, ctx) -> List[Dict]:
        """Get assignable users."""
        raise NotImplementedError()
        
    def merge_pull_request(self, ctx, pr: PullRequest, merge_method: str):
        """Merge pull request."""
        raise NotImplementedError()

class GitHubClient(GitHubInterface):
    """GitHub client implementation."""
    def __init__(self, ctx, config):
        """Initialize with config."""
        self.config = config
        self.token = self._find_token()
        if not self.token:
            print("Error: No GitHub token found. Define GITHUB_TOKEN env var or put token in /home/ubuntu/code/pyspr/token file")
            return
        self.client = Github(self.token)
        self._repo = None

    def _find_token(self) -> Optional[str]:
        """Find GitHub token from file or env var."""
        # First try environment variable
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            return token

        # Then try token file
        token_file = "/home/ubuntu/code/pyspr/token"
        try:
            if os.path.exists(token_file):
                with open(token_file, "r") as f:
                    token = f.read().strip()
                    if token:
                        return token
        except Exception as e:
            print(f"Error reading token file: {e}")

    @property
    def repo(self):
        """Get GitHub repository."""
        if self._repo is None:
            owner = self.config.repo.get('github_repo_owner')
            name = self.config.repo.get('github_repo_name') 
            if owner and name:
                self._repo = self.client.get_repo(f"{owner}/{name}")
        return self._repo

    def get_info(self, ctx, git_cmd) -> Optional[GitHubInfo]:
        """Get GitHub info."""
        local_branch = git_cmd.must_git("rev-parse --abbrev-ref HEAD").strip()
        
        pull_requests = []
        if self.repo:
            spr_branch_pattern = r'^spr/[^/]+/([a-f0-9]{8})'
            open_prs = self.repo.get_pulls(state='open')
            for pr in open_prs:
                branch_match = re.match(spr_branch_pattern, pr.head.ref)
                if branch_match:
                    # Extract commit ID from branch name - matches Go behavior
                    commit_id = branch_match.group(1)
                    commit_hash = git_cmd.must_git(f"rev-parse {pr.head.sha}").strip()
                    # Get actual commit ID from commit message if possible
                    try:
                        body = git_cmd.must_git(f"show -s --format=%b {commit_hash}").strip()
                        msg_commit_id = re.search(r'commit-id:([a-f0-9]{8})', body)
                        if msg_commit_id:
                            commit_id = msg_commit_id.group(1)
                    except:
                        pass  # Keep ID from branch name if can't get from message
                    commit = Commit(commit_id, commit_hash, pr.title)
                    commits = [commit]  # Simplified, no commit history check
                    try:
                        in_queue = pr.auto_merge is not None
                    except:
                        in_queue = False
                    pull_requests.append(PullRequest(pr.number, commit, commits,
                                                    base_ref=pr.base.ref, in_queue=in_queue,
                                                    title=pr.title, body=pr.body))
                
        return GitHubInfo(local_branch, pull_requests)

    def create_pull_request(self, ctx, git_cmd, info, commit: Commit, prev_commit: Optional[Commit]) -> PullRequest:
        """Create pull request."""
        if not self.repo:
            raise Exception("GitHub repo not initialized - check token and repo owner/name config")
        branch_name = self.branch_name_from_commit(commit)
        
        # Find base branch - use prev_commit's branch if exists
        if prev_commit:
            base = self.branch_name_from_commit(prev_commit) 
        else:
            base = self.config.repo.get('github_branch', 'main')
        
        title = commit.subject
        commit.body = git_cmd.must_git(f"show -s --format=%b {commit.commit_hash}").strip()
        
        # Get current PR stack for interlinking
        current_prs = info.pull_requests[:] if info and info.pull_requests else []
        new_pr = PullRequest(0, commit, [commit], base_ref=base, title=title)
        current_prs.append(new_pr)  # Add new PR to stack for proper linking
        
        body = self.format_body(commit, current_prs)
        
        pr = self.repo.create_pull(title=title, body=body, head=branch_name, base=base)
        return PullRequest(pr.number, commit, [commit], base_ref=base, title=title, body=body)

    def update_pull_request(self, ctx, git_cmd, prs: List[PullRequest], 
                           pr: PullRequest, commit: Optional[Commit], prev_commit: Optional[Commit]):
        """Update pull request."""
        gh_pr = self.repo.get_pull(pr.number)
        
        # Debug print
        print(f"Debug PR #{pr.number}:")
        print(f"  Title: {gh_pr.title}")
        print(f"  Current base: {gh_pr.base.ref}")
        
        # Get fresh info from PR
        pr.title = gh_pr.title
        
        # Update title if needed and commit is provided 
        need_body_update = False
        if commit:
            commit.body = git_cmd.must_git(f"show -s --format=%b {commit.commit_hash}").strip()
            if gh_pr.title != commit.subject:
                gh_pr.edit(title=commit.subject)
                pr.title = commit.subject
                need_body_update = True
        
        # Update body with stack info if we have a commit
        if commit and (need_body_update or not pr.body):
            body = self.format_body(commit, prs)
            gh_pr.edit(body=body)
            pr.body = body

        # Update base branch to maintain stack, but not if in merge queue
        try:
            in_queue = gh_pr.auto_merge is not None
        except:
            in_queue = False

        if not in_queue:
            current_base = gh_pr.base.ref
            desired_base = None
            
            if prev_commit:
                desired_base = self.branch_name_from_commit(prev_commit)
                print(f"  Should target: {desired_base} (prev commit: {prev_commit.commit_hash[:8]})")
            else:
                desired_base = self.config.repo.get('github_branch', 'main')
                print("  Should target: main (no prev commit)")
                
            if current_base != desired_base:
                print(f"  Updating base from {current_base} to {desired_base}")
                gh_pr.edit(base=desired_base)

    def add_reviewers(self, ctx, pr: PullRequest, user_ids: List[str]):
        """Add reviewers to pull request."""
        gh_pr = self.repo.get_pull(pr.number)
        gh_pr.create_review_request(reviewers=user_ids)

    def comment_pull_request(self, ctx, pr: PullRequest, comment: str):
        """Comment on pull request."""
        gh_pr = self.repo.get_pull(pr.number)
        gh_pr.create_issue_comment(comment)

    def close_pull_request(self, ctx, pr: PullRequest):
        """Close pull request."""
        gh_pr = self.repo.get_pull(pr.number)
        gh_pr.edit(state="closed")

    def get_assignable_users(self, ctx) -> List[Dict]:
        """Get assignable users."""
        users = self.repo.get_assignees()
        return [{"login": u.login, "id": u.login} for u in users]

    def merge_pull_request(self, ctx, pr: PullRequest, merge_method: str):
        """Merge pull request using merge queue if configured."""
        gh_pr = self.repo.get_pull(pr.number)
        
        # Check if merge queue is enabled and supported for this repo
        merge_queue_enabled = self.config.repo.get('merge_queue', False)
        print(f"Merge queue enabled in config: {merge_queue_enabled}")
        
        if merge_queue_enabled:
            try:
                # Debug API info
                print("Pull request attributes available:")
                print(f"  auto_merge: {getattr(gh_pr, 'auto_merge', None)}")
                print(f"  mergeable: {gh_pr.mergeable}")
                print(f"  mergeable_state: {gh_pr.mergeable_state}")
                # Convert merge method to uppercase for PyGithub
                gh_method = merge_method.upper()
                # Try to enable auto-merge (merge queue)
                gh_pr.enable_automerge(merge_method=gh_method)
                print(f"PR #{pr.number} added to merge queue")
                return  # Success, we're done
            except Exception as e:
                print(f"Merge queue not supported or error: {e}")
                print(f"Error type: {type(e)}")
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
        lines = []
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