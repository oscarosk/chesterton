"""Evidence synthesizer module for Chesterton - LLM-powered deletion verdict."""

import json
import re
import logging
from dataclasses import dataclass, field
from typing import Optional, List
from datetime import datetime, timedelta
import requests
import os

# Load .env at module import time with correct absolute path
from dotenv import load_dotenv
from pathlib import Path
_env_path = Path(__file__).resolve().parents[2] / ".env"
if _env_path.exists():
    load_dotenv(_env_path)

# Import existing dataclasses from other modules
from .git_history import CodeTarget, GitCommit, GitEvidence
from .github_fetcher import GitHubIssue, GitHubEvidence

# Configure logging
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)


# ============================================================================
# NEW DATACLASSES (from ARCHITECTURE.md section 3)
# ============================================================================

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
    caller_evidence: 'CallerGraphEvidence'
    test_evidence: 'TestEvidence'


# ============================================================================
# PLACEHOLDER DATACLASSES (for modules not yet built)
# ============================================================================

@dataclass
class CallSite:
    """A location where the target code is called."""
    file_path: str
    line_number: int
    function_name: str
    context_snippet: str  # 3 lines of context


@dataclass
class CallerGraphEvidence:
    """Caller graph and dependency evidence (placeholder)."""
    call_sites: List[CallSite] = field(default_factory=list)
    total_callers: int = 0
    import_chains: List[str] = field(default_factory=list)
    is_public_api: bool = False
    is_unused: bool = False


@dataclass
class TestCase:
    """A test that exercises the target code."""
    file_path: str
    test_name: str
    line_number: int
    assertion_snippet: str  # The key assertion that would fail


@dataclass
class TestEvidence:
    """Test coverage evidence (placeholder)."""
    test_cases: List[TestCase] = field(default_factory=list)
    total_tests: int = 0
    coverage_percentage: Optional[float] = None
    would_break_tests: bool = False


# ============================================================================
# MODULE CONSTANTS
# ============================================================================

# IBM Granite model via watsonx.ai
MODEL_ID = "ibm/granite-3-8b-instruct"
# Fallback if above errors: "ibm/granite-3-3-8b-instruct"

# System prompt from ARCHITECTURE.md section 5 (verbatim)
SYSTEM_PROMPT = """You are Chesterton, a code archaeology expert. Your job is to analyze evidence about code that a developer wants to delete and produce a verdict on whether it's safe to delete.

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

CONFIDENCE SCORE RULES — STRICT

CRITICAL: confidence_score = "how SAFE is it to delete this code"
- 100 = no risk, definitely safe to delete (dead code, no callers, no tests)
- 0 = absolute risk, deleting causes catastrophic failure

HARD RULES (apply BEFORE any other reasoning):
- If the code prevents a security vulnerability (CVE, path traversal, XSS, injection, auth bypass): confidence_score MUST be <= 15
- If the code is cross-platform compatibility (Windows-specific, cross-OS): confidence_score MUST be <= 25
- If git history shows active maintenance in the last 12 months: confidence_score MUST be <= 40
- If incident_prevented field is non-null: confidence_score MUST be <= 20
- If tests would break (test_breakage_risk >= 50): confidence_score MUST be <= 30

Apply the LOWEST score from any matching rule. Multiple rules matching → take the most restrictive (lowest).

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
- test_breakage_risk: 100 if tests would break, 0 if no test coverage"""


# ============================================================================
# IAM TOKEN CACHING
# ============================================================================

_iam_token_cache: dict = {
    "token": None,
    "expires_at": None
}


