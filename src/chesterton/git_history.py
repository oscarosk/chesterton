"""Git history analysis module for Chesterton."""

import re
import os
from dataclasses import dataclass, field
from typing import Optional, List
from datetime import datetime
from git import Repo, GitCommandError


@dataclass
class CodeTarget:
    """The code the user wants to delete."""
    file_path: str  # Relative to repo root
    start_line: int
    end_line: int
    repo_path: str  # Absolute path to git repo
    content: str  # The actual code snippet


@dataclass
class GitCommit:
    """A single git commit touching the target code."""
    sha: str
    author: str
    date: datetime
    message: str
    diff_snippet: str  # The relevant diff lines


@dataclass
class GitEvidence:
    """Git history evidence for the target code."""
    commits: List[GitCommit]
    original_commit: Optional[GitCommit]  # The commit that introduced this code
    blame_authors: List[str]  # All authors who touched these lines
    total_commits: int
    last_modified: datetime
    cross_repo_history: Optional[str] = None  # For code that migrated repos


def analyze_git_history(target: CodeTarget) -> GitEvidence:
    """Analyze git history for the target code using git log -L and git blame."""
    try:
        repo = Repo(target.repo_path)
        
        # Check if file exists
        full_path = os.path.join(target.repo_path, target.file_path)
        if not os.path.exists(full_path):
            raise FileNotFoundError(f"Target file does not exist: {target.file_path}")
        
        # Get commits touching the target lines using git log -L
        commits = _get_line_history(repo, target)
        
        # Find the original commit that introduced this code
        original_commit = find_original_commit(target)
        
        # Get blame authors
        blame_authors = _get_blame_authors(repo, target)
        
        # Detect cross-repo migration
        cross_repo_history = detect_cross_repo_migration(commits)
        
        # Get last modified date
        last_modified = commits[0].date if commits else datetime.now()
        
        return GitEvidence(
            commits=commits,
            original_commit=original_commit,
            blame_authors=blame_authors,
            total_commits=len(commits),
            last_modified=last_modified,
            cross_repo_history=cross_repo_history
        )
    
    except GitCommandError as e:
        # Log warning and return empty evidence
        print(f"Warning: Git command failed: {e}")
        return GitEvidence(
            commits=[],
            original_commit=None,
            blame_authors=[],
            total_commits=0,
            last_modified=datetime.now(),
            cross_repo_history=None
        )

def find_original_commit(target: CodeTarget) -> Optional[GitCommit]:
    """Find the commit that first introduced this code (oldest commit in git log -L)."""
    try:
        repo = Repo(target.repo_path)
        log_output = repo.git.log(
            "-L", f"{target.start_line},{target.end_line}:{target.file_path}"
        )
    except GitCommandError:
        return None
    if not log_output:
        return None

    # Parse commit blocks. Each block starts with "commit <40-hex>".
    import re
    commit_blocks = re.split(r"(?=^commit [0-9a-f]{40})", log_output, flags=re.MULTILINE)
    commit_blocks = [b for b in commit_blocks if b.startswith("commit ")]
    if not commit_blocks:
        return None

    # Oldest commit is the last block (git log returns newest-first).
    oldest_block = commit_blocks[-1]

    sha_match = re.search(r"^commit ([0-9a-f]{40})", oldest_block, re.MULTILINE)
    author_match = re.search(r"^Author:\s*(.+?)\s*<", oldest_block, re.MULTILINE)
    date_match = re.search(r"^Date:\s*(.+)$", oldest_block, re.MULTILINE)

    if not sha_match:
        return None

    # Extract the commit message (lines after Date: and before the diff).
    lines = oldest_block.splitlines()
    msg_lines = []
    in_msg = False
    for line in lines:
        if line.startswith("Date:"):
            in_msg = True
            continue
        if in_msg:
            if line.startswith("diff "):
                break
            msg_lines.append(line.strip())
    message = " ".join(l for l in msg_lines if l).strip()

    from datetime import datetime
    date_str = date_match.group(1).strip() if date_match else ""
    try:
        date = datetime.strptime(date_str, "%a %b %d %H:%M:%S %Y %z")
    except (ValueError, TypeError):
        date = datetime.now()

    return GitCommit(
        sha=sha_match.group(1),
        author=author_match.group(1) if author_match else "Unknown",
        date=date,
        message=message,
        diff_snippet="",
    )


def detect_cross_repo_migration(commits: List[GitCommit]) -> Optional[str]:
    """
    Detect if code migrated from another repo.
    
    Searches commit messages for patterns like "moved to X" or "imported from Y".
    Fixed regex to capture the correct word (e.g., "Werkzeug" not "send_file").
    
    Example commit message: "move send_file and send_from_directory to Werkzeug"
    Should capture: "Werkzeug"
    """
    # Patterns for detecting cross-repo migration
    # Using .+? (non-greedy) to skip over function names and capture the repo name
    patterns = [
        r"moved? .+? to (\w+)",  # "move send_file to Werkzeug"
        r"migrated .+? to (\w+)",  # "migrated helpers to Werkzeug"
        r"transferred .+? to (\w+)",
        r"relocated .+? to (\w+)",
        r"now in (\w+)",
        # Reverse direction patterns
        r"imported .+? from (\w+)",  # "imported from Werkzeug"
        r"moved? from (\w+)",  # "moved from Flask"
        r"migrated from (\w+)",
        r"brought .+? from (\w+)",
    ]
    
    for commit in commits:
        message_lower = commit.message.lower()
        
        # Try each pattern
        for pattern in patterns:
            match = re.search(pattern, message_lower, re.IGNORECASE)
            if match:
                repo_name = match.group(1).capitalize()
                return f"Code migrated to/from {repo_name} in commit {commit.sha[:7]}"
    
    return None


