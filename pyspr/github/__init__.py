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

class GitHubClient(GitHubInterface):
    """GitHub client implementation."""
    def __init__(self, ctx, config):
        """Initialize with config."""
        self.config = config
        self.token = os.environ.get("GITHUB_TOKEN")
        self.client = Github(self.token)
        self._repo = None

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
                    pull_requests.append(PullRequest(pr.number, commit, commits))
                
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
        body = git_cmd.must_git(f"show -s --format=%b {commit.commit_hash}").strip()
        
        pr = self.repo.create_pull(title=title, body=body, head=branch_name, base=base)
        return PullRequest(pr.number, commit, [commit])

    def update_pull_request(self, ctx, git_cmd, prs: List[PullRequest], 
                           pr: PullRequest, commit: Commit, prev_commit: Optional[Commit]):
        """Update pull request."""
        gh_pr = self.repo.get_pull(pr.number)
        
        # Debug print
        print(f"Debug PR #{pr.number}:")
        print(f"  Title: {gh_pr.title}")
        print(f"  Current base: {gh_pr.base.ref}")
        
        # Update title if needed
        if gh_pr.title != commit.subject:
            gh_pr.edit(title=commit.subject)

        # Update base branch to maintain stack
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

    def branch_name_from_commit(self, commit: Commit) -> str:
        """Generate branch name from commit. Matches Go implementation."""
        remote_branch = self.config.repo.get('github_branch', 'main')
        return f"spr/{remote_branch}/{commit.commit_id}"