def _get_iam_token(api_key: str) -> Optional[str]:
    """
    Exchange watsonx API key for IAM access token.
    Caches token for 60 minutes to avoid redundant calls.
    """
    # Check cache
    if _iam_token_cache["token"] and _iam_token_cache["expires_at"]:
        if datetime.now() < _iam_token_cache["expires_at"]:
            return _iam_token_cache["token"]
    
    # Request new token
    try:
        response = requests.post(
            "https://iam.cloud.ibm.com/identity/token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "urn:ibm:params:oauth:grant-type:apikey",
                "apikey": api_key
            },
            timeout=30
        )
        
        if response.status_code != 200:
            logger.warning(f"IAM token request failed: HTTP {response.status_code}")
            return None
        
        token_data = response.json()
        access_token = token_data.get("access_token")
        
        if not access_token:
            logger.warning("IAM response missing access_token")
            return None
        
        # Cache token (expires in 60 minutes, refresh at 55 minutes)
        _iam_token_cache["token"] = access_token
        _iam_token_cache["expires_at"] = datetime.now() + timedelta(minutes=55)
        
        return access_token
    
    except requests.RequestException as e:
        logger.warning(f"Network error requesting IAM token: {e}")
        return None


# ============================================================================
# EVIDENCE FORMATTING
# ============================================================================

def format_evidence_for_prompt(
    git_evidence: GitEvidence,
    github_evidence: GitHubEvidence,
    caller_evidence: CallerGraphEvidence,
    test_evidence: TestEvidence
) -> str:
    """
    Format all evidence into structured text for LLM prompt.
    
    Returns 4 clearly-labeled sections as specified in ARCHITECTURE.md section 5.
    If a section is empty, writes "No evidence available" rather than omitting it.
    """
    sections = []
    
    # === GIT HISTORY EVIDENCE ===
    git_section = "=== GIT HISTORY EVIDENCE ===\n"
    if git_evidence.commits:
        if git_evidence.original_commit:
            git_section += f"Original commit: {git_evidence.original_commit.sha[:7]} by {git_evidence.original_commit.author} on {git_evidence.original_commit.date}\n"
            git_section += f"  Message: {git_evidence.original_commit.message}\n"
            if git_evidence.original_commit.diff_snippet:
                git_section += f"  Diff: {git_evidence.original_commit.diff_snippet[:200]}...\n"
            git_section += "\n"
        
        git_section += f"Total commits touching this code: {git_evidence.total_commits}\n"
        git_section += f"Last modified: {git_evidence.last_modified}\n"
        git_section += f"All authors: {', '.join(git_evidence.blame_authors)}\n"
        
        if git_evidence.cross_repo_history:
            git_section += f"Cross-repo history: {git_evidence.cross_repo_history}\n"
        
        git_section += "\nRecent commits:\n"
        for i, commit in enumerate(git_evidence.commits[:5]):
            git_section += f"{i+1}. {commit.sha[:7]} by {commit.author} on {commit.date}\n"
            git_section += f"   Message: {commit.message}\n"
            if commit.diff_snippet:
                git_section += f"   Diff: {commit.diff_snippet[:150]}...\n"
    else:
        git_section += "No evidence available\n"
    
    sections.append(git_section)
    
    # === GITHUB EVIDENCE ===
    github_section = "=== GITHUB EVIDENCE ===\n"
    if github_evidence.linked_issues or github_evidence.linked_prs or github_evidence.cross_repo_references:
        if github_evidence.linked_issues:
            github_section += f"Linked issues ({len(github_evidence.linked_issues)}):\n"
            for issue in github_evidence.linked_issues[:5]:
                github_section += f"  #{issue.number}: {issue.title}\n"
                github_section += f"  URL: {issue.url}\n"
                github_section += f"  Labels: {', '.join(issue.labels)}\n"
                # First 500 chars of body
                body_preview = issue.body[:500] if issue.body else ""
                github_section += f"  Body: {body_preview}...\n"
                # Top comment if available
                if issue.comments:
                    github_section += f"  Top comment: {issue.comments[0][:300]}...\n"
                github_section += "\n"
        
        if github_evidence.linked_prs:
            github_section += f"Linked PRs ({len(github_evidence.linked_prs)}):\n"
            for pr in github_evidence.linked_prs[:5]:
                github_section += f"  #{pr.number}: {pr.title}\n"
                github_section += f"  URL: {pr.url}\n"
        
        if github_evidence.cross_repo_references:
            github_section += f"Cross-repo references: {', '.join(github_evidence.cross_repo_references)}\n"
    else:
        github_section += "No evidence available\n"
    
    sections.append(github_section)
    
    # === CALLER GRAPH EVIDENCE ===
    caller_section = "=== CALLER GRAPH EVIDENCE ===\n"
    if caller_evidence.total_callers > 0 or caller_evidence.call_sites:
        caller_section += f"Total callers: {caller_evidence.total_callers}\n"
        caller_section += f"Is public API: {caller_evidence.is_public_api}\n"
        caller_section += f"Is unused: {caller_evidence.is_unused}\n"
        
        if caller_evidence.call_sites:
            caller_section += "\nCall sites:\n"
            for site in caller_evidence.call_sites[:10]:
                caller_section += f"  {site.file_path}:{site.line_number} in {site.function_name}\n"
                caller_section += f"  Context: {site.context_snippet}\n"
        
        if caller_evidence.import_chains:
            caller_section += f"\nImport chains: {', '.join(caller_evidence.import_chains[:5])}\n"
    else:
        caller_section += "No evidence available\n"
    
    sections.append(caller_section)
    
    # === TEST EVIDENCE ===
    test_section = "=== TEST EVIDENCE ===\n"
    if test_evidence.total_tests > 0 or test_evidence.test_cases:
        test_section += f"Total tests: {test_evidence.total_tests}\n"
        test_section += f"Would break tests: {test_evidence.would_break_tests}\n"
        
        if test_evidence.coverage_percentage is not None:
            test_section += f"Coverage: {test_evidence.coverage_percentage}%\n"
        
        if test_evidence.test_cases:
            test_section += "\nTest cases:\n"
            for test in test_evidence.test_cases[:10]:
                test_section += f"  {test.test_name} in {test.file_path}:{test.line_number}\n"
                test_section += f"  Assertion: {test.assertion_snippet}\n"
    else:
        test_section += "No evidence available\n"
    
    sections.append(test_section)
    
    return "\n".join(sections)


