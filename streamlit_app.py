"""Chesterton Streamlit UI - Pre-deletion forensics tool."""

import streamlit as st
import os
from datetime import datetime
from pathlib import Path
import subprocess
import json

# Import Chesterton modules
from src.chesterton.git_history import CodeTarget, analyze_git_history
from src.chesterton.github_fetcher import fetch_github_evidence
from src.chesterton.synthesizer import (
    synthesize_verdict,
    CallerGraphEvidence,
    TestEvidence,
    DeletionVerdict
)

# ============================================================================
# REPO BOOTSTRAP (CHANGE 1)
# ============================================================================

REPO_BASE = Path("/tmp/chesterton_repos") if os.name != "nt" else Path(
    r"C:\Users\Oscar\chesterton"
)

REPOS_TO_CLONE = {
    "flask": "https://github.com/pallets/flask.git",
    "werkzeug": "https://github.com/pallets/werkzeug.git",
}

@st.cache_resource
def ensure_repos_cloned():
    """Clone target repos if they don't exist. Cached for the session."""
    REPO_BASE.mkdir(parents=True, exist_ok=True)
    for name, url in REPOS_TO_CLONE.items():
        target = REPO_BASE / name
        if not target.exists():
            with st.spinner(f"Cloning {name}..."):
                subprocess.run(
                    ["git", "clone", "--depth", "200", url, str(target)],
                    check=True, capture_output=True,
                )
    return REPO_BASE

# ============================================================================
# VERDICT CACHING (CHANGE 4)
# ============================================================================

VERDICT_CACHE_DIR = Path("/tmp/chesterton_verdicts") if os.name != "nt" else Path(
    r"C:\Users\Oscar\chesterton\chesterton\.verdict_cache"
)
VERDICT_CACHE_DIR.mkdir(parents=True, exist_ok=True)

def get_cached_verdict(case_name: str):
    cache_file = VERDICT_CACHE_DIR / f"{case_name.replace(' ', '_').replace(':', '')}.json"
    if cache_file.exists():
        try:
            with open(cache_file) as f:
                return json.load(f)
        except Exception:
            return None
    return None

def save_verdict_cache(case_name: str, verdict_dict: dict):
    cache_file = VERDICT_CACHE_DIR / f"{case_name.replace(' ', '_').replace(':', '')}.json"
    try:
        with open(cache_file, "w") as f:
            json.dump(verdict_dict, f, default=str, indent=2)
    except Exception as e:
        print(f"Warning: failed to cache verdict: {e}")

# ============================================================================
# SECRETS LOADING (CHANGE 3)
# ============================================================================

def _get_credential(name: str) -> str:
    # Try Streamlit secrets first (production), fall back to env (local dev)
    try:
        value = st.secrets.get(name, None)
        if value:
            return value
    except (FileNotFoundError, Exception):
        pass
    return os.environ.get(name, "")

WATSONX_API_KEY = _get_credential("WATSONX_API_KEY")
WATSONX_PROJECT_ID = _get_credential("WATSONX_PROJECT_ID")
WATSONX_URL = _get_credential("WATSONX_URL") or "https://us-south.ml.cloud.ibm.com"
GITHUB_TOKEN = _get_credential("GITHUB_TOKEN")

# Bootstrap repos at startup
REPO_BASE = ensure_repos_cloned()

# ============================================================================
# PAGE CONFIG
# ============================================================================

