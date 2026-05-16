"""GitHub evidence fetcher module for Chesterton."""

import re
import os
import logging
from dataclasses import dataclass
from typing import Optional, List
from datetime import datetime
import requests
from git import Repo

# Import GitCommit from git_history.py
from .git_history import CodeTarget, GitCommit, GitEvidence

# Configure logging
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)


@dataclass
class GitHubIssue:
    """A GitHub issue or PR linked to the code."""
    number: int
    title: str
    url: str
    body: str
    comments: List[str]  # Comment bodies
    labels: List[str]
    created_at: datetime
    closed_at: Optional[datetime]


@dataclass
class GitHubEvidence:
    """GitHub context evidence."""
    linked_issues: List[GitHubIssue]
    linked_prs: List[GitHubIssue]  # PRs are issues in GitHub API
    commit_references: List[str]  # Issue/PR numbers mentioned in commits
    cross_repo_references: List[str]  # References to other repos (e.g., Werkzeug)


# Simple in-memory cache for fetch_issue results
# Key: (owner, name, number) -> GitHubIssue
_issue_cache = {}


def extract_issue_numbers(commit_messages: List[str]) -> List[int]:
    """
    Extract issue/PR numbers from commit messages.
    
    Patterns matched (case-insensitive):
    - #N
    - fixes #N
    - closes #N
    - GH-N
    - issue #N
    
    Returns de-duplicated list of integers, sorted.
    
    Example:
    >>> extract_issue_numbers(["ignore colon with slash when split app_import_path\\nFix issue #2961."])
    [2961]
    """
    issue_numbers = set()
    
    # Patterns to match issue references
    patterns = [
        r'#(\d+)',           # #123
        r'GH-(\d+)',         # GH-123
        r'issue\s+#(\d+)',   # issue #123
        r'fixes\s+#(\d+)',   # fixes #123
        r'closes\s+#(\d+)',  # closes #123
    ]
    
    for message in commit_messages:
        message_lower = message.lower()
        for pattern in patterns:
            matches = re.finditer(pattern, message_lower, re.IGNORECASE)
            for match in matches:
                issue_numbers.add(int(match.group(1)))
    
    return sorted(list(issue_numbers))