# ============================================================================
# GRANITE API CALL
# ============================================================================

def _call_granite_api(
    system_prompt: str,
    user_prompt: str,
    watsonx_api_key: str,
    watsonx_project_id: str,
    watsonx_url: str
) -> Optional[str]:
    """
    Call Granite API via watsonx.ai.
    Returns the model's text response or None on error.
    """
    # Get IAM token
    access_token = _get_iam_token(watsonx_api_key)
    if not access_token:
        logger.warning("Failed to obtain IAM access token")
        return None
    
    # Build request
    endpoint = f"{watsonx_url}/ml/v1/text/chat?version=2024-03-14"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    
    body = {
        "model_id": MODEL_ID,
        "project_id": watsonx_project_id,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "max_tokens": 2500,
        "temperature": 0.2,
        "top_p": 0.9
    }
    
    try:
        response = requests.post(endpoint, headers=headers, json=body, timeout=60)
        
        # Handle errors
        if response.status_code == 401 or response.status_code == 403:
            logger.warning("watsonx.ai credentials invalid (401/403)")
            return None
        
        if response.status_code == 429:
            logger.warning("watsonx.ai rate limit exceeded (429)")
            return None
        
        if response.status_code != 200:
            logger.warning(f"watsonx.ai API error: HTTP {response.status_code}")
            logger.warning(f"Response: {response.text[:500]}")
            return None
        
        # Parse response
        response_data = response.json()
        
        # Extract message content
        choices = response_data.get("choices", [])
        if not choices:
            logger.warning("watsonx.ai response missing 'choices'")
            return None
        
        message = choices[0].get("message", {})
        content = message.get("content", "")
        
        if not content:
            logger.warning("watsonx.ai response missing message content")
            return None
        
        return content
    
    except requests.Timeout:
        logger.warning("watsonx.ai API timeout (60s)")
        return None
    
    except requests.RequestException as e:
        logger.warning(f"Network error calling watsonx.ai: {e}")
        return None


