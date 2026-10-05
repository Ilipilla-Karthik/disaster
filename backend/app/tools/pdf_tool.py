"""Situation report PDF generator (Requirement 16).

Uses ReportLab (already a dependency). Every generated report carries the
decision-support disclaimer, the assessment timestamp, and an explicit
"Verification Required" section listing unresolved facts so the PDF can never
imply certainty the system does not have.
"""

from __future__ import annotations

import io
from datetime import datetime
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.core.constants import DISCLAIMER

__all__ = ["build_situation_report_pdf"]


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "TitleX",
            parent=base["Title"],
            fontSize=17,
            leading=21,
            spaceAfter=2,
            alignment=TA_LEFT,
        ),
        "subtitle": ParagraphStyle(
            "SubtitleX",
            parent=base["Normal"],
            fontSize=9,
            textColor=colors.HexColor("#555555"),
            spaceAfter=8,
        ),
        "h2": ParagraphStyle(
            "H2X",
            parent=base["Heading2"],
            fontSize=12,
            leading=15,
            spaceBefore=10,
            spaceAfter=4,
            textColor=colors.HexColor("#1e3a8a"),
        ),
        "body": ParagraphStyle(
            "BodyX", parent=base["BodyText"], fontSize=9, leading=12.5, spaceAfter=4
        ),
        "disclaimer": ParagraphStyle(
            "DiscX",
            parent=base["BodyText"],
            fontSize=8,
            leading=11,
            textColor=colors.HexColor("#7f1d1d"),
            backColor=colors.HexColor("#fef2f2"),
            borderPadding=6,
            spaceBefore=8,
            spaceAfter=8,
        ),
        "cell": ParagraphStyle(
            "CellX", parent=base["BodyText"], fontSize=8, leading=10.5
        ),
        "cellhead": ParagraphStyle(
            "CellHeadX",
            parent=base["BodyText"],
            fontSize=8,
            leading=10.5,
            textColor=colors.white,
        ),
    }


def _table(rows: list[list[Any]], widths: list[float], s: dict[str, ParagraphStyle],
           header: bool = True) -> Table:
    data: list[list[Any]] = []
    for r_index, row in enumerate(rows):
        style = "cellhead" if (header and r_index == 0) else "cell"
        data.append([Paragraph(str(c), s[style]) for c in row])
    tbl = Table(data, colWidths=widths, repeatRows=1 if header else 0)
    tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e3a8a")) if header else
                ("BACKGROUND", (0, 0), (-1, -1), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1),
                 [colors.white, colors.HexColor("#f8fafc")]),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    return tbl