def fetch_issue(
    repo_owner: str,
    repo_name: str,
    issue_number: int,
    github_token: Optional[str] = None
) -> Optional[GitHubIssue]:
    """
    Fetch a single issue/PR with comments from GitHub API.
    
    Returns None if 404/403/network error (logs warning).
    Uses in-memory cache to avoid redundant API calls.
    """
    # Check cache first
    cache_key = (repo_owner, repo_name, issue_number)
    if cache_key in _issue_cache:
        return _issue_cache[cache_key]
    
    # Build API URL
    base_url = "https://api.github.com"
    issue_url = f"{base_url}/repos/{repo_owner}/{repo_name}/issues/{issue_number}"
    comments_url = f"{base_url}/repos/{repo_owner}/{repo_name}/issues/{issue_number}/comments"
    
    # Prepare headers
    headers = {}
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"
    
    try:
        # Fetch issue data
        response = requests.get(issue_url, headers=headers, timeout=10)
        
        # Handle rate limiting
        if response.status_code == 403 and "rate limit" in response.text.lower():
            logger.warning(
                f"GitHub API rate limit exceeded. Consider setting GITHUB_TOKEN environment variable. "
                f"Failed to fetch issue #{issue_number} from {repo_owner}/{repo_name}"
            )
            return None
        
        # Handle not found
        if response.status_code == 404:
            logger.warning(f"Issue #{issue_number} not found in {repo_owner}/{repo_name}")
            return None
        
        # Handle other errors
        if response.status_code != 200:
            logger.warning(
                f"Failed to fetch issue #{issue_number} from {repo_owner}/{repo_name}: "
                f"HTTP {response.status_code}"
            )
            return None
        
        issue_data = response.json()
        
        # Fetch comments
        comments = []
        try:
            comments_response = requests.get(comments_url, headers=headers, timeout=10)
            if comments_response.status_code == 200:
                comments_data = comments_response.json()
                for comment in comments_data:
                    body = comment.get("body", "")
                    # Truncate to 1000 chars
                    if len(body) > 1000:
                        body = body[:1000] + "...[truncated]"
                    comments.append(body)
        except requests.RequestException as e:
            logger.warning(f"Failed to fetch comments for issue #{issue_number}: {e}")
        
        # Parse issue data
        body = issue_data.get("body", "") or ""
        # Truncate body to 4000 chars
        if len(body) > 4000:
            body = body[:4000] + "...[truncated]"
        
        labels = [label["name"] for label in issue_data.get("labels", [])]
        
        created_at_str = issue_data.get("created_at", "")
        try:
            created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            created_at = datetime.now()
        
        closed_at = None
        closed_at_str = issue_data.get("closed_at")
        if closed_at_str:
            try:
                closed_at = datetime.fromisoformat(closed_at_str.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                pass
        
        github_issue = GitHubIssue(
            number=issue_data["number"],
            title=issue_data["title"],
            url=issue_data["html_url"],
            body=body,
            comments=comments,
            labels=labels,
            created_at=created_at,
            closed_at=closed_at
        )
        
        # Cache the result (only cache successful fetches)
        _issue_cache[cache_key] = github_issue
        
        return github_issue
    
    except requests.RequestException as e:
        logger.warning(f"Network error fetching issue #{issue_number} from {repo_owner}/{repo_name}: {e}")
        return None


def fetch_github_evidence(
    target: CodeTarget,
    git_evidence: GitEvidence,
    github_token: Optional[str] = None
) -> GitHubEvidence:
    """
    Fetch GitHub evidence by:
    1. Extracting issue/PR numbers from commit messages
    2. Fetching full issue/PR data via GitHub API
    3. Searching for cross-repo references in issue bodies
    
    Falls back gracefully if no token provided or API access fails.
    """
    # Warn if no token provided
    if not github_token:
        logger.warning(
            "No GitHub token provided. API rate limits apply (60 requests/hour). "
            "Set GITHUB_TOKEN environment variable for higher limits."
        )
    
    # Extract repo owner and name from git remote URL
    repo_owner, repo_name = _extract_repo_info(target.repo_path)
    if not repo_owner or not repo_name:
        logger.warning(f"Could not extract repo owner/name from {target.repo_path}")
        return GitHubEvidence(
            linked_issues=[],
            linked_prs=[],
            commit_references=[],
            cross_repo_references=[]
        )
    
    # Extract issue numbers from commit messages
    commit_messages = [commit.message for commit in git_evidence.commits]
    issue_numbers = extract_issue_numbers(commit_messages)
    
    # Fetch issues
    linked_issues = []
    for issue_number in issue_numbers:
        issue = fetch_issue(repo_owner, repo_name, issue_number, github_token)
        if issue:
            linked_issues.append(issue)
    
    # Populate commit_references with issue numbers as strings
    commit_references = [str(num) for num in issue_numbers]
    
    # Scan issue bodies for cross-repo references
    cross_repo_references = _extract_cross_repo_references(linked_issues)
    
    # For hackathon simplicity, treat all results as issues (PRs use same endpoint)
    return GitHubEvidence(
        linked_issues=linked_issues,
        linked_prs=[],  # Empty for now
        commit_references=commit_references,
        cross_repo_references=cross_repo_references
    )


def _extract_repo_info(repo_path: str) -> tuple[Optional[str], Optional[str]]:
    """
    Extract repo owner and name from git remote URL.
    
    Handles both:
    - https://github.com/owner/name.git
    - git@github.com:owner/name.git
    
    Returns (owner, name) or (None, None) if parsing fails.
    """
    try:
        repo = Repo(repo_path)
        
        # Get origin remote URL
        if "origin" not in repo.remotes:
            logger.warning(f"No 'origin' remote found in {repo_path}")
            return None, None
        
        remote_url = repo.remotes.origin.url
        
        # Parse HTTPS URL: https://github.com/owner/name.git
        https_match = re.match(r'https://github\.com/([^/]+)/([^/]+?)(?:\.git)?$', remote_url)
        if https_match:
            return https_match.group(1), https_match.group(2)
        
        # Parse SSH URL: git@github.com:owner/name.git
        ssh_match = re.match(r'git@github\.com:([^/]+)/([^/]+?)(?:\.git)?$', remote_url)
        if ssh_match:
            return ssh_match.group(1), ssh_match.group(2)
        
        logger.warning(f"Could not parse GitHub URL: {remote_url}")
        return None, None
    
    except Exception as e:
        logger.warning(f"Error extracting repo info from {repo_path}: {e}")
        return None, None


def _extract_cross_repo_references(issues: List[GitHubIssue]) -> List[str]:
    """
    Scan issue bodies for mentions of other pallets repos.
    
    Looks for: flask, werkzeug, jinja, click, quart
    Returns list of unique repo names mentioned (lowercase).
    """
    pallets_repos = {"flask", "werkzeug", "jinja", "click", "quart"}
    mentioned_repos = set()
    
    for issue in issues:
        # Scan body
        body_lower = issue.body.lower()
        for repo in pallets_repos:
            if repo in body_lower:
                mentioned_repos.add(repo)
        
        # Scan comments
        for comment in issue.comments:
            comment_lower = comment.lower()
            for repo in pallets_repos:
                if repo in comment_lower:
                    mentioned_repos.add(repo)
    
    return sorted(list(mentioned_repos))


if __name__ == "__main__":
    from dotenv import load_dotenv
    
    load_dotenv()
    
    # Demonstrate against Flask Case 2 (issue #2961)
    print("Fetching Flask issue #2961...")
    print("=" * 60)
    
    issue = fetch_issue("pallets", "flask", 2961, os.getenv("GITHUB_TOKEN"))
    
    if issue:
        print(f"Issue #{issue.number}: {issue.title}")
        print(f"URL: {issue.url}")
        print(f"Body (first 300 chars): {issue.body[:300]}")
        print(f"Comments: {len(issue.comments)}")
        print(f"Labels: {', '.join(issue.labels)}")
        print(f"Created: {issue.created_at}")
        if issue.closed_at:
            print(f"Closed: {issue.closed_at}")
    else:
        print("Failed to fetch issue.")

# Made with Bob