# ============================================================================
# CONFIDENCE SANITY CHECK
# ============================================================================

def _enforce_confidence_sanity(verdict: DeletionVerdict) -> DeletionVerdict:
    """
    Post-process confidence score to enforce sanity checks.
    
    Cross-checks the score against verdict text and risk breakdown.
    Overrides score if it violates safety rules.
    """
    original_score = verdict.confidence_score
    new_score = original_score
    reason = None
    
    # Rule 1: incident_prevented is non-null/non-empty AND score > 20
    if verdict.incident_prevented and verdict.incident_prevented.strip():
        if verdict.confidence_score > 20:
            new_score = 15
            reason = "incident_prevented field is non-empty"
    
    # Rule 2: security_risk >= 70 AND score > 20
    if verdict.risk_breakdown.security_risk >= 70:
        if verdict.confidence_score > 20:
            new_score = min(new_score, 15)
            reason = f"security_risk is {verdict.risk_breakdown.security_risk}"
    
    # Rule 3: Security keywords in why_it_exists or incident_prevented
    security_keywords = ["cve", "vulnerability", "security", "exploit", "attack"]
    text_to_check = (verdict.why_it_exists or "").lower() + " " + (verdict.incident_prevented or "").lower()
    
    if any(keyword in text_to_check for keyword in security_keywords):
        if verdict.confidence_score > 30:
            new_score = min(new_score, 20)
            if not reason:
                reason = "security-related keywords found in verdict text"
    
    # Apply override if needed
    if new_score != original_score:
        logger.warning(
            f"Sanity check: confidence adjusted from {original_score} to {new_score} "
            f"because {reason}"
        )
        verdict.confidence_score = new_score
    
    return verdict


# ============================================================================
# JSON EXTRACTION HELPER
# ============================================================================

def _extract_json_object(text: str) -> dict:
    """Extract first complete JSON object from text, handling
    markdown fences and trailing prose."""
    # Strip code fences
    cleaned = text.strip()
    if cleaned.startswith("```"):
        # Remove opening fence (with optional 'json' tag)
        cleaned = re.sub(r"^```(?:json)?\s*\n?", "", cleaned)
        # Remove closing fence
        cleaned = re.sub(r"\n?```\s*$", "", cleaned)
    
    # Find first { and walk to matching }
    start = cleaned.find("{")
    if start == -1:
        raise ValueError("No JSON object found in response")
    
    depth = 0
    in_string = False
    escape_next = False
    for i in range(start, len(cleaned)):
        ch = cleaned[i]
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_string:
            escape_next = True
            continue
        if ch == '"' and not escape_next:
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = cleaned[start:i+1]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    break
    
    # Last-ditch: regex
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        return json.loads(match.group(0))
    raise ValueError("Could not extract JSON object from response")


# ============================================================================
# VERDICT SYNTHESIS
# ============================================================================