def _get_line_history(repo: Repo, target: CodeTarget) -> List[GitCommit]:
    """Get all commits that touched the target lines using git log -L."""
    try:
        # Use git log -L to get commits touching specific lines
        # Format: -L start,end:file
        # Pass -L as separate argument for proper parsing
        # Note: --follow is incompatible with -L, so we don't use it
        log_output = repo.git.log(
            "-L", f"{target.start_line},{target.end_line}:{target.file_path}"
        )
        
        if not log_output:
            return []
        
        commits = []
        lines = log_output.split('\n')
        
        # Parse git log -L output which has commit lines starting with commit SHA
        # Format: "commit <sha>" or just "<sha>" (7-40 hex chars at line start)
        current_commit = None
        current_author = None
        current_date = None
        current_message = None
        diff_lines = []
        
        for line in lines:
            # Check if this is a commit boundary (line starts with hex SHA)
            commit_match = re.match(r'^commit ([0-9a-f]{7,40})', line)
            if commit_match:
                # Save previous commit if exists
                if current_commit:
                    commits.append(GitCommit(
                        sha=current_commit,
                        author=current_author or "Unknown",
                        date=current_date or datetime.now(),
                        message=current_message or "",
                        diff_snippet='\n'.join(diff_lines[:20])
                    ))
                
                # Start new commit
                current_commit = commit_match.group(1)
                current_author = None
                current_date = None
                current_message = None
                diff_lines = []
            elif line.startswith('Author:'):
                current_author = line.split(':', 1)[1].strip()
            elif line.startswith('Date:'):
                date_str = line.split(':', 1)[1].strip()
                try:
                    current_date = datetime.strptime(date_str, '%a %b %d %H:%M:%S %Y %z')
                except:
                    current_date = datetime.now()
            elif current_commit and not current_message and line.strip() and not line.startswith('diff') and not line.startswith('@@'):
                # This is the commit message
                current_message = line.strip()
            elif line.startswith('@@') or line.startswith('+') or line.startswith('-') or line.startswith(' '):
                # This is part of the diff
                diff_lines.append(line)
        
        # Don't forget the last commit
        if current_commit:
            commits.append(GitCommit(
                sha=current_commit,
                author=current_author or "Unknown",
                date=current_date or datetime.now(),
                message=current_message or "",
                diff_snippet='\n'.join(diff_lines[:20])
            ))
        
        return commits
    
    except GitCommandError:
        return []


def _get_commit_diff(repo: Repo, sha: str, file_path: str) -> str:
    """Get the diff snippet for a specific commit and file."""
    try:
        # Get diff for this commit
        diff = repo.git.show(sha, "--", file_path, unified=3)
        
        # Extract just the diff lines (skip commit metadata)
        lines = diff.split('\n')
        diff_lines = []
        in_diff = False
        
        for line in lines:
            if line.startswith('@@'):
                in_diff = True
            if in_diff:
                diff_lines.append(line)
                if len(diff_lines) >= 20:  # Limit to 20 lines
                    break
        
        return '\n'.join(diff_lines)
    
    except GitCommandError:
        return ""


def _get_blame_authors(repo: Repo, target: CodeTarget) -> List[str]:
    """Get all unique authors who touched the target lines."""
    try:
        blame_output = repo.git.blame(
            target.file_path,
            L=f"{target.start_line},{target.end_line}"
        )
        
        # Extract author names from blame output
        authors = set()
        for line in blame_output.split('\n'):
            # Blame format: SHA (Author Name YYYY-MM-DD ...) code
            match = re.search(r'\(([^)]+?)\s+\d{4}-\d{2}-\d{2}', line)
            if match:
                author = match.group(1).strip()
                authors.add(author)
        
        return sorted(list(authors))
    
    except GitCommandError:
        return []


if __name__ == "__main__":
    # Demo against Flask Case 1: Path Traversal Protection
    # This demonstrates the module against the hero case from demo_cases.md
    
    target = CodeTarget(
        file_path="src/werkzeug/security.py",
        start_line=11,  # _os_alt_seps constant
        end_line=12,
        repo_path=r"C:\Users\Oscar\chesterton\werkzeug",
        content=(
            '_os_alt_seps: list[str] = list(\n'
            '    sep for sep in [os.sep, os.altsep] if sep is not None and sep != "/"\n'
            ')'
        ),
    )
    
    print("Analyzing Case 1: Path Traversal Protection (Werkzeug, originally Flask 2010)")
    print("=" * 60)
    print(f"File: {target.file_path}")
    print(f"Lines: {target.start_line}-{target.end_line}")
    print()
    
    evidence = analyze_git_history(target)
    
    print(f"Total commits: {evidence.total_commits}")
    print(f"Last modified: {evidence.last_modified}")
    print(f"Blame authors: {', '.join(evidence.blame_authors)}")
    print()
    
    if evidence.original_commit:
        print("Original commit:")
        print(f"  SHA: {evidence.original_commit.sha[:7]}")
        print(f"  Author: {evidence.original_commit.author}")
        print(f"  Date: {evidence.original_commit.date}")
        print(f"  Message: {evidence.original_commit.message}")
        print()
    
    if evidence.cross_repo_history:
        print(f"Cross-repo history: {evidence.cross_repo_history}")
        print()
    
    print("Recent commits:")
    for i, commit in enumerate(evidence.commits[:5]):
        print(f"{i+1}. {commit.sha[:7]} by {commit.author}")
        print(f"   {commit.message}")
        print()

# Made with Bob
