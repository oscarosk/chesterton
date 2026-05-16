# Chesterton Architecture

## 1. Product Spec

Chesterton is a pre-deletion safety net that analyzes code before removal. A developer highlights a function, line range, or file they're about to delete. Chesterton performs multi-source archaeology—examining git history, GitHub issues/PRs, caller graphs, and test coverage—then synthesizes a verdict: what the code does, why it was added, what depends on it, what production incident it prevented, and a confidence-safe-to-delete score (0-100). Every claim is evidence-grounded with explicit citations. For high-stakes cases, Chesterton can invoke Bob Shell for deep-dive analysis. The tool prevents CVEs, cross-platform regressions, and subtle security hardening removals that look safe but aren't.

## 2. End-to-End Data Flow

1. **User Input** → [`cli.py`](src/chesterton/cli.py) or Streamlit UI
   - Produces: [`CodeTarget`](src/chesterton/cli.py) (file path, line range, repo path)

2. **Git History Analysis** → [`git_history.py`](src/chesterton/git_history.py)
   - Produces: [`GitEvidence`](src/chesterton/git_history.py) (commits, authors, messages, blame data)

3. **GitHub Context Fetch** → [`github_fetcher.py`](src/chesterton/github_fetcher.py)
   - Produces: [`GitHubEvidence`](src/chesterton/github_fetcher.py) (linked issues, PR discussions, cross-repo references)

4. **Caller Graph Analysis** → [`caller_graph.py`](src/chesterton/caller_graph.py)
   - Produces: [`CallerGraphEvidence`](src/chesterton/caller_graph.py) (call sites, dependency count, import chains)

5. **Test Detection** → [`test_detector.py`](src/chesterton/test_detector.py)
   - Produces: [`TestEvidence`](src/chesterton/test_detector.py) (test files, test names, assertions that would break)

6. **Evidence Synthesis** → [`synthesizer.py`](src/chesterton/synthesizer.py)
   - Consumes: All evidence structures
   - Produces: [`DeletionVerdict`](src/chesterton/synthesizer.py) (verdict text, confidence score, evidence citations, risk breakdown)

7. **Optional Deep Dive** → [`synthesizer.py`](src/chesterton/synthesizer.py) Bob Shell invocation
   - Trigger: Confidence < 70 OR user explicit request OR hero demo case
   - Produces: Enhanced [`DeletionVerdict`](src/chesterton/synthesizer.py) with deeper reasoning

8. **UI Display** → Streamlit panels
   - Renders: [`DeletionVerdict`](src/chesterton/synthesizer.py) with progressive disclosure

## 3. Core Data Structures

```python
from dataclasses import dataclass, field
from typing import Optional, List
from datetime import datetime

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

@dataclass
class CallSite:
    """A location where the target code is called."""
    file_path: str
    line_number: int
    function_name: str
    context_snippet: str  # 3 lines of context

@dataclass
class CallerGraphEvidence:
    """Caller graph and dependency evidence."""
    call_sites: List[CallSite]
    total_callers: int
    import_chains: List[str]  # Module import paths that reach this code
    is_public_api: bool  # Exported in __init__.py or similar
    is_unused: bool  # No callers found

@dataclass
class TestCase:
    """A test that exercises the target code."""
    file_path: str
    test_name: str
    line_number: int
    assertion_snippet: str  # The key assertion that would fail

@dataclass
class TestEvidence:
    """Test coverage evidence."""
    test_cases: List[TestCase]
    total_tests: int
    coverage_percentage: Optional[float]  # If available
    would_break_tests: bool

@dataclass
class EvidenceCitation:
    """A citation linking a verdict claim to its evidence source."""
    claim: str  # The claim being made
    source_type: str  # "git_history" | "github_issue" | "caller_graph" | "test"
    source_id: str  # Commit SHA, issue number, file path, etc.
    quote: str  # Direct quote from the evidence

@dataclass
class RiskBreakdown:
    """Breakdown of deletion risks by category."""
    security_risk: int  # 0-100
    compatibility_risk: int  # 0-100
    functionality_risk: int  # 0-100
    test_breakage_risk: int  # 0-100

@dataclass
class DeletionVerdict:
    """The final verdict on whether code is safe to delete."""
    target: CodeTarget
    
    # Core verdict
    summary: str  # 2-3 sentence summary
    what_it_does: str  # Functional description
    why_it_exists: str  # Historical reason for existence
    what_depends_on_it: str  # Dependency description
    incident_prevented: Optional[str]  # CVE, bug, or incident it prevents
    
    # Confidence and risk
    confidence_score: int  # 0-100, where 100 = definitely safe to delete
    risk_breakdown: RiskBreakdown
    
    # Evidence grounding
    citations: List[EvidenceCitation]
    evidence_quality: str  # "strong" | "moderate" | "weak"
    
    # Metadata
    analysis_timestamp: datetime
    used_deep_dive: bool  # Whether Bob Shell was invoked
    
    # Raw evidence (for UI drill-down)
    git_evidence: GitEvidence
    github_evidence: GitHubEvidence
    caller_evidence: CallerGraphEvidence
    test_evidence: TestEvidence
```