def build_situation_report_pdf(payload: dict[str, Any]) -> bytes:
    """Render a full situation report to PDF bytes."""
    s = _styles()
    buf = io.BytesIO()
    situation = payload.get("situation") or {}

    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        rightMargin=15 * mm,
        leftMargin=15 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
        title=payload.get("title", "Situation Report"),
        author=payload.get("generated_by", "system"),
    )

    story: list[Any] = []
    generated_at = payload.get("generated_at") or datetime.now().isoformat()

    # ---- Header -----------------------------------------------------------
    story.append(Paragraph(payload.get("title", "Emergency Situation Report"), s["title"]))
    story.append(
        Paragraph(
            f"Generated {generated_at} &nbsp;|&nbsp; Prepared by "
            f"{payload.get('generated_by', 'system')} &nbsp;|&nbsp; "
            f"Situation ID: {payload.get('report_id', '-')}",
            s["subtitle"],
        )
    )
    story.append(Paragraph(DISCLAIMER, s["disclaimer"]))

    # ---- Executive summary ------------------------------------------------
    if situation.get("executive_summary"):
        story.append(Paragraph("1. Situation Summary", s["h2"]))
        story.append(Paragraph(situation["executive_summary"], s["body"]))

    # ---- Incidents --------------------------------------------------------
    story.append(Paragraph("2. Incident Summary", s["h2"]))
    incidents = situation.get("incidents") or []
    if incidents:
        rows = [["ID", "Type", "Location", "People", "Severity", "Score",
                 "Verification", "Access"]]
        for inc in incidents:
            rows.append([
                inc.get("incident_id", "-"),
                inc.get("incident_type", "-"),
                inc.get("location", "-"),
                inc.get("people_reported_affected", 0),
                inc.get("severity_level", "-"),
                inc.get("priority_score", 0),
                inc.get("verification_status", "-"),
                (inc.get("accessibility") or {}).get("status_label", "-"),
            ])
        story.append(_table(rows, [15 * mm, 21 * mm, 26 * mm, 14 * mm,
                                   22 * mm, 13 * mm, 22 * mm, 30 * mm], s))
    else:
        story.append(Paragraph("No active incidents.", s["body"]))

    # ---- Priority assessment --------------------------------------------
    story.append(Paragraph("3. Priority Assessment", s["h2"]))
    story.append(
        Paragraph(
            situation.get("priority_methodology")
            or "Deterministic weighted factor model. The LLM does not generate "
            "severity scores. Every contribution is listed per incident below.",
            s["body"],
        )
    )
    for inc in incidents:
        factors = inc.get("priority_factors") or {}
        blocks = []
        if isinstance(factors, dict) and factors.get("factors"):
            blocks.append(f"<b>{inc.get('incident_id')}</b> (score "
                          f"{factors.get('score')} &rarr; {factors.get('severity')})")
            for f in factors["factors"]:
                if f.get("contribution", 0) > 0:
                    blocks.append(
                        f"&bull; {f.get('label')}: +{f.get('contribution')} "
                        f"&mdash; {f.get('evidence')}"
                    )
            if factors.get("verification_required_fields"):
                blocks.append(
                    f"&bull; <b>Verification Required</b>: "
                    f"{', '.join(factors['verification_required_fields'])}"
                )
        if blocks:
            story.append(Paragraph("<br/>".join(blocks), s["body"]))

    # ---- Weather ----------------------------------------------------------
    story.append(Paragraph("4. Weather &amp; Environmental Conditions", s["h2"]))
    weather = situation.get("weather") or {}
    if weather:
        sim = " (SIMULATED - not an observation)" if weather.get("simulation_mode") else ""
        story.append(
            Paragraph(
                f"{weather.get('condition', 'unknown')}{sim}. "
                f"Rainfall last hour: {weather.get('rainfall_mm_last_hour')} mm; "
                f"forecast next 6h: {weather.get('rainfall_next_6h_mm')} mm. "
                f"Wind: {weather.get('wind_speed_ms')} m/s "
                f"(gusts {weather.get('wind_gust_ms')} m/s). "
                f"Worsening: {'yes' if weather.get('worsening') else 'no'}. "
                f"Severe warning: {'yes' if weather.get('severe_warning') else 'no'}. "
                f"Visibility: {weather.get('visibility_km')} km.",
                s["body"],
            )
        )
        if weather.get("note"):
            story.append(Paragraph(f"<i>{weather['note']}</i>", s["body"]))
    else:
        story.append(Paragraph("No weather data available. Verification required.", s["body"]))

    # ---- Accessibility ---------------------------------------------------
    story.append(Paragraph("5. Accessibility &amp; Routes", s["h2"]))
    roads = situation.get("roads") or []
    if roads:
        rows = [["Road", "Zone Route", "Status", "Provenance", "Reason"]]
        for r in roads:
            rows.append([
                r.get("name", "-"),
                f"{r.get('origin_zone') or '-'} &rarr; {r.get('destination_zone') or '-'}",
                r.get("status", "-"),
                r.get("fact_status", "-"),
                (r.get("blocked_reason") or "-")[:60],
            ])
        story.append(_table(rows, [40 * mm, 45 * mm, 26 * mm, 22 * mm, 30 * mm], s))
        story.append(
            Paragraph(
                "<i>Provenance matters: <b>known</b> = reported by an authoritative "
                "source; <b>inferred</b> = an agent hypothesis requiring field "
                "verification; <b>unverified</b> = no report on file. An inferred "
                "problem is never presented as a confirmed closure.</i>",
                s["body"],
            )
        )
    else:
        story.append(Paragraph("No route records available.", s["body"]))

    # ---- Resources -------------------------------------------------------
    story.append(Paragraph("6. Resource Position", s["h2"]))
    resources = situation.get("resources") or []
    if resources:
        rows = [["ID", "Type", "Location", "Cap.", "Status", "Assignment"]]
        for r in resources:
            rows.append([
                r.get("resource_id", "-"), r.get("resource_type", "-"),
                r.get("location", "-"), r.get("capacity", "-"),
                r.get("status", "-"), r.get("current_assignment") or "-",
            ])
        story.append(_table(rows, [22 * mm, 30 * mm, 40 * mm, 13 * mm, 24 * mm, 34 * mm], s))
    else:
        story.append(Paragraph("No resources registered.", s["body"]))

    # ---- Shortages -------------------------------------------------------
    story.append(Paragraph("7. Resource Shortages &amp; Escalation", s["h2"]))
    shortages = situation.get("shortages") or []
    if shortages:
        rows = [["Resource", "Required", "Available", "Deficit", "Note"]]
        for sh in shortages:
            rows.append([
                sh.get("label", sh.get("resource_type", "-")),
                sh.get("required", "-"), sh.get("available", "-"),
                sh.get("deficit", "-"), (sh.get("note") or "-")[:80],
            ])
        story.append(_table(rows, [34 * mm, 18 * mm, 20 * mm, 16 * mm, 75 * mm], s))
        story.append(
            Paragraph(
                "Deficits are reported as-is. The system does not create additional "
                "resources to close a gap; escalation to a higher authority is required.",
                s["body"],
            )
        )
    else:
        story.append(Paragraph("No resource shortages identified.", s["body"]))

    # ---- Shelters --------------------------------------------------------
    story.append(Paragraph("8. Shelter Status", s["h2"]))
    shelters = situation.get("shelters") or []
    if shelters:
        rows = [["Shelter", "Location", "Capacity", "Occupancy", "Available", "Utilisation"]]
        for sh in shelters:
            rows.append([
                sh.get("name", "-"), sh.get("location", "-"), sh.get("capacity", "-"),
                sh.get("current_occupancy", "-"), sh.get("available_capacity", "-"),
                f"{sh.get('utilization_pct', 0)}%",
            ])
        story.append(_table(rows, [26 * mm, 45 * mm, 20 * mm, 21 * mm, 22 * mm, 24 * mm], s))
    else:
        story.append(Paragraph("No shelters registered.", s["body"]))

    # ---- Plan ------------------------------------------------------------
    story.append(Paragraph("9. Recommended Actions (pending approval)", s["h2"]))
    plan = situation.get("recommended_plan") or {}
    if plan:
        story.append(
            Paragraph(f"Plan {plan.get('plan_id')} &mdash; status "
                      f"{plan.get('status')}. Trigger: {plan.get('trigger')}.", s["body"])
        )
        if plan.get("summary"):
            story.append(Paragraph(plan["summary"], s["body"]))
        allocations = plan.get("allocations") or []
        if allocations:
            rows = [["Incident", "Resource", "Rationale", "Route"]]
            for a in allocations:
                route_note = (
                    f"{a.get('travel_minutes')} min / {a.get('distance_km')} km "
                    f"({'verified' if a.get('route_verified') else 'UNVERIFIED'})"
                    if a.get("travel_minutes") is not None
                    else "verification required"
                )
                rows.append([
                    a.get("incident_id", "-"),
                    f"{a.get('resource_id', '-')} ({a.get('resource_type', '-')})",
                    a.get("rationale", "-"),
                    route_note,
                ])
            story.append(_table(rows, [22 * mm, 40 * mm, 80 * mm, 32 * mm], s))
        if plan.get("assumptions"):
            story.append(Paragraph("Assumptions used in this plan:", s["body"]))
            story.append(
                Paragraph("<br/>".join(f"&bull; {a}" for a in plan["assumptions"]), s["body"])
            )
    else:
        story.append(Paragraph("No response plan generated.", s["body"]))

    # ---- Pending approvals & actions ------------------------------------
    if payload.get("include_actions", True):
        story.append(Paragraph("10. Pending Human Approvals", s["h2"]))
        approvals = situation.get("pending_approvals") or []
        if approvals:
            rows = [["Request", "Plan", "Requested From", "Status"]]
            for a in approvals:
                rows.append([
                    a.get("request_id", "-"), a.get("plan_id") or a.get("incident_id") or "-",
                    a.get("requested_from", "-"), a.get("status", "-"),
                ])
            story.append(_table(rows, [32 * mm, 34 * mm, 60 * mm, 34 * mm], s))
        else:
            story.append(Paragraph("No approvals pending.", s["body"]))

        story.append(Paragraph("11. Action Tracking", s["h2"]))
        actions = situation.get("actions") or []
        if actions:
            rows = [["Action", "Incident", "Description", "Status", "Assigned"]]
            for a in actions:
                rows.append([
                    a.get("action_id", "-"), a.get("incident_id") or "-",
                    (a.get("description") or "-")[:70],
                    a.get("status", "-"), a.get("assigned_to") or "-",
                ])
            story.append(_table(rows, [24 * mm, 20 * mm, 78 * mm, 24 * mm, 28 * mm], s))
        else:
            story.append(Paragraph("No actions recorded.", s["body"]))

    # ---- Alerts ----------------------------------------------------------
    story.append(Paragraph("12. Active Alerts", s["h2"]))
    alerts = situation.get("active_alerts") or []
    if alerts:
        rows = [["Severity", "Type", "Message"]]
        for al in alerts:
            rows.append([
                al.get("severity", "-"), al.get("alert_type", "-"),
                (al.get("message") or "-")[:95],
            ])
        story.append(_table(rows, [20 * mm, 40 * mm, 110 * mm], s))
    else:
        story.append(Paragraph("No active alerts.", s["body"]))

    # ---- Uncertainties ---------------------------------------------------
    story.append(Paragraph("13. Data Uncertainties &amp; Verification Required", s["h2"]))
    uncertainties = payload.get("uncertainties") or situation.get("uncertainties") or []
    if uncertainties:
        story.append(
            Paragraph(
                "<br/>".join(f"&bull; {u}" for u in uncertainties),
                s["body"],
            )
        )
    else:
        story.append(Paragraph("No outstanding data uncertainties recorded.", s["body"]))

    # ---- Footer ----------------------------------------------------------
    story.append(Spacer(1, 6 * mm))
    story.append(KeepTogether(Paragraph(DISCLAIMER, s["disclaimer"])))
    story.append(
        Paragraph(
            f"Report ID {payload.get('report_id', '-')} &mdash; generated "
            f"{generated_at} by {payload.get('generated_by', 'system')}. "
            "This document is decision support only and does not constitute an "
            "operational order.",
            s["subtitle"],
        )
    )

    doc.build(story)
    return buf.getvalue()