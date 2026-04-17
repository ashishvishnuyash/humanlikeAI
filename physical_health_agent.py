"""
Physical Health Agent
======================
LLM pipeline functions for:
  1. Analysing uploaded medical documents (background task)
  2. Generating periodic health reports from check-in aggregates

Called by routers/physical_health.py — never called directly by main.py.
"""

import uuid
from datetime import datetime, timezone
from typing import List, Optional

from langchain_openai import ChatOpenAI
from google.cloud.firestore_v1 import SERVER_TIMESTAMP

from physical_health_schemas import (
    MedicalReportAnalysis,
    PeriodicReportResponse,
    FlaggedValue,
)
from physical_health_prompts import (
    ANALYZE_MEDICAL_REPORT,
    GENERATE_HEALTH_SUGGESTIONS,
    GENERATE_PERIODIC_REPORT,
)
from report_schemas import score_to_level
from rag import get_rag_store


# ─── LLM singleton ───────────────────────────────────────────────────────────

def _get_llm(temperature: float = 0.1) -> ChatOpenAI:
    return ChatOpenAI(model="gpt-4o-mini", temperature=temperature)


# ─── Structured output schema for periodic report ────────────────────────────

from pydantic import BaseModel, Field

class _PeriodicReportLLMOutput(BaseModel):
    overall_score:       float = Field(ge=0, le=10)
    trend:               str
    summary:             str
    strengths:           List[str]
    concerns:            List[str]
    recommendations:     List[str]
    risk_flags:          List[str]
    follow_up_suggested: bool


# ─── Medical document analysis ───────────────────────────────────────────────

def analyze_medical_document(raw_text: str) -> MedicalReportAnalysis:
    """
    Node 1: LLM reads raw medical report text and returns structured findings.
    Uses structured output — returns MedicalReportAnalysis directly.
    """
    llm = _get_llm(temperature=0.1)
    structured = llm.with_structured_output(MedicalReportAnalysis)
    result = (ANALYZE_MEDICAL_REPORT | structured).invoke({"report_text": raw_text})
    return result


def generate_health_suggestions(
    analysis: MedicalReportAnalysis,
    checkin_context: str,
) -> List[str]:
    """
    Node 2: LLM generates personalised lifestyle suggestions combining
    medical findings with recent check-in data.
    Returns a list of suggestion strings.
    """
    llm = _get_llm(temperature=0.4)

    flagged_summary = ""
    if analysis.flagged_values:
        lines = [
            f"- {fv.name}: {fv.value} (normal: {fv.normal_range}) — {fv.plain_explanation}"
            for fv in analysis.flagged_values
        ]
        flagged_summary = "Flagged values:\n" + "\n".join(lines)
    else:
        flagged_summary = "No abnormal values found."

    findings_summary = f"{analysis.summary}\n\n{flagged_summary}"

    response = llm.invoke(
        GENERATE_HEALTH_SUGGESTIONS.format_messages(
            findings_summary=findings_summary,
            checkin_context=checkin_context or "No recent check-in data available.",
        )
    )
    raw = response.content.strip()

    # Parse numbered list into individual strings
    suggestions = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        # Strip leading "1. ", "2. ", "- " etc.
        for prefix in ["1.", "2.", "3.", "4.", "5.", "6.", "7.", "-", "•"]:
            if line.startswith(prefix):
                line = line[len(prefix):].strip()
                break
        if line:
            suggestions.append(line)

    return suggestions


# ─── Background processing task ──────────────────────────────────────────────