## 4. Module Contracts

### 4.1 [`git_history.py`](src/chesterton/git_history.py)

**Responsibility:** Extract git history evidence for target code using gitpython.

**Public Functions:**

```python
def analyze_git_history(target: CodeTarget) -> GitEvidence:
    """
    Analyze git history for the target code.
    
    Returns GitEvidence with:
    - All commits touching the target lines (git log -L)
    - Original commit that introduced the code (git blame)
    - All authors who modified these lines
    - Cross-repo history if code migrated (detected via commit messages)
    """

def find_original_commit(target: CodeTarget) -> Optional[GitCommit]:
    """Find the commit that first introduced this code."""

def detect_cross_repo_migration(commits: List[GitCommit]) -> Optional[str]:
    """
    Detect if code migrated from another repo.
    Searches commit messages for patterns like "moved from X" or "imported from Y".
    """
```

**External Resources:**
- gitpython library for git operations
- Local git repository at `target.repo_path`

**Critical Edge Case:**
Handle code that was moved/renamed within the repo. Use `git log --follow` to track file renames. If the file was renamed, the "original commit" might predate the current filename.

### 4.2 [`github_fetcher.py`](src/chesterton/github_fetcher.py)

**Responsibility:** Fetch GitHub issues, PRs, and discussions linked to the code.

**Public Functions:**

```python
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
    
    Uses PyGithub or requests. Falls back gracefully if no token provided.
    """

def extract_issue_numbers(commit_messages: List[str]) -> List[int]:
    """Extract issue/PR numbers from commit messages (e.g., #4485, GH-2961)."""

def fetch_issue(repo_owner: str, repo_name: str, issue_number: int) -> GitHubIssue:
    """Fetch a single issue/PR with comments."""
```

**External Resources:**
- GitHub REST API (rate limit: 60/hour unauthenticated, 5000/hour authenticated)
- Requires repo owner/name (extracted from git remote URL)

**Critical Edge Case:**
Handle private repos and missing tokens gracefully. If API access fails, return empty `GitHubEvidence` rather than crashing. Log the failure for user awareness but don't block the pipeline.

### 4.3 [`caller_graph.py`](src/chesterton/caller_graph.py)

**Responsibility:** Build caller graph using tree-sitter to find all call sites.

**Public Functions:**

```python
def analyze_caller_graph(target: CodeTarget) -> CallerGraphEvidence:
    """
    Build caller graph for target code:
    1. Parse target file to extract function/class names
    2. Search entire repo for call sites using tree-sitter
    3. Detect if code is part of public API (__init__.py exports)
    4. Build import chains showing how code is reached
    
    Supports Python and JavaScript grammars.
    """

def find_call_sites(
    function_name: str,
    repo_path: str,
    language: str = "python"
) -> List[CallSite]:
    """Find all locations where function_name is called."""

def is_public_api(target: CodeTarget) -> bool:
    """Check if code is exported in __init__.py or similar."""
```

**External Resources:**
- tree-sitter library with Python and JavaScript grammars
- File system access to scan entire repo

**Critical Edge Case:**
Handle dynamic calls and string-based imports (e.g., `getattr()`, `importlib.import_module()`). These won't appear in static analysis. Mark the evidence as "static analysis only" and note that dynamic calls may exist. For the demo cases, this is acceptable since they're all static calls.

### 4.4 [`test_detector.py`](src/chesterton/test_detector.py)

