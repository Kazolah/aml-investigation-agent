"""
agent/nodes/sar_generator.py — Node 8: SAR Draft Generation

Maps the reviewed AML state to the FINTRAC STR (Suspicious Transaction Report)
structure and generates:
    1. A structured JSON SAR record
    2. A PDF document (via ReportLab)

FINTRAC STR structure implemented:
    Part A — Reporting entity (mock bank details)
    Part B — Subject information (from CDD profile)
    Part C — Transaction details (from enrichment)
    Part D — Suspicious activity description (from LLM narrative + human review)
    Part E — Red flags (from LLM reasoning)
    Part F — Action taken (from human review decision)

Output files are written to: data/sar_output/
"""

import json
import os
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

from agent.state import AMLState

load_dotenv()

SAR_OUTPUT_DIR = Path("data/sar_output")


# ── Mock reporting entity ─────────────────────────────────────────────────────
REPORTING_ENTITY = {
    "institution_name": "Northern Trust Bank of Canada",
    "fintrac_reporting_id": "NTBC-2024-001",
    "branch_address": "100 King Street West, Suite 5400, Toronto, ON M5X 1B1",
    "contact_officer": "AML Compliance Department",
    "contact_phone": "416-555-0100",
    "contact_email": "aml.compliance@northerntrustbank.ca",
    "swift_bic": "NTBCCATT",
}


def _build_sar_json(state: AMLState, generated_at: str) -> dict:
    alert = state["alert"]
    cdd = state.get("cdd_profile", {})
    txn_history = state.get("transaction_history", [])
    llm = state.get("llm_reasoning", {})
    human_override = state.get("human_override", {})
    watchlist = state.get("watchlist_result", {})
    velocity = state.get("velocity_metrics", {})
    alert_meta = alert.get("alert_metadata", {})
    if isinstance(alert_meta, str):
        alert_meta = json.loads(alert_meta)

    # Determine final narrative — use human override note if provided, else LLM narrative
    officer_action = human_override.get("action", "approve")
    officer_note = human_override.get("override_note", "")
    base_narrative = llm.get("sar_narrative", "")
    if officer_action == "override" and officer_note:
        final_narrative = f"{base_narrative}\n\nCompliance Officer Note: {officer_note}"
    else:
        final_narrative = base_narrative

    # Most suspicious transactions (last 10 for the report)
    suspicious_txns = txn_history[:10]

    return {
        "sar_id": f"SAR-{alert.get('alert_id')}-{generated_at[:10].replace('-', '')}",
        "generated_at": generated_at,
        "alert_id": alert.get("alert_id"),
        "rule_fired": alert.get("rule_fired"),
        "part_a_reporting_entity": REPORTING_ENTITY,
        "part_b_subject": {
            "account_id": cdd.get("account_id"),
            "full_name": cdd.get("full_name"),
            "date_of_birth": cdd.get("date_of_birth"),
            "nationality": cdd.get("nationality"),
            "country_of_residence": cdd.get("country_of_residence"),
            "occupation": cdd.get("occupation"),
            "risk_rating": cdd.get("risk_rating"),
            "is_pep": cdd.get("is_pep"),
            "onboarding_date": cdd.get("onboarding_date"),
            "last_kyc_review": cdd.get("last_review_date"),
        },
        "part_c_transactions": {
            "alert_transaction": {
                "amount": alert.get("amount"),
                "currency": alert.get("currency"),
                "counterparty": alert.get("counterparty"),
                "counterparty_account": alert.get("counterparty_account"),
                "timestamp": alert.get("timestamp"),
            },
            "velocity_summary": velocity,
            "recent_transactions": suspicious_txns,
            "total_transactions_90d": velocity.get("txn_count_90d"),
            "total_volume_90d": velocity.get("total_volume_90d"),
        },
        "part_d_suspicious_activity": {
            "narrative": final_narrative,
            "rule_description": alert_meta.get("rule_description", ""),
            "watchlist_result": {
                "match_found": watchlist.get("match_found"),
                "list_name": watchlist.get("list_name"),
                "matched_entity": watchlist.get("matched_entry", {}).get("name") if watchlist.get("match_found") else None,
                "confidence": watchlist.get("confidence_score"),
            } if not state.get("watchlist_failed") else {"status": "screening_failed"},
        },
        "part_e_red_flags": llm.get("red_flags", []),
        "part_f_action_taken": {
            "llm_recommendation": llm.get("recommendation"),
            "suspicion_score": llm.get("suspicion_score"),
            "human_review": {
                "officer_id": human_override.get("officer_id"),
                "officer_name": human_override.get("officer_name"),
                "action": human_override.get("action"),
                "override_note": officer_note,
                "reviewed_at": human_override.get("reviewed_at"),
            },
            "final_decision": "file_sar",
        },
    }