async def process_medical_document(
    doc_id:      str,
    user_id:     str,
    company_id:  str,
    raw_text:    str,
    report_type: str,
    db,
) -> None:
    """
    Background task triggered after a medical file is uploaded.

    Steps:
    1. Set status → "processing"
    2. Analyse with LLM → MedicalReportAnalysis
    3. Ingest text chunks into RAG with medical metadata
    4. Fetch recent check-in context for suggestions
    5. Generate personalised suggestions
    6. Update Firestore doc with results + status → "analyzed"
    7. If urgency_level == "emergency" → write wellness_event for in-app alert
    """
    doc_ref = db.collection("medical_documents").document(doc_id)

    try:
        # Step 1 — mark as processing
        doc_ref.update({"status": "processing"})

        # Step 2 — LLM analysis
        analysis = analyze_medical_document(raw_text)

        # Step 3 — RAG ingestion (filtered by user_id so Q&A stays private)
        chunk_ids: List[str] = []
        try:
            store = get_rag_store()
            chunk_ids = store.add_documents(
                texts=[raw_text],
                metadata_per_doc=[{
                    "type":        "medical_report",
                    "user_id":     user_id,
                    "doc_id":      doc_id,
                    "report_type": report_type,
                }],
                chunk_size=400,
                chunk_overlap=80,
            )
        except Exception as rag_err:
            print(f"[physical_health_agent] RAG ingestion error for {doc_id}: {rag_err}")
            # Non-fatal — analysis still saved without RAG

        # Step 4 — fetch recent check-in context for suggestions
        checkin_context = _build_checkin_context(user_id, db)

        # Step 5 — personalised suggestions
        suggestions = generate_health_suggestions(analysis, checkin_context)

        # Step 6 — update Firestore with all results
        doc_ref.update({
            "status":           "analyzed",
            "analyzed_at":      SERVER_TIMESTAMP,
            "rag_chunk_ids":    chunk_ids,
            "summary":          analysis.summary,
            "key_findings":     analysis.key_findings,
            "flagged_values":   [fv.model_dump() for fv in analysis.flagged_values],
            "recommendations":  suggestions or analysis.recommendations,
            "follow_up_needed": analysis.follow_up_needed,
            "urgency_level":    analysis.urgency_level,
            "report_type":      analysis.report_type,
            "report_date":      analysis.report_date,
        })

        # Step 7 — emergency escalation (user-only alert, never visible to employer)
        if analysis.urgency_level == "emergency":
            try:
                db.collection("wellness_events").add({
                    "user_id":    user_id,
                    "company_id": company_id,
                    "event_type": "medical_emergency_flag",
                    "source":     "medical_document",
                    "doc_id":     doc_id,
                    "message":    (
                        "Your recently uploaded health document contains findings "
                        "that may require immediate medical attention. "
                        "Please contact a healthcare professional as soon as possible."
                    ),
                    "created_at": SERVER_TIMESTAMP,
                    "seen":       False,
                })
            except Exception as e:
                print(f"[physical_health_agent] wellness_event write error: {e}")

    except Exception as e:
        print(f"[physical_health_agent] process_medical_document failed for {doc_id}: {e}")
        try:
            doc_ref.update({"status": "failed"})
        except Exception:
            pass


# ─── Periodic report generation ──────────────────────────────────────────────