**Responsibility:** Find tests that exercise the target code.

**Public Functions:**

```python
def detect_tests(
    target: CodeTarget,
    caller_evidence: CallerGraphEvidence
) -> TestEvidence:
    """
    Find tests for target code:
    1. Search for test files (test_*.py, *_test.py)
    2. Check if any call sites are in test files
    3. Extract test function names and key assertions
    4. Estimate if deleting code would break tests
    
    Uses caller graph to find test call sites.
    """

def extract_test_assertions(test_file: str, test_name: str) -> str:
    """Extract the key assertion from a test function."""

def would_break_tests(test_cases: List[TestCase]) -> bool:
    """Determine if deleting code would break any tests."""
```

**External Resources:**
- File system access to read test files
- tree-sitter for parsing test files

**Critical Edge Case:**
Handle indirect test coverage. A test might not directly call the target code but call a function that calls it. Use the caller graph to detect this. Mark tests as "direct" or "indirect" coverage.

### 4.5 [`synthesizer.py`](src/chesterton/synthesizer.py)

**Responsibility:** Synthesize all evidence into a verdict using Granite LLM.

**Public Functions:**

```python
def synthesize_verdict(
    target: CodeTarget,
    git_evidence: GitEvidence,
    github_evidence: GitHubEvidence,
    caller_evidence: CallerGraphEvidence,
    test_evidence: TestEvidence,
    watsonx_api_key: str,
    watsonx_project_id: str
) -> DeletionVerdict:
    """
    Synthesize verdict using Granite LLM:
    1. Format all evidence into prompt
    2. Call Granite with structured JSON output schema
    3. Parse response into DeletionVerdict
    4. Validate that all claims have citations
    
    If confidence < 70 OR hero demo case, trigger deep dive.
    """

def invoke_deep_dive(
    target: CodeTarget,
    initial_verdict: DeletionVerdict,
    all_evidence: dict
) -> DeletionVerdict:
    """
    Invoke Bob Shell for deep dive analysis:
    1. Write evidence to temporary file
    2. Call Bob Shell subprocess with prompt
    3. Parse Bob's response
    4. Merge with initial verdict
    
    Returns enhanced verdict with used_deep_dive=True.
    """

def format_evidence_for_prompt(
    git_evidence: GitEvidence,
    github_evidence: GitHubEvidence,
    caller_evidence: CallerGraphEvidence,
    test_evidence: TestEvidence
) -> str:
    """Format all evidence into structured text for LLM prompt."""
```

**External Resources:**
- watsonx.ai Granite API (REST endpoint)
- Bob Shell subprocess (optional)
- Temporary file system for Bob Shell context

**Critical Edge Case:**
Handle LLM hallucination. Validate that every claim in the verdict has a corresponding citation. If a claim lacks a citation, mark it as "unverified" or remove it. The evidence_quality field should downgrade to "weak" if >20% of claims lack citations.

### 4.6 [`cli.py`](src/chesterton/cli.py)

**Responsibility:** CLI entry point and orchestration.

**Public Functions:**

```python
def main():
    """
    CLI entry point. Parses arguments and orchestrates the pipeline:
    1. Parse CLI args (file path, line range, repo path)
    2. Create CodeTarget
    3. Call each analysis module in sequence
    4. Call synthesizer
    5. Print verdict to stdout
    
    Usage: chesterton analyze <file> --lines <start>-<end> --repo <path>
    """

def parse_arguments() -> CodeTarget:
    """Parse CLI arguments into CodeTarget."""

def run_analysis_pipeline(target: CodeTarget) -> DeletionVerdict:
    """Run the full analysis pipeline and return verdict."""
```

**External Resources:**
- argparse for CLI parsing
- Environment variables for API keys (WATSONX_API_KEY, GITHUB_TOKEN)

**Critical Edge Case:**
Handle missing API keys gracefully. If WATSONX_API_KEY is missing, fail fast with clear error message. If GITHUB_TOKEN is missing, warn but continue with degraded GitHub evidence.

## 5. Granite Synthesizer Prompt Template

### System Prompt