def _build_sar_pdf(sar: dict, output_path: Path) -> None:
    """Render the SAR JSON to a formatted PDF using ReportLab."""
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.lib import colors
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
    )

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=letter,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "Title", parent=styles["Heading1"], fontSize=14, spaceAfter=6
    )
    h2_style = ParagraphStyle(
        "H2", parent=styles["Heading2"], fontSize=11, spaceAfter=4,
        textColor=colors.HexColor("#1a1a2e")
    )
    body_style = ParagraphStyle(
        "Body", parent=styles["Normal"], fontSize=9, leading=14
    )
    label_style = ParagraphStyle(
        "Label", parent=styles["Normal"], fontSize=8, textColor=colors.grey
    )

    story = []

    # Header
    story.append(Paragraph("SUSPICIOUS TRANSACTION REPORT (STR)", title_style))
    story.append(Paragraph("FINTRAC — Financial Transactions and Reports Analysis Centre of Canada", label_style))
    story.append(Spacer(1, 0.1 * inch))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.black))
    story.append(Spacer(1, 0.1 * inch))

    meta_data = [
        ["SAR ID", sar["sar_id"]],
        ["Alert ID", sar["alert_id"]],
        ["Rule Fired", sar["rule_fired"]],
        ["Generated", sar["generated_at"]],
    ]
    meta_table = Table(meta_data, colWidths=[1.5 * inch, 4 * inch])
    meta_table.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 0.15 * inch))

    # Part A — Reporting Entity
    story.append(Paragraph("PART A — REPORTING ENTITY", h2_style))
    entity = sar["part_a_reporting_entity"]
    story.append(Paragraph(f"<b>{entity['institution_name']}</b>", body_style))
    story.append(Paragraph(entity["branch_address"], body_style))
    story.append(Paragraph(f"FINTRAC ID: {entity['fintrac_reporting_id']}  |  SWIFT: {entity['swift_bic']}", body_style))
    story.append(Spacer(1, 0.1 * inch))

    # Part B — Subject
    story.append(Paragraph("PART B — SUBJECT INFORMATION", h2_style))
    subj = sar["part_b_subject"]
    subj_data = [
        ["Account ID", subj["account_id"], "Full Name", subj["full_name"]],
        ["Nationality", subj["nationality"], "Country of Residence", subj["country_of_residence"]],
        ["Date of Birth", subj["date_of_birth"] or "N/A", "Occupation", subj["occupation"]],
        ["Risk Rating", subj["risk_rating"], "Is PEP", str(subj["is_pep"])],
        ["Onboarding Date", subj["onboarding_date"], "Last KYC Review", subj["last_kyc_review"]],
    ]
    subj_table = Table(subj_data, colWidths=[1.2 * inch, 2.0 * inch, 1.5 * inch, 2.0 * inch])
    subj_table.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f7f8fa")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(subj_table)
    story.append(Spacer(1, 0.1 * inch))

    # Part C — Transactions
    story.append(Paragraph("PART C — TRANSACTION DETAILS", h2_style))
    txn_c = sar["part_c_transactions"]
    alert_txn = txn_c["alert_transaction"]
    story.append(Paragraph(
        f"<b>Alert Transaction:</b> {alert_txn['currency']} {alert_txn['amount']:,.2f} "
        f"to {alert_txn['counterparty']} on {alert_txn['timestamp'][:10]}",
        body_style
    ))
    vel = txn_c["velocity_summary"]
    story.append(Paragraph(
        f"<b>90-Day Summary:</b> {txn_c['total_transactions_90d']} transactions, "
        f"total volume {alert_txn['currency']} {txn_c['total_volume_90d']:,.2f}, "
        f"avg daily {alert_txn['currency']} {vel.get('avg_daily_volume', 0):,.2f}, "
        f"velocity ratio {vel.get('velocity_ratio', 0)}x",
        body_style
    ))
    story.append(Spacer(1, 0.05 * inch))

    # Recent transactions table
    txn_headers = ["Date", "Direction", "Amount", "Channel", "Counterparty"]
    txn_rows = [txn_headers] + [
        [t["date"], t["direction"].upper(), f"{t['currency']} {t['amount']:,.2f}",
         t["channel"], t["counterparty"][:30]]
        for t in txn_c["recent_transactions"][:8]
    ]
    txn_table = Table(txn_rows, colWidths=[0.8*inch, 0.7*inch, 1.2*inch, 0.8*inch, 3.2*inch])
    txn_table.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a1a2e")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7f8fa")]),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.lightgrey),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(txn_table)
    story.append(Spacer(1, 0.1 * inch))

    # Part D — Suspicious Activity
    story.append(Paragraph("PART D — SUSPICIOUS ACTIVITY DESCRIPTION", h2_style))
    pd = sar["part_d_suspicious_activity"]
    story.append(Paragraph(pd["rule_description"], label_style))
    story.append(Spacer(1, 0.05 * inch))
    story.append(Paragraph(pd["narrative"], body_style))
    story.append(Spacer(1, 0.1 * inch))

    # Part E — Red Flags
    story.append(Paragraph("PART E — RED FLAGS IDENTIFIED", h2_style))
    for flag in sar["part_e_red_flags"]:
        story.append(Paragraph(f"• {flag}", body_style))
    story.append(Spacer(1, 0.1 * inch))

    # Part F — Action Taken
    story.append(Paragraph("PART F — ACTION TAKEN", h2_style))
    pf = sar["part_f_action_taken"]
    hr = pf["human_review"]
    story.append(Paragraph(
        f"<b>LLM Recommendation:</b> {pf['llm_recommendation']} "
        f"(suspicion score: {pf['suspicion_score']})",
        body_style
    ))
    story.append(Paragraph(
        f"<b>Reviewed by:</b> {hr.get('officer_id', 'N/A')} — "
        f"Action: {hr.get('action', 'approve')} at {hr.get('reviewed_at', 'N/A')}",
        body_style
    ))
    if hr.get("override_note"):
        story.append(Paragraph(f"<b>Officer Note:</b> {hr['override_note']}", body_style))
    story.append(Paragraph(f"<b>Final Decision:</b> {pf['final_decision'].upper()}", body_style))

    # Footer
    story.append(Spacer(1, 0.2 * inch))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.grey))
    story.append(Paragraph(
        f"Generated by AML Investigation Agent | {sar['generated_at']} | "
        f"POC — Not a real regulatory filing",
        label_style
    ))

    doc.build(story)


def sar_generation(state: AMLState) -> dict:
    SAR_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    generated_at = datetime.now().isoformat()
    sar = _build_sar_json(state, generated_at)

    # Write JSON
    alert_id = state["alert"].get("alert_id", "UNKNOWN")
    json_path = SAR_OUTPUT_DIR / f"SAR-{alert_id}.json"
    with open(json_path, "w") as f:
        json.dump(sar, f, indent=2, default=str)

    # Write PDF
    pdf_path = SAR_OUTPUT_DIR / f"SAR-{alert_id}.pdf"
    _build_sar_pdf(sar, pdf_path)

    sar["pdf_path"] = str(pdf_path)
    sar["json_path"] = str(json_path)

    entry = {
        "node": "sar_generation",
        "timestamp": generated_at,
        "summary": (
            f"SAR draft generated for alert {alert_id}. "
            f"JSON: {json_path.name}. PDF: {pdf_path.name}."
        ),
    }

    return {
        "sar_draft": sar,
        "decision": "file_sar",
        "audit_trail": [entry],
    }