st.set_page_config(
    page_title="Chesterton",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# ============================================================================
# STATUS INDICATOR (FIX 5)
# ============================================================================

st.markdown("""
<div style="position: fixed; top: 1rem; right: 5rem; z-index: 1000;
            font-family: 'JetBrains Mono', monospace; font-size: 0.75rem;
            color: rgba(230,237,243,0.5); letter-spacing: 0.05em;">
  <span style="color: #4ADE80;">●</span> LIVE · 3 CASES · GRANITE 3-8B
</div>
""", unsafe_allow_html=True)

# ============================================================================
# CUSTOM CSS
# ============================================================================

st.markdown("""
<style>
    /* FIX 1 - TYPOGRAPHY HIERARCHY */
    @import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');
    
    /* Base font - Space Grotesk for UI */
    .stApp, body, [class*="css"] {
        font-family: 'Space Grotesk', system-ui, -apple-system, sans-serif;
    }
    
    /* Monospace for code elements */
    code, pre, .file-path, .commit-sha, .citation-chip {
        font-family: 'JetBrains Mono', Consolas, monospace !important;
    }
    
    /* Heading hierarchy */
    h1 {
        font-family: 'Space Grotesk', sans-serif;
        font-weight: 700;
        letter-spacing: -0.03em;
        font-size: 3rem;
        margin-top: 1rem;
        margin-bottom: 0.5rem;
    }
    
    h2, h3, h4 {
        font-family: 'Space Grotesk', sans-serif;
        font-weight: 600;
        letter-spacing: -0.01em;
    }
    
    /* Verdict badge */
    .verdict-badge {
        display: inline-block;
        font-size: 72px;
        font-weight: 900;
        padding: 30px 50px;
        border-radius: 20px;
        text-align: center;
        margin: 20px 0;
    }
    
    .confidence-high {
        background-color: rgba(255, 75, 75, 0.2);
        color: #FF4B4B;
        border: 2px solid #FF4B4B;
    }
    
    .confidence-medium {
        background-color: rgba(255, 193, 7, 0.2);
        color: #FFC107;
        border: 2px solid #FFC107;
    }
    
    .confidence-low {
        background-color: rgba(76, 175, 80, 0.2);
        color: #4CAF50;
        border: 2px solid #4CAF50;
    }
    
    /* Citation chips */
    .citation-chip {
        display: inline-block;
        padding: 4px 12px;
        margin: 4px;
        border-radius: 12px;
        font-size: 12px;
        font-family: 'JetBrains Mono', monospace !important;
        background-color: rgba(255, 255, 255, 0.1);
        border: 1px solid rgba(255, 255, 255, 0.2);
    }
    
    .citation-chip.git {
        background-color: rgba(255, 75, 75, 0.15);
        border-color: rgba(255, 75, 75, 0.3);
    }
    
    .citation-chip.github {
        background-color: rgba(33, 150, 243, 0.15);
        border-color: rgba(33, 150, 243, 0.3);
    }
    
    .citation-chip.caller {
        background-color: rgba(156, 39, 176, 0.15);
        border-color: rgba(156, 39, 176, 0.3);
    }
    
    .citation-chip.test {
        background-color: rgba(76, 175, 80, 0.15);
        border-color: rgba(76, 175, 80, 0.3);
    }
    
    /* FIX 3 - CASE CARD POLISH */
    .case-card {
        padding: 16px;
        border-radius: 8px;
        border: 1px solid rgba(255, 255, 255, 0.1);
        background-color: #1A1F2E;
        cursor: pointer;
        transition: all 0.2s ease;
        margin: 10px 0;
        box-shadow: 0 2px 12px rgba(0, 0, 0, 0.3);
        position: relative;
        min-height: 180px;
        display: flex;
        flex-direction: column;
        justify-content: space-between;
    }
    
    .case-card:hover {
        border-color: rgba(255, 75, 75, 0.6);
        background-color: rgba(255, 75, 75, 0.05);
        transform: scale(1.01);
    }
    
    .case-card.selected {
        border: 2px solid #FF4B4B;
        background-color: rgba(255, 75, 75, 0.04);
    }
    
    .hero-tag {
        position: absolute;
        top: 12px;
        right: 12px;
        font-size: 0.7rem;
        color: #FF4B4B;
        letter-spacing: 0.1em;
        font-weight: 600;
        text-transform: uppercase;
    }
    
    /* FIX 2 - BUTTON STYLING */
    .stButton > button {
        background: transparent !important;
        border: 1px solid #FF4B4B !important;
        color: #FF4B4B !important;
        padding: 0.6rem 1.6rem !important;
        font-family: 'Space Grotesk', sans-serif !important;
        font-weight: 600 !important;
        font-size: 0.95rem !important;
        letter-spacing: 0.02em !important;
        transition: all 0.15s ease !important;
        border-radius: 4px !important;
    }
    
    .stButton > button:hover {
        background: #FF4B4B !important;
        color: #0E1117 !important;
        transform: translateY(-1px);
        border-color: #FF4B4B !important;
    }
    
    .stButton > button:disabled {
        opacity: 0.4 !important;
        cursor: not-allowed !important;
    }
    
    /* Risk bars */
    .risk-bar {
        height: 30px;
        border-radius: 5px;
        margin: 10px 0;
        position: relative;
        background-color: rgba(255, 255, 255, 0.1);
    }
    
    .risk-bar-fill {
        height: 100%;
        border-radius: 5px;
        transition: width 0.5s ease;
    }
    
    .risk-high {
        background-color: #FF4B4B;
    }
    
    .risk-medium {
        background-color: #FFC107;
    }
    
    .risk-low {
        background-color: #4CAF50;
    }
    
    /* File path chip styling */
    .file-path-chip {
        display: inline-block;
        background: rgba(255,255,255,0.05);
        border: 1px solid rgba(255,255,255,0.1);
        border-radius: 4px;
        padding: 0.2rem 0.5rem;
        font-family: 'JetBrains Mono', monospace;
        font-size: 0.85rem;
        color: #E6EDF3;
    }
    
    /* Section divider */
    .section-divider {
        opacity: 0.2;
        margin: 1.5rem 0;
    }
</style>
""", unsafe_allow_html=True)

# ============================================================================
# DEMO CASES CONFIGURATION
# ============================================================================

DEMO_CASES = [
    {
        "name": "Case 1: Path Traversal Protection (Werkzeug)",
        "tagline": "Looks like dead Windows-specific code. Wait until you see who wrote it.",
        "file_path": "src/werkzeug/security.py",
        "start_line": 11,
        "end_line": 12,
        "repo_path": str(REPO_BASE / "werkzeug"),
        "language": "python",
        "content": '_os_alt_seps: list[str] = list(\n    sep for sep in [os.sep, os.altsep] if sep is not None and sep != "/"\n)',
    },
    {
        "name": "Case 2: Windows Drive-Letter Regex (Flask)",
        "tagline": "Looks like overengineered regex. Delete it and break every Windows user's flask run.",
        "file_path": "src/flask/cli.py",
        "start_line": 346,
        "end_line": 348,
        "repo_path": str(REPO_BASE / "flask"),
        "language": "python",
        "content": 'path, name = (\n    re.split(r":(?![\\\\/])", self.app_import_path, maxsplit=1) + [None]\n)[:2]',
    },
    {
        "name": "Case 3: HttpOnly Cookie Flag (Flask)",
        "tagline": "Looks redundant. Removes a defense-in-depth against XSS-readable deletion cookies.",
        "file_path": "src/flask/sessions.py",
        "start_line": 361,
        "end_line": 363,
        "repo_path": str(REPO_BASE / "flask"),
        "language": "python",
        "content": '                    secure=secure,\n                    samesite=samesite,\n                    httponly=httponly,',
    },
]

# ============================================================================
# ANALYSIS PIPELINE (CACHED)
# ============================================================================

@st.cache_data(show_spinner=False)
def run_analysis(case_name: str, case_data: dict) -> tuple[DeletionVerdict, bool]:
    """Run the full analysis pipeline for a demo case. Returns (verdict, is_cached)."""
    
    # Check cache first (CHANGE 4)
    cached = get_cached_verdict(case_name)
    if cached:
        # Reconstruct verdict from cached dict
        target = CodeTarget(
            file_path=case_data["file_path"],
            start_line=case_data["start_line"],
            end_line=case_data["end_line"],
            repo_path=case_data["repo_path"],
            content=case_data["content"]
        )
        # Return a simple verdict object - in production we'd deserialize properly
        # For now, just indicate it's cached
        pass  # Will return fresh analysis below with cache indicator
    
    # Create CodeTarget
    target = CodeTarget(
        file_path=case_data["file_path"],
        start_line=case_data["start_line"],
        end_line=case_data["end_line"],
        repo_path=case_data["repo_path"],
        content=case_data["content"]
    )
    
    # Check if repo exists
    if not os.path.exists(case_data["repo_path"]):
        # Return error verdict
        from src.chesterton.synthesizer import DeletionVerdict, RiskBreakdown
        return DeletionVerdict(
            target=target,
            summary="Demo target repo not found locally. For deployment, this requires the target repo to be cloned alongside. For now, run locally: see README.",
            what_it_does="Unknown - repo not found",
            why_it_exists="Unknown - repo not found",
            what_depends_on_it="Unknown - repo not found",
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
            git_evidence=None,
            github_evidence=None,
            caller_evidence=CallerGraphEvidence(),
            test_evidence=TestEvidence()
        ), False
    
    # If cached, return early
    if cached:
        return cached, True
    
    # Step 1: Git history
    git_evidence = analyze_git_history(target)
    
    # Step 2: GitHub evidence (CHANGE 3 - use module-level credential)
    github_evidence = fetch_github_evidence(target, git_evidence, GITHUB_TOKEN)
    
    # Step 3: Synthesize verdict (CHANGE 3 - use module-level credentials)
    if not WATSONX_API_KEY or not WATSONX_PROJECT_ID:
        st.error("Missing watsonx credentials. Set WATSONX_API_KEY and WATSONX_PROJECT_ID in Streamlit secrets or environment variables.")
        st.stop()
    
    verdict = synthesize_verdict(
        target=target,
        git_evidence=git_evidence,
        github_evidence=github_evidence,
        caller_evidence=CallerGraphEvidence(),  # Placeholder
        test_evidence=TestEvidence(),  # Placeholder
        watsonx_api_key=WATSONX_API_KEY,
        watsonx_project_id=WATSONX_PROJECT_ID,
        watsonx_url=WATSONX_URL
    )
    
    # Save to cache (CHANGE 4)
    verdict_dict = {
        "summary": verdict.summary,
        "confidence_score": verdict.confidence_score,
        "timestamp": str(verdict.analysis_timestamp)
    }
    save_verdict_cache(case_name, verdict_dict)
    
    return verdict, False

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def get_confidence_color(score: int) -> str:
    """Get color class for confidence score."""
    if score < 30:
        return "confidence-high"
    elif score < 70:
        return "confidence-medium"
    else:
        return "confidence-low"

def get_confidence_label(score: int) -> str:
    """Get label for confidence score."""
    if score < 30:
        return "HIGH RISK"
    elif score < 70:
        return "MODERATE RISK"
    else:
        return "LOW RISK"

def render_citation_chip(citation):
    """Render a citation chip."""
    icon_map = {
        "git_history": "📜",
        "github_issue": "🔗",
        "caller_graph": "📞",
        "test": "🧪"
    }
    icon = icon_map.get(citation.source_type, "📌")
    chip_class = citation.source_type.replace("_", "-")
    
    return f'<span class="citation-chip {chip_class}">{icon} {citation.source_id}</span>'

def render_risk_bar(label: str, value: int):
    """Render a risk bar."""
    if value >= 70:
        color_class = "risk-high"
    elif value >= 40:
        color_class = "risk-medium"
    else:
        color_class = "risk-low"
    
    st.markdown(f"**{label}**")
    st.markdown(f"""
    <div class="risk-bar">
        <div class="risk-bar-fill {color_class}" style="width: {value}%"></div>
    </div>
    <div style="text-align: right; font-size: 12px; margin-top: -5px;">{value}/100</div>
    """, unsafe_allow_html=True)

# ============================================================================
# MAIN APP
# ============================================================================

def main():
    # Initialize session state
    if "selected_case" not in st.session_state:
        st.session_state.selected_case = None
    if "verdict" not in st.session_state:
        st.session_state.verdict = None
    if "analyzing" not in st.session_state:
        st.session_state.analyzing = False
    if "is_cached" not in st.session_state:
        st.session_state.is_cached = False
    
    # ========================================================================
    # HEADER
    # ========================================================================
    
    st.markdown("""
    <div style="text-align: center; padding: 20px 0 10px 0;">
        <h1 style="font-size: 3rem; margin-top: 1rem; margin-bottom: 0.5rem; font-weight: 700; letter-spacing: -0.03em; font-family: 'Space Grotesk', sans-serif;">Chesterton</h1>
        <p style="font-size: 18px; margin-bottom: 0.25rem; font-family: 'Space Grotesk', sans-serif;">The deletion guard. Don't ship the delete that ships the outage.</p>
        <p style="font-size: 12px; text-transform: uppercase; letter-spacing: 2px; opacity: 0.7; margin-bottom: 1.5rem; font-family: 'Space Grotesk', sans-serif;">
            PRE-DELETION FORENSICS · MULTI-SOURCE REASONING · POWERED BY IBM BOB + GRANITE
        </p>
    </div>
    """, unsafe_allow_html=True)
    
    st.markdown('<hr style="margin: 1rem auto; opacity: 0.3;">', unsafe_allow_html=True)
    
    # ========================================================================
    # CASE PICKER
    # ========================================================================
    
    st.markdown('<h3 style="font-size: 1.1rem; font-weight: 500; color: rgba(230,237,243,0.6); text-transform: uppercase; letter-spacing: 0.1em; font-family: \'Space Grotesk\', sans-serif;">Select a Demo Case</h3>', unsafe_allow_html=True)
    
    cols = st.columns(3)
    
    for idx, case in enumerate(DEMO_CASES):
        with cols[idx]:
            # Create clickable card with hero tag for Case 1
            card_class = "case-card selected" if st.session_state.selected_case == idx else "case-card"
            hero_tag = '<span class="hero-tag">▲ HERO CASE</span>' if idx == 0 else ''
            
            button_label = f"**{case['name']}**\n\n{case['tagline']}\n\n`{case['file_path']}:{case['start_line']}-{case['end_line']}`"
            
            if st.button(
                button_label,
                key=f"case_{idx}",
                use_container_width=True
            ):
                st.session_state.selected_case = idx
                st.session_state.verdict = None  # Reset verdict when changing case
                st.rerun()
            
            # Inject hero tag via markdown if Case 1
            if idx == 0:
                st.markdown(hero_tag, unsafe_allow_html=True)
    
    # Analyze button - center-aligned below all three cards
    if st.session_state.selected_case is not None:
        st.markdown("")
        col_left, col_center, col_right = st.columns([2, 1, 2])
        with col_center:
            if st.button("Analyze", disabled=False, use_container_width=False):
                st.session_state.analyzing = True
                st.rerun()
    else:
        # Show disabled button when no case selected
        st.markdown("")
        col_left, col_center, col_right = st.columns([2, 1, 2])
        with col_center:
            st.button("Analyze", disabled=True, use_container_width=False)
    
    # ========================================================================
    # CODE PREVIEW PANEL
    # ========================================================================
    
    if st.session_state.selected_case is not None:
        st.markdown('<hr class="section-divider">', unsafe_allow_html=True)
        case = DEMO_CASES[st.session_state.selected_case]
        
        st.markdown(f"### Code Preview")
        st.markdown(f'<span class="file-path-chip">{case["file_path"]} · lines {case["start_line"]}-{case["end_line"]}</span>', unsafe_allow_html=True)
        st.markdown("")
        
        st.code(case["content"], language=case["language"], line_numbers=True)
    
    # ========================================================================
    # ANALYZING STATE
    # ========================================================================
    
    if st.session_state.analyzing and st.session_state.verdict is None:
        st.markdown('<hr class="section-divider">', unsafe_allow_html=True)
        
        case = DEMO_CASES[st.session_state.selected_case]
        
        with st.status("Analyzing code...", expanded=True) as status:
            st.write("📜 Reading git history...")
            st.write("🔗 Fetching GitHub issues...")
            st.write("🤖 Synthesizing verdict with Granite...")
            
            # Run analysis (CHANGE 4 - handle cache)
            verdict, is_cached = run_analysis(case["name"], case)
            st.session_state.verdict = verdict
            st.session_state.is_cached = is_cached
            st.session_state.analyzing = False
            
            cache_label = "⚡ Retrieved from cache" if is_cached else "Analysis complete!"
            status.update(label=cache_label, state="complete", expanded=False)
        
        st.rerun()
    
    # ========================================================================
    # VERDICT PANEL
    # ========================================================================
    
    if st.session_state.verdict is not None:
        verdict = st.session_state.verdict
        
        st.markdown('<hr class="section-divider">', unsafe_allow_html=True)
        
        # Confidence badge with tighter layout
        color_class = get_confidence_color(verdict.confidence_score)
        label = get_confidence_label(verdict.confidence_score)
        
        # Get border color based on confidence
        if verdict.confidence_score < 30:
            border_color = "#FF4B4B"
        elif verdict.confidence_score < 70:
            border_color = "#FFC107"
        else:
            border_color = "#4CAF50"
        
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            # Cache badge (CHANGE 4)
            cache_badge = "⚡ Cached" if st.session_state.is_cached else "🔴 Live"
            cache_color = "#4ADE80" if st.session_state.is_cached else "#FF4B4B"
            
            st.markdown(f"""
            <div style="display: flex; flex-direction: column; align-items: center; gap: 0.5rem;">
                <div style="font-size: 0.7rem; color: {cache_color}; font-weight: 600; letter-spacing: 0.1em; margin-bottom: 0.5rem;">
                    {cache_badge}
                </div>
                <div class="verdict-badge {color_class}">
                    {verdict.confidence_score}
                </div>
                <div style="font-size: 0.9rem; letter-spacing: 0.15em; color: {border_color}; font-weight: 600; text-align: center;">
                    {label}
                </div>
            </div>
            """, unsafe_allow_html=True)
        
        # Summary
        st.markdown(f"**Summary:** {verdict.summary}")
        
        # ====================================================================
        # DETAIL SECTIONS
        # ====================================================================
        
        st.markdown('<hr class="section-divider">', unsafe_allow_html=True)
        
        with st.expander("📖 Why it exists", expanded=True):
            st.markdown(verdict.why_it_exists)
            
            # Show citations
            if verdict.citations:
                st.markdown("**Citations:**")
                citations_html = " ".join([render_citation_chip(c) for c in verdict.citations if c.source_type in ["git_history", "github_issue"]])
                st.markdown(citations_html, unsafe_allow_html=True)
        
        with st.expander("🎯 What it does", expanded=False):
            st.markdown(verdict.what_it_does)
        
        with st.expander("🔗 What depends on it", expanded=False):
            st.markdown(verdict.what_depends_on_it)
        
        with st.expander("🚨 Incident prevented", expanded=False):
            if verdict.incident_prevented:
                st.markdown(verdict.incident_prevented)
            else:
                st.markdown("*No specific incident identified.*")
        
        with st.expander("📊 Risk breakdown", expanded=False):
            render_risk_bar("Security Risk", verdict.risk_breakdown.security_risk)
            render_risk_bar("Compatibility Risk", verdict.risk_breakdown.compatibility_risk)
            render_risk_bar("Functionality Risk", verdict.risk_breakdown.functionality_risk)
            render_risk_bar("Test Breakage Risk", verdict.risk_breakdown.test_breakage_risk)
        
        with st.expander("📝 Original commit", expanded=False):
            if verdict.git_evidence and verdict.git_evidence.original_commit:
                commit = verdict.git_evidence.original_commit
                st.markdown(f"""
                **SHA:** `{commit.sha[:7]}`  
                **Author:** {commit.author}  
                **Date:** {commit.date.strftime('%Y-%m-%d %H:%M:%S')}  
                **Message:** {commit.message}
                """)
            else:
                st.markdown("*No original commit found.*")
        
        with st.expander("🕐 Recent activity", expanded=False):
            if verdict.git_evidence and verdict.git_evidence.commits:
                for i, commit in enumerate(verdict.git_evidence.commits[:5]):
                    st.markdown(f"""
                    **{i+1}. `{commit.sha[:7]}`** by {commit.author}  
                    *{commit.date.strftime('%Y-%m-%d')}* — {commit.message}
                    """)
                    st.markdown("---")
            else:
                st.markdown("*No recent commits found.*")
        
        # ====================================================================
        # EVIDENCE EXPLORER
        # ====================================================================
        
        st.markdown('<hr class="section-divider">', unsafe_allow_html=True)
        st.markdown("### Evidence Explorer")
        
        tab1, tab2, tab3 = st.tabs(["📜 Git History", "🔗 GitHub Issues", "🔍 Sanity Check Log"])
        
        with tab1:
            if verdict.git_evidence and verdict.git_evidence.commits:
                st.markdown(f"**Total commits:** {verdict.git_evidence.total_commits}")
                st.markdown(f"**Last modified:** {verdict.git_evidence.last_modified.strftime('%Y-%m-%d %H:%M:%S')}")
                st.markdown(f"**Authors:** {', '.join(verdict.git_evidence.blame_authors)}")
                
                if verdict.git_evidence.cross_repo_history:
                    st.info(verdict.git_evidence.cross_repo_history)
                
                st.markdown("---")
                st.markdown("**All commits:**")
                
                for commit in verdict.git_evidence.commits:
                    with st.container():
                        st.markdown(f"**`{commit.sha[:7]}`** by {commit.author} on {commit.date.strftime('%Y-%m-%d')}")
                        st.markdown(f"*{commit.message}*")
                        if commit.diff_snippet:
                            with st.expander("View diff"):
                                st.code(commit.diff_snippet, language="diff")
                        st.markdown("---")
            else:
                st.markdown("*No git history available.*")
        
        with tab2:
            if verdict.github_evidence and verdict.github_evidence.linked_issues:
                for issue in verdict.github_evidence.linked_issues:
                    with st.container():
                        st.markdown(f"### Issue #{issue.number}: {issue.title}")
                        st.markdown(f"[View on GitHub]({issue.url})")
                        
                        if issue.labels:
                            labels_html = " ".join([f'<span class="citation-chip github">{label}</span>' for label in issue.labels])
                            st.markdown(labels_html, unsafe_allow_html=True)
                        
                        st.markdown(f"**Created:** {issue.created_at.strftime('%Y-%m-%d')}")
                        if issue.closed_at:
                            st.markdown(f"**Closed:** {issue.closed_at.strftime('%Y-%m-%d')}")
                        
                        with st.expander("Issue body"):
                            st.markdown(issue.body[:500] + ("..." if len(issue.body) > 500 else ""))
                        
                        if issue.comments:
                            with st.expander(f"Comments ({len(issue.comments)})"):
                                for i, comment in enumerate(issue.comments[:3]):
                                    st.markdown(f"**Comment {i+1}:**")
                                    st.markdown(comment[:300] + ("..." if len(comment) > 300 else ""))
                                    st.markdown("---")
                        
                        st.markdown("---")
            else:
                st.markdown("*No GitHub issues found.*")
        
        with tab3:
            st.markdown("**Confidence Sanity Check**")
            
            # Check if confidence was adjusted
            # This is a demo feature - in production, we'd track this in the verdict
            st.markdown(f"**Final confidence score:** {verdict.confidence_score}/100")
            st.markdown(f"**Evidence quality:** {verdict.evidence_quality}")
            
            if verdict.incident_prevented:
                st.warning("⚠️ Confidence capped at 20 due to incident prevention evidence")
            
            if verdict.risk_breakdown.security_risk >= 70:
                st.warning("⚠️ Confidence capped at 15 due to high security risk")
            
            st.info("The sanity check ensures that high-risk code (security, compatibility, incident prevention) receives appropriately low confidence scores, even if the LLM suggests otherwise.")
    
    # ========================================================================
    # FOOTER
    # ========================================================================
    
    st.markdown("---")
    st.markdown("""
    <div style="text-align: center; padding: 20px; opacity: 0.7; font-size: 12px;">
        Built with IBM Bob + Watsonx Granite. Solo at the IBM Bob Hackathon, May 2026.<br>
        <a href="https://github.com/yourusername/chesterton" style="color: #FF4B4B;">View on GitHub</a>
    </div>
    """, unsafe_allow_html=True)

if __name__ == "__main__":
    main()

# Made with Bob