```
You are Chesterton, a code archaeology expert. Your job is to analyze evidence about code that a developer wants to delete and produce a verdict on whether it's safe to delete.

You MUST ground every claim in evidence. Use these citation formats:
- "git history shows..." → cite commit SHA
- "issue #N discussion shows..." → cite issue number
- "the code is called from..." → cite file path and line number
- "test X would break..." → cite test file and name

If evidence is absent or weak, say so explicitly. Do not speculate beyond what the evidence supports.

Your verdict must be structured JSON matching this schema:
{
  "summary": "2-3 sentence summary of the verdict",
  "what_it_does": "Functional description of the code",
  "why_it_exists": "Historical reason for existence, citing git/GitHub evidence",
  "what_depends_on_it": "Description of dependencies, citing caller graph",
  "incident_prevented": "CVE, bug, or incident it prevents (null if none)",
  "confidence_score": 0-100 integer (100 = definitely safe to delete),
  "risk_breakdown": {
    "security_risk": 0-100,
    "compatibility_risk": 0-100,
    "functionality_risk": 0-100,
    "test_breakage_risk": 0-100
  },
  "citations": [
    {
      "claim": "The claim being made",
      "source_type": "git_history | github_issue | caller_graph | test",
      "source_id": "commit SHA | issue number | file path | test name",
      "quote": "Direct quote from evidence"
    }
  ],
  "evidence_quality": "strong | moderate | weak"
}

Confidence scoring guidelines:
- 90-100: No dependencies, no tests, trivial code, clear "dead code" evidence
- 70-89: Few dependencies, well-understood purpose, low risk
- 40-69: Multiple dependencies OR unclear purpose OR missing evidence
- 20-39: Security/compatibility implications OR high test breakage risk
- 0-19: CVE-class risk OR critical production code OR strong "do not delete" evidence

Risk breakdown guidelines:
- security_risk: 100 if code prevents CVE/exploit, 0 if no security implications
- compatibility_risk: 100 if platform-specific (Windows/Mac/Linux), 0 if platform-agnostic
- functionality_risk: Based on caller count and API surface
- test_breakage_risk: 100 if tests would break, 0 if no test coverage
```

### User Prompt Template

```
Analyze this code for deletion safety:

FILE: {target.file_path}
LINES: {target.start_line}-{target.end_line}
CODE:
```
{target.content}
```

=== GIT HISTORY EVIDENCE ===
{format_git_evidence(git_evidence)}

=== GITHUB EVIDENCE ===
{format_github_evidence(github_evidence)}

=== CALLER GRAPH EVIDENCE ===
{format_caller_evidence(caller_evidence)}

=== TEST EVIDENCE ===
{format_test_evidence(test_evidence)}

Produce your verdict as JSON matching the schema.
```

### Evidence Formatting Functions

```python
def format_git_evidence(evidence: GitEvidence) -> str:
    """
    Format:
    - Original commit: {sha} by {author} on {date}
      Message: {message}
      Diff: {diff_snippet}
    - Total commits touching this code: {total_commits}
    - Last modified: {last_modified}
    - All authors: {blame_authors}
    - Cross-repo history: {cross_repo_history if present}
    """

def format_github_evidence(evidence: GitHubEvidence) -> str:
    """
    Format:
    - Linked issues: {issue.number} - {issue.title}
      URL: {issue.url}
      Key discussion: {first 500 chars of body + top comment}
    - Linked PRs: {similar format}
    - Cross-repo references: {list}
    """

def format_caller_evidence(evidence: CallerGraphEvidence) -> str:
    """
    Format:
    - Total callers: {total_callers}
    - Is public API: {is_public_api}
    - Call sites:
      {file_path}:{line_number} in {function_name}
      Context: {context_snippet}
    - Import chains: {import_chains}
    """

def format_test_evidence(evidence: TestEvidence) -> str:
    """
    Format:
    - Total tests: {total_tests}
    - Would break tests: {would_break_tests}
    - Test cases:
      {test_name} in {file_path}:{line_number}
      Assertion: {assertion_snippet}
    """
```

### Required vs Optional Evidence

**Required fields (must be present in prompt):**
- `target.file_path`, `target.content`
- `git_evidence.commits` (can be empty list)
- `caller_evidence.total_callers`
- `test_evidence.total_tests`

**Optional fields (include if available):**
- `git_evidence.original_commit` (may be None if code is very old)
- `git_evidence.cross_repo_history` (only for cross-repo migrations)
- `github_evidence.linked_issues` (empty if no GitHub access)
- `github_evidence.linked_prs` (empty if no GitHub access)
- `caller_evidence.call_sites` (empty if no callers)
- `test_evidence.test_cases` (empty if no tests)