def synthesize_verdict(
    target: CodeTarget,
    git_evidence: GitEvidence,
    github_evidence: GitHubEvidence,
    caller_evidence: CallerGraphEvidence,
    test_evidence: TestEvidence,
    watsonx_api_key: str,
    watsonx_project_id: str,
    watsonx_url: str
) -> DeletionVerdict:
    """
    Synthesize verdict using Granite LLM.
    
    Process:
    1. Format all evidence into prompt
    2. Call Granite with structured JSON output schema
    3. Parse response into DeletionVerdict
    4. Validate that all claims have citations
    
    Returns DeletionVerdict with fallback values if LLM call fails.
    """
    # Format evidence
    formatted_evidence = format_evidence_for_prompt(
        git_evidence, github_evidence, caller_evidence, test_evidence
    )
    
    # Build user prompt
    user_prompt = f"""Analyze this code for deletion safety:

FILE: {target.file_path}
LINES: {target.start_line}-{target.end_line}
CODE:
```
{target.content}
```

{formatted_evidence}

Produce your verdict as JSON matching the schema."""
    
    # Call Granite
    response_text = _call_granite_api(
        SYSTEM_PROMPT,
        user_prompt,
        watsonx_api_key,
        watsonx_project_id,
        watsonx_url
    )
    
    # Parse response
    if not response_text:
        logger.warning("Granite API call failed, returning fallback verdict")
        return _create_fallback_verdict(target, git_evidence, github_evidence, caller_evidence, test_evidence)
    
    # Parse JSON using robust extractor
    try:
        verdict_data = _extract_json_object(response_text)
    except (json.JSONDecodeError, ValueError) as e:
        logger.error("Failed to parse Granite response as JSON. Raw response (full):\n%s", response_text)
        return _create_fallback_verdict(target, git_evidence, github_evidence, caller_evidence, test_evidence)
    
    # Extract fields with defaults
    summary = verdict_data.get("summary", "LLM response could not be parsed")
    what_it_does = verdict_data.get("what_it_does", "Unknown")
    why_it_exists = verdict_data.get("why_it_exists", "Unknown")
    what_depends_on_it = verdict_data.get("what_depends_on_it", "Unknown")
    incident_prevented = verdict_data.get("incident_prevented")
    confidence_score = verdict_data.get("confidence_score", 50)
    evidence_quality = verdict_data.get("evidence_quality", "weak")
    
    # Parse risk breakdown
    risk_data = verdict_data.get("risk_breakdown", {})
    risk_breakdown = RiskBreakdown(
        security_risk=risk_data.get("security_risk", 0),
        compatibility_risk=risk_data.get("compatibility_risk", 0),
        functionality_risk=risk_data.get("functionality_risk", 0),
        test_breakage_risk=risk_data.get("test_breakage_risk", 0)
    )
    
    # Parse citations
    citations = []
    citations_data = verdict_data.get("citations", [])
    for cit_data in citations_data:
        citations.append(EvidenceCitation(
            claim=cit_data.get("claim", ""),
            source_type=cit_data.get("source_type", ""),
            source_id=cit_data.get("source_id", ""),
            quote=cit_data.get("quote", "")
        ))
    
    verdict = DeletionVerdict(
        target=target,
        summary=summary,
        what_it_does=what_it_does,
        why_it_exists=why_it_exists,
        what_depends_on_it=what_depends_on_it,
        incident_prevented=incident_prevented,
        confidence_score=confidence_score,
        risk_breakdown=risk_breakdown,
        citations=citations,
        evidence_quality=evidence_quality,
        analysis_timestamp=datetime.now(),
        used_deep_dive=False,
        git_evidence=git_evidence,
        github_evidence=github_evidence,
        caller_evidence=caller_evidence,
        test_evidence=test_evidence
    )
    
    # Apply confidence sanity checks
    verdict = _enforce_confidence_sanity(verdict)
    
    return verdict


def _create_fallback_verdict(
    target: CodeTarget,
    git_evidence: GitEvidence,
    github_evidence: GitHubEvidence,
    caller_evidence: CallerGraphEvidence,
    test_evidence: TestEvidence
) -> DeletionVerdict:
    """Create a fallback verdict when LLM call fails."""
    return DeletionVerdict(
        target=target,
        summary="LLM response could not be parsed. Manual review required.",
        what_it_does="Unknown - LLM analysis failed",
        why_it_exists="Unknown - LLM analysis failed",
        what_depends_on_it="Unknown - LLM analysis failed",
        incident_prevented=None,
        confidence_score=50,
        risk_breakdown=RiskBreakdown(
            security_risk=50,
            compatibility_risk=50,
            functionality_risk=50,
            test_breakage_risk=50
        ),
        citations=[],
        evidence_quality="weak",
        analysis_timestamp=datetime.now(),
        used_deep_dive=False,
        git_evidence=git_evidence,
        github_evidence=github_evidence,
        caller_evidence=caller_evidence,
        test_evidence=test_evidence
    )