def generate_periodic_report(
    user_id:    str,
    company_id: str,
    aggregates: dict,
    period_str: str,
    period_start: datetime,
    period_end:   datetime,
    report_type:  str,
    db,
) -> PeriodicReportResponse:
    """
    Synthesise a period health report from aggregated check-in data
    + any relevant medical context retrieved from RAG.
    Saves result to physical_health_reports collection.
    """

    # Build metrics summary string for the prompt
    metrics_summary = (
        f"Period: {period_str}\n"
        f"Check-ins recorded: {aggregates.get('total_checkins', 0)}\n"
        f"Average energy level: {aggregates.get('avg_energy', 0):.1f}/10\n"
        f"Average sleep quality: {aggregates.get('avg_sleep_quality', 0):.1f}/10\n"
        f"Average sleep hours: {aggregates.get('avg_sleep_hours', 0):.1f}h\n"
        f"Average nutrition quality: {aggregates.get('avg_nutrition_quality', 0):.1f}/10\n"
        f"Average pain level: {aggregates.get('avg_pain_level', 0):.1f}/10 (10=no pain)\n"
        f"Average hydration: {aggregates.get('avg_hydration', 0):.1f}/10\n"
        f"Days with exercise: {aggregates.get('exercise_days', 0)}\n"
        f"Average exercise minutes (on active days): {aggregates.get('avg_exercise_minutes', 0):.0f}min\n"
    )

    # Fetch medical context from RAG
    medical_context = "No uploaded medical documents found for this period."
    try:
        store = get_rag_store()
        chunks = store.retrieve(
            query="health findings lab results blood test medical report",
            top_k=4,
            metadata_filter={"user_id": user_id, "type": "medical_report"},
        )
        if chunks:
            medical_context = "\n\n".join(c["text"] for c in chunks)
    except Exception as e:
        print(f"[physical_health_agent] RAG retrieval error in periodic report: {e}")

    # LLM structured generation
    llm = _get_llm(temperature=0.2)
    structured = llm.with_structured_output(_PeriodicReportLLMOutput)
    llm_result = (GENERATE_PERIODIC_REPORT | structured).invoke({
        "period":          period_str,
        "metrics_summary": metrics_summary,
        "medical_context": medical_context,
    })

    report_id = str(uuid.uuid4())
    generated_at = datetime.now(timezone.utc)

    # Persist to Firestore
    try:
        db.collection("physical_health_reports").document(report_id).set({
            "report_id":                  report_id,
            "user_id":                    user_id,
            "company_id":                 company_id,
            "generated_at":               SERVER_TIMESTAMP,
            "period_start":               period_start.isoformat(),
            "period_end":                 period_end.isoformat(),
            "report_type":                report_type,
            "avg_energy":                 aggregates.get("avg_energy", 0),
            "avg_sleep_quality":          aggregates.get("avg_sleep_quality", 0),
            "avg_sleep_hours":            aggregates.get("avg_sleep_hours", 0),
            "avg_exercise_minutes_per_day": aggregates.get("avg_exercise_minutes_daily", 0),
            "avg_nutrition_quality":      aggregates.get("avg_nutrition_quality", 0),
            "avg_pain_level":             aggregates.get("avg_pain_level", 0),
            "avg_hydration":              aggregates.get("avg_hydration", 0),
            "exercise_days_count":        aggregates.get("exercise_days", 0),
            "overall_score":              llm_result.overall_score,
            "overall_level":              score_to_level(llm_result.overall_score),
            "trend":                      llm_result.trend,
            "summary":                    llm_result.summary,
            "strengths":                  llm_result.strengths,
            "concerns":                   llm_result.concerns,
            "recommendations":            llm_result.recommendations,
            "risk_flags":                 llm_result.risk_flags,
            "follow_up_suggested":        llm_result.follow_up_suggested,
        })
    except Exception as e:
        print(f"[physical_health_agent] Firestore write error for periodic report: {e}")

    return PeriodicReportResponse(
        report_id=report_id,
        period_start=period_start.isoformat(),
        period_end=period_end.isoformat(),
        report_type=report_type,
        overall_score=round(llm_result.overall_score, 2),
        overall_level=score_to_level(llm_result.overall_score),
        trend=llm_result.trend,
        avg_energy=round(aggregates.get("avg_energy", 0), 2),
        avg_sleep_quality=round(aggregates.get("avg_sleep_quality", 0), 2),
        avg_sleep_hours=round(aggregates.get("avg_sleep_hours", 0), 2),
        avg_exercise_minutes_daily=round(aggregates.get("avg_exercise_minutes_daily", 0), 2),
        avg_nutrition_quality=round(aggregates.get("avg_nutrition_quality", 0), 2),
        avg_pain_level=round(aggregates.get("avg_pain_level", 0), 2),
        exercise_days=aggregates.get("exercise_days", 0),
        summary=llm_result.summary,
        strengths=llm_result.strengths,
        concerns=llm_result.concerns,
        recommendations=llm_result.recommendations,
        follow_up_suggested=llm_result.follow_up_suggested,
        generated_at=generated_at.isoformat(),
    )


# ─── Internal helpers ─────────────────────────────────────────────────────────

def _build_checkin_context(user_id: str, db) -> str:
    """
    Build a short text summary of the user's last 7 days of check-ins
    for use in the suggestions prompt. Returns empty string on failure.
    """
    try:
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        docs = (
            db.collection("physical_health_checkins")
            .where("user_id", "==", user_id)
            .where("created_at", ">=", cutoff)
            .stream()
        )

        totals = {
            "energy": [], "sleep_quality": [], "sleep_hours": [],
            "nutrition": [], "pain": [], "hydration": [], "exercise_min": [],
        }

        for doc in docs:
            d = doc.to_dict()
            totals["energy"].append(d.get("energy_level", 0))
            totals["sleep_quality"].append(d.get("sleep_quality", 0))
            totals["sleep_hours"].append(d.get("sleep_hours", 0))
            totals["nutrition"].append(d.get("nutrition_quality", 0))
            totals["pain"].append(d.get("pain_level", 0))
            totals["hydration"].append(d.get("hydration", 0))
            totals["exercise_min"].append(d.get("exercise_minutes", 0))

        if not totals["energy"]:
            return "No recent check-in data available."

        def avg(lst): return round(sum(lst) / len(lst), 1) if lst else 0

        return (
            f"Last 7 days averages ({len(totals['energy'])} check-ins):\n"
            f"- Energy: {avg(totals['energy'])}/10\n"
            f"- Sleep quality: {avg(totals['sleep_quality'])}/10\n"
            f"- Sleep hours: {avg(totals['sleep_hours'])}h\n"
            f"- Nutrition: {avg(totals['nutrition'])}/10\n"
            f"- Pain (10=no pain): {avg(totals['pain'])}/10\n"
            f"- Hydration: {avg(totals['hydration'])}/10\n"
            f"- Exercise minutes: {avg(totals['exercise_min'])}min/day\n"
        )

    except Exception as e:
        print(f"[physical_health_agent] _build_checkin_context error: {e}")
        return "No recent check-in data available."