If optional evidence is missing, the prompt should say "No evidence available" for that section rather than omitting it entirely. This signals to the LLM that we looked but found nothing, rather than didn't look.

## 6. Deep Dive Mode Design

### Trigger Conditions

Bob Shell deep dive is invoked when **ANY** of these conditions are met:

1. **Low confidence:** `initial_verdict.confidence_score < 70`
2. **Hero demo case:** `target.file_path` contains "helpers.py" AND "send_from_directory" in `target.content`
3. **Explicit user request:** CLI flag `--deep-dive` or Streamlit button click

### Bob Shell Invocation

```python
def invoke_deep_dive(
    target: CodeTarget,
    initial_verdict: DeletionVerdict,
    all_evidence: dict
) -> DeletionVerdict:
    """
    Invoke Bob Shell for deep dive analysis.
    
    Process:
    1. Write context file to bob_sessions/context_{timestamp}.md
    2. Write prompt file to bob_sessions/prompt_{timestamp}.md
    3. Invoke: subprocess.run([
         "bob-shell",
         "--context", "bob_sessions/context_{timestamp}.md",
         "--prompt", "bob_sessions/prompt_{timestamp}.md",
         "--output", "bob_sessions/response_{timestamp}.md"
       ])
    4. Parse response_{timestamp}.md
    5. Merge Bob's insights with initial_verdict
    6. Return enhanced verdict with used_deep_dive=True
    """
```

### Context File Format

```markdown
# Chesterton Deep Dive Context

## Target Code
File: {target.file_path}
Lines: {target.start_line}-{target.end_line}

```{language}
{target.content}
```

## Initial Verdict
Confidence: {initial_verdict.confidence_score}/100
Summary: {initial_verdict.summary}

## Evidence Summary
{condensed version of all evidence}

## Uncertainty Areas
{list of claims with weak citations or missing evidence}
```

### Prompt File Format

```markdown
You are assisting Chesterton, a code deletion safety analyzer. The initial analysis produced a confidence score of {score}/100, which is below the threshold for a clear verdict.

Your task: Perform deeper reasoning about this code's deletion safety. Focus on:

1. **Cross-cutting concerns:** Are there subtle interactions this code has with other parts of the system that static analysis missed?

2. **Historical context:** What does the git history and issue discussion reveal about *why* this code exists? What problem was it solving?

3. **Risk assessment:** What's the worst-case scenario if this code is deleted? What production incident could occur?

4. **Evidence gaps:** What evidence is missing that would strengthen or weaken the deletion case?

Produce a structured analysis with:
- Enhanced "why it exists" explanation
- Specific incident scenarios if deleted
- Revised confidence score with justification
- Recommendations for the developer

Ground all claims in the provided evidence. If you need to speculate, mark it clearly as speculation.
```

### Response Parsing

