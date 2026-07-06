"""
ui/streamlit_app.py — Compliance Officer Human Review Interface

This is the UI that drives the LangGraph interrupt/resume pattern.

How it works:
    1. Officer selects an alert ID that is pending human review
    2. The UI reads the paused graph state from the SQLite checkpointer
    3. Displays: alert summary, transactions, CDD profile, watchlist result,
       LLM reasoning (score, red flags, draft narrative)
    4. Officer chooses: Approve / Override (requires note) / Send Back
    5. On submit, the UI injects human_override into state via update_state()
       and resumes the graph — which runs sar_generation and audit_log to completion

Run:
    streamlit run ui/streamlit_app.py
"""

import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

# ── Path setup ─────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

# Disable LangSmith tracing if no key configured
api_key = os.getenv("LANGCHAIN_API_KEY", "")
if not api_key or api_key.startswith("ls__..."):
    os.environ["LANGCHAIN_TRACING_V2"] = "false"

DB_PATH = os.getenv("DB_PATH", str(ROOT / "data" / "mock_transactions.db"))

# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="AML Compliance Review",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Styling ────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
    .risk-high   { color: #dc2626; font-weight: 700; }
    .risk-medium { color: #d97706; font-weight: 700; }
    .risk-low    { color: #16a34a; font-weight: 700; }
    .flag-item   { padding: 4px 0; border-bottom: 1px solid #e5e7eb; }
    .metric-card { background: #f7f8fa; border-radius: 6px; padding: 12px; }
    .section-header { font-size: 0.85rem; font-weight: 600; text-transform: uppercase;
                      color: #57606a; letter-spacing: 0.05em; margin-bottom: 8px; }
    div[data-testid="stHorizontalBlock"] { align-items: stretch; }
</style>
""", unsafe_allow_html=True)


# ── Helpers ────────────────────────────────────────────────────────────────────

def get_pending_alerts() -> list[str]:
    """Return alert IDs currently paused at the human_review interrupt."""
    try:
        from agent.graph import build_graph
        graph = build_graph(DB_PATH)
        # LangGraph stores checkpoint state — find threads interrupted at human_review
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute(
            """SELECT DISTINCT thread_id FROM checkpoints
               WHERE thread_id NOT LIKE 'writes_%'
               ORDER BY thread_id"""
        ).fetchall()
        conn.close()
        return [r[0] for r in rows]
    except Exception:
        # Fallback: show all known alert IDs
        return ["ALT-001", "ALT-002", "ALT-003", "ALT-004", "ALT-005"]


def load_graph_state(alert_id: str) -> dict | None:
    """Load the current paused graph state for a given alert/thread."""
    try:
        from agent.graph import build_graph
        graph = build_graph(DB_PATH)
        config = {"configurable": {"thread_id": alert_id}}
        snapshot = graph.get_state(config)
        if snapshot and snapshot.values:
            return snapshot.values
    except Exception as e:
        st.error(f"Failed to load state for {alert_id}: {e}")
    return None


def resume_graph(alert_id: str, human_override: dict) -> dict | None:
    """Inject human_override and resume the graph to completion."""
    try:
        from agent.graph import build_graph
        graph = build_graph(DB_PATH)
        config = {"configurable": {"thread_id": alert_id}}
        # Inject human decision into state
        graph.update_state(config, {"human_override": human_override}, as_node="node_human_review")
        # Resume — graph runs human_review → sar_generation → audit_log
        final_state = None
        for step in graph.stream(None, config=config):
            node_name = list(step.keys())[0]
            st.write(f"  ✓ `{node_name}`")
        snapshot = graph.get_state(config)
        return snapshot.values if snapshot else None
    except Exception as e:
        st.error(f"Failed to resume graph: {e}")
        return None


def score_badge(score: str) -> str:
    colour = {"high": "#dc2626", "medium": "#d97706", "low": "#16a34a"}.get(score, "#6b7280")
    return f'<span style="background:{colour};color:white;padding:2px 10px;border-radius:12px;font-size:0.85rem;font-weight:600">{score.upper()}</span>'


def risk_colour(rating: str) -> str:
    return {"HIGH": "risk-high", "MEDIUM": "risk-medium", "LOW": "risk-low"}.get(rating, "")


# ── Sidebar ────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 🔍 AML Compliance Review")
    st.markdown("*Suspicious Activity Investigation Agent*")
    st.divider()

    alert_id = st.selectbox(
        "Select Alert",
        options=["ALT-001", "ALT-002", "ALT-003", "ALT-004", "ALT-005"],
        index=2,  # Default to ALT-003 (the high-risk demo scenario)
    )

    st.divider()
    st.markdown("**Agent Status**")

    if st.button("🔄 Load Alert State", use_container_width=True):
        st.session_state["loaded_alert"] = alert_id
        st.session_state["state"] = load_graph_state(alert_id)
        st.session_state["submitted"] = False

    st.divider()
    st.markdown("""
    **Officer Actions**
    - **Approve** — Accept LLM recommendation
    - **Override** — Change decision (note required)
    - **Send Back** — Re-run enrichment
    """)
    st.divider()
    st.caption("POC — Northern Trust Bank of Canada")
    st.caption("AML Investigation Agent v0.1")


# ── Main content ───────────────────────────────────────────────────────────────
st.title("AML Suspicious Activity — Compliance Review")

if "state" not in st.session_state or not st.session_state.get("state"):
    st.info("👈 Select an alert from the sidebar and click **Load Alert State** to begin.")

    # Quick-start instructions
    with st.expander("Getting started"):
        st.markdown("""
        1. Run `python data/seed_data.py` to populate the database
        2. Run `python run_agent.py --alert ALT-003` to process an alert through the agent
        3. The agent will pause at the human review step
        4. Come back here and select **ALT-003** → click **Load Alert State**
        5. Review the LLM analysis and take an action
        """)
    st.stop()

state = st.session_state["state"]

if not state:
    st.warning(f"No paused state found for {st.session_state.get('loaded_alert')}. Run `python run_agent.py --alert ALT-003` first.")
    st.stop()

alert = state.get("alert", {})
cdd = state.get("cdd_profile", {})
velocity = state.get("velocity_metrics", {})
watchlist = state.get("watchlist_result", {})
watchlist_failed = state.get("watchlist_failed", False)
llm = state.get("llm_reasoning", {})
txn_history = state.get("transaction_history", [])
edd_required = state.get("edd_required", False)
alert_meta = alert.get("alert_metadata", {})
if isinstance(alert_meta, str):
    alert_meta = json.loads(alert_meta)

# ── Alert header ───────────────────────────────────────────────────────────────
col_id, col_rule, col_score, col_rec = st.columns(4)
with col_id:
    st.metric("Alert ID", alert.get("alert_id", "N/A"))
with col_rule:
    st.metric("Rule Fired", alert.get("rule_fired", "N/A"))
with col_score:
    score = llm.get("suspicion_score", "N/A")
    st.metric("Suspicion Score", score.upper() if score else "N/A")
with col_rec:
    rec = llm.get("recommendation", "N/A")
    st.metric("LLM Recommendation", rec.upper() if rec else "N/A")

st.divider()

# ── Main panels ────────────────────────────────────────────────────────────────
left, right = st.columns([3, 2])

with left:
    # ── Alert Summary ──────────────────────────────────────────────────────────
    st.markdown('<p class="section-header">Alert Summary</p>', unsafe_allow_html=True)
    with st.container(border=True):
        c1, c2 = st.columns(2)
        with c1:
            st.markdown(f"**Amount:** {alert.get('currency')} {alert.get('amount', 0):,.2f}")
            st.markdown(f"**Account:** `{alert.get('account_id')}`")
            st.markdown(f"**Timestamp:** {alert.get('timestamp', '')[:16]}")
        with c2:
            st.markdown(f"**Counterparty:** {alert.get('counterparty')}")
            st.markdown(f"**Counterparty Account:** {alert.get('counterparty_account') or 'N/A'}")
            st.markdown(f"**Rule Description:** {alert_meta.get('rule_description', 'N/A')}")

    # ── Transaction History ────────────────────────────────────────────────────
    st.markdown('<p class="section-header">Transaction History (90 days)</p>', unsafe_allow_html=True)
    with st.container(border=True):
        c1, c2, c3 = st.columns(3)
        with c1:
            st.metric("Transactions", velocity.get("txn_count_90d", 0))
        with c2:
            st.metric("Total Volume", f"CAD {velocity.get('total_volume_90d', 0):,.0f}")
        with c3:
            ratio = velocity.get("velocity_ratio", 0)
            st.metric("Velocity Ratio", f"{ratio}x", delta=f"{ratio - 1:.1f}x above baseline" if ratio > 1 else None)

        if txn_history:
            import pandas as pd
            df = pd.DataFrame(txn_history[:20])
            display_cols = ["date", "direction", "amount", "currency", "channel", "counterparty"]
            display_cols = [c for c in display_cols if c in df.columns]
            st.dataframe(
                df[display_cols].rename(columns=str.title),
                use_container_width=True,
                height=220,
            )

    # ── LLM Reasoning ─────────────────────────────────────────────────────────
    st.markdown('<p class="section-header">LLM Reasoning</p>', unsafe_allow_html=True)
    with st.container(border=True):
        if llm.get("suspicion_score"):
            st.markdown(
                f"**Suspicion Score:** {score_badge(llm['suspicion_score'])}",
                unsafe_allow_html=True
            )

        st.markdown("**Red Flags Identified:**")
        for flag in llm.get("red_flags", []):
            st.markdown(f'<div class="flag-item">⚠️ {flag}</div>', unsafe_allow_html=True)

        st.markdown("**SAR Narrative (Draft):**")
        st.markdown(
            f'<div style="background:#f7f8fa;padding:12px;border-radius:6px;font-size:0.9rem">'
            f'{llm.get("sar_narrative", "N/A")}</div>',
            unsafe_allow_html=True
        )

        if llm.get("reasoning"):
            with st.expander("LLM Reasoning Detail"):
                st.write(llm["reasoning"])

with right:
    # ── CDD Profile ───────────────────────────────────────────────────────────
    st.markdown('<p class="section-header">Customer Profile (KYC/CDD)</p>', unsafe_allow_html=True)
    with st.container(border=True):
        st.markdown(f"**Name:** {cdd.get('full_name', 'N/A')}")
        st.markdown(f"**Account:** `{cdd.get('account_id')}`")
        st.markdown(f"**Occupation:** {cdd.get('occupation', 'N/A')}")
        st.markdown(f"**Nationality:** {cdd.get('nationality')} | **Country:** {cdd.get('country_of_residence')}")
        st.markdown(f"**Onboarded:** {cdd.get('onboarding_date')} | **Last Review:** {cdd.get('last_review_date')}")

        risk = cdd.get("risk_rating", "N/A")
        risk_css = risk_colour(risk)
        st.markdown(
            f"**Risk Rating:** <span class='{risk_css}'>{risk}</span>",
            unsafe_allow_html=True
        )

        flags = []
        if cdd.get("is_pep"):
            flags.append("🔴 Politically Exposed Person (PEP)")
        if cdd.get("fatf_listed_country"):
            flags.append("🔴 FATF Grey-listed country of nationality")
        if edd_required:
            flags.append("⚠️ Enhanced Due Diligence (EDD) required")
        for f in flags:
            st.markdown(f)

    # ── Watchlist Result ──────────────────────────────────────────────────────
    st.markdown('<p class="section-header">Watchlist Screening</p>', unsafe_allow_html=True)
    with st.container(border=True):
        if watchlist_failed:
            st.error("⚠️ Watchlist screening FAILED after maximum retries")
            st.markdown("Manual screening required before proceeding.")
        elif watchlist.get("match_found"):
            st.error(f"🔴 **MATCH FOUND** — {watchlist.get('list_name')}")
            matched = watchlist.get("matched_entry", {})
            st.markdown(f"**Matched Entity:** {matched.get('name')}")
            st.markdown(f"**Program/Reason:** {matched.get('program', matched.get('risk_level', 'N/A'))}")
            st.markdown(f"**Confidence:** {watchlist.get('confidence_score', 0):.0%}")
            if matched.get("reason"):
                st.markdown(f"**Reason:** {matched['reason']}")
            if watchlist.get("retry_count", 0) > 0:
                st.caption(f"ℹ️ Screening succeeded after {watchlist['retry_count']} retry(s)")
        else:
            st.success(f"✅ No watchlist match found")
            st.markdown(f"Best similarity score: {watchlist.get('confidence_score', 0):.0%}")

    # ── Audit Trail ───────────────────────────────────────────────────────────
    st.markdown('<p class="section-header">Agent Audit Trail</p>', unsafe_allow_html=True)
    with st.container(border=True):
        trail = state.get("audit_trail", [])
        for entry in trail:
            st.markdown(
                f'<div style="font-size:0.8rem;padding:4px 0;border-bottom:1px solid #e5e7eb">'
                f'<b>{entry.get("node","")}</b> · '
                f'{entry.get("timestamp","")[:19]} · '
                f'{entry.get("summary","")}'
                f'</div>',
                unsafe_allow_html=True
            )

st.divider()

# ── Officer Action Panel ───────────────────────────────────────────────────────
if st.session_state.get("submitted"):
    result = st.session_state.get("result")
    if result:
        st.success("✅ Review submitted. Graph resumed to completion.")
        decision = result.get("decision", "unknown")
        if decision == "file_sar":
            sar = result.get("sar_draft", {})
            st.markdown(f"**SAR ID:** `{sar.get('sar_id', 'N/A')}`")
            pdf_path = sar.get("pdf_path")
            if pdf_path and Path(pdf_path).exists():
                with open(pdf_path, "rb") as f:
                    st.download_button(
                        label="📄 Download SAR PDF",
                        data=f.read(),
                        file_name=Path(pdf_path).name,
                        mime="application/pdf",
                    )
        elif decision == "auto_clear":
            st.info("Alert auto-cleared. No SAR filed.")
        elif decision == "sent_back_for_review":
            st.warning("Case sent back for re-investigation. Re-run `python run_agent.py --alert <id>` to re-process.")
    st.stop()

st.markdown("### Officer Decision")
st.markdown("Review the analysis above and take one of the following actions.")

action = st.radio(
    "Action",
    options=["approve", "override", "send_back"],
    format_func=lambda x: {
        "approve": "✅ Approve — Accept LLM recommendation",
        "override": "✏️ Override — Change the recommendation (note required)",
        "send_back": "↩️ Send Back — Re-run enrichment with fresh data",
    }[x],
    horizontal=True,
)

override_note = ""
if action == "override":
    override_note = st.text_area(
        "Override Note (required)",
        placeholder="Explain why you are overriding the LLM recommendation...",
        height=100,
    )
    if not override_note.strip():
        st.warning("An override note is required before submitting.")

officer_id = st.text_input("Officer ID", value="officer-001", help="Your compliance officer ID")

col_submit, col_cancel = st.columns([1, 5])
with col_submit:
    submit_disabled = (action == "override" and not override_note.strip())
    submitted = st.button(
        "Submit Decision",
        type="primary",
        use_container_width=True,
        disabled=submit_disabled,
    )

if submitted:
    human_override = {
        "officer_id": officer_id,
        "officer_name": officer_id,
        "action": action,
        "override_note": override_note.strip(),
        "reviewed_at": datetime.now().isoformat(),
    }
    with st.spinner("Resuming agent graph..."):
        result = resume_graph(st.session_state["loaded_alert"], human_override)
    st.session_state["submitted"] = True
    st.session_state["result"] = result
    st.rerun()