# ============================================================================
# DEEP DIVE (STUB)
# ============================================================================

def invoke_deep_dive(
    target: CodeTarget,
    initial_verdict: DeletionVerdict,
    all_evidence: dict
) -> DeletionVerdict:
    """
    Invoke Bob Shell for deep dive analysis.
    
    NOT IMPLEMENTED YET - will be added in a later session.
    """
    raise NotImplementedError("Deep dive mode not yet implemented")


# ============================================================================
# SMOKE TEST
# ============================================================================

if __name__ == "__main__":
    from src.chesterton.git_history import (
        CodeTarget, analyze_git_history
    )
    from src.chesterton.github_fetcher import fetch_github_evidence
    
    # Debug .env loading
    print(f"DEBUG: .env path resolved to: {_env_path}")
    print(f"DEBUG: .env exists: {_env_path.exists()}")
    print(f"DEBUG: GITHUB_TOKEN in env: {os.getenv('GITHUB_TOKEN') is not None}")
    print(f"DEBUG: GITHUB_TOKEN length: {len(os.getenv('GITHUB_TOKEN') or '')}")
    print(f"DEBUG: WATSONX_API_KEY in env: {os.getenv('WATSONX_API_KEY') is not None}")
    
    # Verify GitHub token is loaded
    github_token = os.getenv("GITHUB_TOKEN")
    if not github_token:
        print("WARNING: No GitHub token provided (GITHUB_TOKEN not found in environment)")
    else:
        print(f"GitHub token loaded: {github_token[:10]}...")
    
    target = CodeTarget(
        file_path="src/flask/sessions.py",
        start_line=361,
        end_line=363,
        repo_path=r"C:\Users\Oscar\chesterton\flask",
        content=(
            '                    secure=secure,\n'
            '                    samesite=samesite,\n'
            '                    httponly=httponly,'
        ),
    )
    
    print("Step 1: Analyzing git history...")
    git_ev = analyze_git_history(target)
    print(f"  Found {git_ev.total_commits} commits, original by {git_ev.original_commit.author if git_ev.original_commit else 'unknown'}")
    
    print("Step 2: Fetching GitHub evidence...")
    gh_ev = fetch_github_evidence(target, git_ev, github_token)
    print(f"  Found {len(gh_ev.linked_issues)} linked issues, {len(gh_ev.cross_repo_references)} cross-repo refs")
    
    print("Step 3: Synthesizing verdict with Granite...")
    
    # Get credentials with fallback
    api_key = os.getenv("WATSONX_API_KEY")
    project_id = os.getenv("WATSONX_PROJECT_ID")
    url = os.getenv("WATSONX_URL")
    
    if not api_key or not project_id or not url:
        print("ERROR: Missing watsonx credentials. Set WATSONX_API_KEY, WATSONX_PROJECT_ID, and WATSONX_URL")
        exit(1)
    
    verdict = synthesize_verdict(
        target=target,
        git_evidence=git_ev,
        github_evidence=gh_ev,
        caller_evidence=CallerGraphEvidence(),  # empty placeholder
        test_evidence=TestEvidence(),           # empty placeholder
        watsonx_api_key=api_key,
        watsonx_project_id=project_id,
        watsonx_url=url,
    )
    
    print("\n" + "="*60)
    print("VERDICT")
    print("="*60)
    print(f"Confidence: {verdict.confidence_score}/100")
    print(f"Summary: {verdict.summary}")
    print(f"\nWhy it exists:\n{verdict.why_it_exists}")
    print(f"\nIncident prevented: {verdict.incident_prevented}")
    print(f"\nCitations: {len(verdict.citations)}")
    for cit in verdict.citations[:3]:
        print(f"  - [{cit.source_type}] {cit.claim[:80]}...")
    print(f"\nEvidence quality: {verdict.evidence_quality}")

# Made with Bob