Bob's response is parsed for:
- Enhanced `why_it_exists` text (replaces initial verdict)
- Enhanced `incident_prevented` text (replaces initial verdict)
- Revised `confidence_score` (if Bob provides one)
- Additional `citations` (extracted from Bob's references)

The final verdict merges Granite's structured output with Bob's deeper reasoning.

### Fallback Behavior

If Bob Shell invocation fails (subprocess error, timeout, missing binary):
1. Log the error
2. Return the initial Granite verdict unchanged
3. Set `used_deep_dive=False`
4. Add a note to the verdict: "Deep dive analysis was attempted but failed"

Do not block the user on Bob Shell availability. Granite-only verdicts are still valuable.

## 7. Streamlit UI Structure

### Page Layout

```
┌─────────────────────────────────────────────────────────┐
│ 🔍 Chesterton   Pre-Deletion Safety Net                 |
├─────────────────────────────────────────────────────────┤
│ [Panel 1: Input]                                        │
│   • File path input                                     │
│   • Line range input (start-end)                        │
│   • Repo path input (default: current directory)        │
│   • [Analyze] button                                    │
├─────────────────────────────────────────────────────────┤
│ [Panel 2: Code Preview] (appears after input)           │
│   • Syntax-highlighted code snippet                     │
│   • Line numbers                                        │
├─────────────────────────────────────────────────────────┤
│ [Panel 3: Verdict Summary] (appears after analysis)     │
│   • Confidence score (large, color-coded)               │
│     - 0-39: 🔴 Red "HIGH RISK"                          | 
│     - 40-69: 🟡 Yellow "MODERATE RISK"                  |
│     - 70-89: 🟢 Green "LOW RISK"                        │
│     - 90-100: ✅ "SAFE TO DELETE"                       │
│   • Summary text (2-3 sentences)                        │
│   • [Deep Dive] button (if confidence < 70)             │
├─────────────────────────────────────────────────────────┤
│ [Panel 4: Detailed Verdict] (expandable sections)       │
│   ▼ What It Does                                        │
│   ▼ Why It Exists (with git/GitHub citations)           │
│   ▼ What Depends On It (with caller graph)              │
│   ▼ Incident It Prevents (if applicable)                │
│   ▼ Risk Breakdown (4 bar charts)                       │
├─────────────────────────────────────────────────────────┤
│ [Panel 5: Evidence Explorer] (tabs)                     │
│   [Git History] [GitHub] [Callers] [Tests]              │
│   • Each tab shows raw evidence with citations          │
│   • Clickable links to GitHub issues/commits            │
├─────────────────────────────────────────────────────────┤
│ [Panel 6: Export] (bottom)                              │
│   • [Copy Verdict as Markdown] button                   │
│   • [Download Full Report] button (JSON)                │
└─────────────────────────────────────────────────────────┘
```

### Progressive Disclosure

Panels reveal in sequence:
1. Panel 1 (Input) is always visible
2. Panel 2 (Code Preview) appears when user enters file path
3. Panels 3-6 appear after "Analyze" button click and analysis completes
4. Panel 4 sections are collapsed by default, expand on click
5. Panel 5 tabs load lazily (don't fetch GitHub data until tab is clicked)

### Data Binding

```python
# Streamlit session state
st.session_state.target: CodeTarget
st.session_state.verdict: DeletionVerdict
st.session_state.analysis_running: bool

# Panel 1 binds to:
file_path = st.text_input("File path")
line_range = st.text_input("Line range (e.g., 100-150)")
repo_path = st.text_input("Repo path", value=os.getcwd())

# Panel 2 binds to:
st.code(st.session_state.target.content, language="python")

# Panel 3 binds to:
st.metric("Confidence Score", f"{verdict.confidence_score}/100")
st.write(verdict.summary)

# Panel 4 binds to:
with st.expander("What It Does"):
    st.write(verdict.what_it_does)
# ... similar for other sections

# Panel 5 binds to:
tab1, tab2, tab3, tab4 = st.tabs(["Git History", "GitHub", "Callers", "Tests"])
with tab1:
    for commit in verdict.git_evidence.commits:
        st.write(f"**{commit.sha[:7]}** by {commit.author}")
        st.write(commit.message)
# ... similar for other tabs
```

### Color Coding

- Confidence 0-39: `st.error()` with red background
- Confidence 40-69: `st.warning()` with yellow background
- Confidence 70-89: `st.success()` with green background
- Confidence 90-100: `st.success()` with checkmark icon

### Deep Dive Button

Appears in Panel 3 only if:
- `verdict.confidence_score < 70` OR
- User is viewing a demo case (detected by file path)

Button triggers:
1. Show spinner: "Running deep dive analysis..."
2. Call `invoke_deep_dive()`
3. Replace verdict in session state
4. Refresh Panel 3-6 with enhanced verdict
5. Show badge: "🔬 Deep Dive Analysis Applied"

## 8. Risk List

### Risk 1: Tree-sitter parsing failures on large repos
**Mitigation:** Set a timeout (30 seconds) for caller graph analysis. If timeout, return partial results with `is_unused=False` and a note "Analysis incomplete due to repo size."

### Risk 2: GitHub API rate limits during demo
**Mitigation:** Cache all GitHub responses locally in `bob_sessions/github_cache_{issue_number}.json`. Check cache before API call. For the three demo cases, pre-fetch and commit the cache files to the repo.

### Risk 3: Granite API latency or failures
**Mitigation:** Set a 60-second timeout on Granite API calls. If timeout or error, return a fallback verdict with `confidence_score=50`, `summary="Analysis incomplete due to API error"`, and `evidence_quality="weak"`. Log the error for debugging.

### Risk 4: Bob Shell subprocess hangs or crashes
**Mitigation:** Set a 120-second timeout on Bob Shell subprocess. Use `subprocess.run(timeout=120)`. If timeout, kill the process and return the initial Granite verdict unchanged. Mark `used_deep_dive=False` and add error note to verdict.

### Risk 5: Demo cases don't match expected file paths
**Mitigation:** Create a `demo_config.json` file mapping demo case names to exact file paths and line ranges in the Flask repo. The UI should have a "Load Demo Case" dropdown that auto-fills the input fields. Test all three demo cases in the Flask repo clone before the presentation.

---

## Implementation Notes

### Dependency Installation

```bash
pip install streamlit gitpython PyGithub tree-sitter tree-sitter-python tree-sitter-javascript requests
```

### Environment Variables

```bash
export WATSONX_API_KEY="your_key_here"
export WATSONX_PROJECT_ID="your_project_id"
export GITHUB_TOKEN="your_token_here"  # Optional but recommended
```

### Granite API Endpoint

```python
GRANITE_ENDPOINT = "https://us-south.ml.cloud.ibm.com/ml/v1/text/generation?version=2023-05-29"
GRANITE_MODEL = "ibm/granite-13b-chat-v2"

headers = {
    "Authorization": f"Bearer {watsonx_api_key}",
    "Content-Type": "application/json"
}

payload = {
    "model_id": GRANITE_MODEL,
    "input": prompt,
    "parameters": {
        "max_new_tokens": 2000,
        "temperature": 0.3,  # Low temperature for factual output
        "top_p": 0.9
    },
    "project_id": watsonx_project_id
}
```

### File Structure After Build

```
chesterton/
├── ARCHITECTURE.md (this file)
├── requirements.txt
├── demo_config.json (demo case mappings)
├── src/chesterton/
│   ├── __init__.py
│   ├── cli.py
│   ├── git_history.py
│   ├── github_fetcher.py
│   ├── caller_graph.py
│   ├── test_detector.py
│   └── synthesizer.py
├── bob_sessions/ (created at runtime)
│   ├── context_*.md
│   ├── prompt_*.md
│   ├── response_*.md
│   └── github_cache_*.json
├── streamlit_app.py (Streamlit entry point)
└── tests/
    └── test_*.py
```

### Streamlit Deployment

Deploy to Streamlit Cloud:
1. Push repo to GitHub
2. Connect Streamlit Cloud to repo
3. Set secrets in Streamlit Cloud dashboard:
   - `WATSONX_API_KEY`
   - `WATSONX_PROJECT_ID`
   - `GITHUB_TOKEN`
4. Main file: `streamlit_app.py`

---

## Design Decisions Log

**Why dataclasses over TypedDict?** Dataclasses provide better IDE support, default values, and post-init validation. They're more ergonomic for a 24-hour build.

**Why separate git_history and github_fetcher?** Git history is local and fast. GitHub fetching is remote and rate-limited. Separating them allows the pipeline to proceed even if GitHub access fails.

**Why tree-sitter over AST?** Tree-sitter handles syntax errors gracefully and supports multiple languages. Python's AST module would crash on syntax errors and only supports Python.

**Why Granite over GPT-4?** The hackathon provides $80 watsonx.ai credit. Granite is free for this use case. GPT-4 would require personal API spend.

**Why Bob Shell only for low confidence?** Bob Shell is slow (2-3 minutes per analysis). Using it for every analysis would make the demo unusable. Reserve it for cases where Granite is uncertain.

**Why progressive disclosure in UI?** The verdict is complex (8+ data fields). Showing everything at once is overwhelming. Progressive disclosure lets users drill down as needed.

**Why cache GitHub responses?** GitHub API has a 60/hour unauthenticated rate limit. During demo rehearsals, we'll hit this limit quickly. Caching prevents rate limit errors during the live demo.

**Why 70 as the deep dive threshold?** Confidence 70+ means "low risk" in the scoring guidelines. Below 70 is "moderate risk" or higher, which justifies the extra analysis time.

---

This architecture is locked. Build against it. Any ambiguities or edge cases not covered here: choose the simpler option and document it in code comments.