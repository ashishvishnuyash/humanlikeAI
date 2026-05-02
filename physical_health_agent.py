"""
Physical Health Agent
======================
LLM pipeline functions for:
  1. Analysing uploaded medical documents (background task)
  2. Generating periodic health reports from check-in aggregates

Called by routers/physical_health.py — never called directly by main.py.
"""

import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from langchain_openai import ChatOpenAI

from db.session import get_session_factory
from middleware.usage_tracker import track_usage, tokens_from_langchain_raw
from db.models.physical_health import (
    MedicalDocument,
    PhysicalHealthCheckin,
    PhysicalHealthReport,
    WellnessEvent,
)
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

# ─── Session factory (agent runs outside FastAPI request cycle) ───────────────

SessionLocal = get_session_factory()


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

def analyze_medical_document(
    raw_text: str,
    user_id: str = "",
    company_id: str = "",
) -> MedicalReportAnalysis:
    """
    Node 1: LLM reads raw medical report text and returns structured findings.
    Uses structured output — returns MedicalReportAnalysis directly.
    """
    llm = _get_llm(temperature=0.1)
    structured = llm.with_structured_output(MedicalReportAnalysis, include_raw=True)
    t0 = time.time()
    raw = (ANALYZE_MEDICAL_REPORT | structured).invoke({"report_text": raw_text})
    _tin, _tout = tokens_from_langchain_raw(raw["raw"])
    track_usage(
        user_id=user_id,
        company_id=company_id,
        feature="physical_health",
        model="gpt-4o-mini",
        tokens_in=_tin,
        tokens_out=_tout,
        latency_ms=int((time.time() - t0) * 1000),
    )
    return raw["parsed"]


def generate_health_suggestions(
    analysis: MedicalReportAnalysis,
    checkin_context: str,
    user_id: str = "",
    company_id: str = "",
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

    t0 = time.time()
    response = llm.invoke(
        GENERATE_HEALTH_SUGGESTIONS.format_messages(
            findings_summary=findings_summary,
            checkin_context=checkin_context or "No recent check-in data available.",
        )
    )
    _meta = getattr(response, "usage_metadata", None) or {}
    track_usage(
        user_id=user_id,
        company_id=company_id,
        feature="physical_health",
        model="gpt-4o-mini",
        tokens_in=int(_meta.get("input_tokens", 0)),
        tokens_out=int(_meta.get("output_tokens", 0)),
        latency_ms=int((time.time() - t0) * 1000),
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
) -> None:
    """
    Background task triggered after a medical file is uploaded.

    Steps:
    1. Analyse with LLM → MedicalReportAnalysis
    2. Ingest text chunks into RAG with medical metadata
    3. Fetch recent check-in context for suggestions
    4. Generate personalised suggestions
    5. Update MedicalDocument extracted_text if empty (text already set at upload)
    6. If urgency_level == "emergency" → write wellness_event for in-app alert

    NOTE: Analysis metadata fields (status, summary, key_findings, etc.) are not
    persisted in the current MedicalDocument schema. Phase 5 will add a metadata
    JSONB column for full persistence.
    """
    try:
        # Step 1 — LLM analysis
        analysis = analyze_medical_document(
            raw_text, user_id=user_id, company_id=company_id
        )

        # Step 2 — RAG ingestion (filtered by user_id so Q&A stays private)
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
            # Persist chunk IDs so delete can clean up Pinecone vectors
            if chunk_ids:
                with SessionLocal() as db:
                    db.query(MedicalDocument).filter(
                        MedicalDocument.id == uuid.UUID(doc_id)
                    ).update({"rag_chunk_ids": chunk_ids})
                    db.commit()
        except Exception as rag_err:
            print(f"[physical_health_agent] RAG ingestion error for {doc_id}: {rag_err}")
            # Non-fatal — analysis still saved without RAG

        # Step 3 — fetch recent check-in context for suggestions
        checkin_context = _build_checkin_context(user_id)

        # Step 4 — personalised suggestions
        # suggestions will be persisted in Phase 5 (metadata JSONB column on MedicalDocument)
        suggestions = generate_health_suggestions(  # noqa: F841
            analysis, checkin_context, user_id=user_id, company_id=company_id
        )

        # Step 5 — update extracted_text if it was empty at upload time
        # (Phase 5 will persist full analysis metadata via a JSONB column)
        try:
            with SessionLocal() as db:
                doc = (
                    db.query(MedicalDocument)
                    .filter(MedicalDocument.id == uuid.UUID(doc_id))
                    .one_or_none()
                )
                if doc is not None and not doc.extracted_text:
                    db.query(MedicalDocument).filter(
                        MedicalDocument.id == uuid.UUID(doc_id)
                    ).update({"extracted_text": raw_text})
                    db.commit()
        except Exception as e:
            print(f"[physical_health_agent] MedicalDocument update error for {doc_id}: {e}")

        # Step 6 — emergency escalation (user-only alert, never visible to employer)
        if analysis.urgency_level == "emergency":
            try:
                cid: Optional[uuid.UUID] = uuid.UUID(company_id) if company_id else None
                with SessionLocal() as db:
                    db.add(WellnessEvent(
                        id=uuid.uuid4(),
                        user_id=user_id,
                        company_id=cid,
                        event_type="medical_emergency_flag",
                        data={
                            "source":  "medical_document",
                            "doc_id":  doc_id,
                            "message": (
                                "Your recently uploaded health document contains findings "
                                "that may require immediate medical attention. "
                                "Please contact a healthcare professional as soon as possible."
                            ),
                            "seen": False,
                        },
                    ))
                    db.commit()
            except Exception as e:
                print(f"[physical_health_agent] wellness_event write error: {e}")

    except Exception as e:
        print(f"[physical_health_agent] process_medical_document failed for {doc_id}: {e}")


# ─── Periodic report generation ──────────────────────────────────────────────

def generate_periodic_report(
    user_id:    str,
    company_id: str,
    aggregates: dict,
    period_str: str,
    period_start: datetime,
    period_end:   datetime,
    report_type:  str,
) -> PeriodicReportResponse:
    """
    Synthesise a period health report from aggregated check-in data
    + any relevant medical context retrieved from RAG.
    Saves result to physical_health_reports table.
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
        f"Average exercise minutes (on active days): {aggregates.get('avg_exercise_minutes_daily', 0):.0f}min\n"
    )

    # Fetch medical context from RAG
    medical_context = "No uploaded medical documents found for this period."
    try:
        store = get_rag_store()
        chunks = store.retrieve(
            query="health findings lab results blood test medical report",
            top_k=4,
            metadata_filter={
                "$and": [
                    {"user_id": {"$eq": user_id}},
                    {"type": {"$eq": "medical_report"}},
                ]
            },
        )
        if chunks:
            medical_context = "\n\n".join(c["text"] for c in chunks)
    except Exception as e:
        print(f"[physical_health_agent] RAG retrieval error in periodic report: {e}")

    # LLM structured generation
    llm = _get_llm(temperature=0.2)
    structured = llm.with_structured_output(_PeriodicReportLLMOutput, include_raw=True)
    _t0  = time.time()
    _raw = (GENERATE_PERIODIC_REPORT | structured).invoke({
        "period":          period_str,
        "metrics_summary": metrics_summary,
        "medical_context": medical_context,
    })
    llm_result = _raw["parsed"]
    _tin, _tout = tokens_from_langchain_raw(_raw["raw"])
    track_usage(
        user_id    = user_id,
        company_id = company_id,
        feature    = "physical_health",
        model      = "gpt-4o-mini",
        tokens_in  = _tin,
        tokens_out = _tout,
        latency_ms = int((time.time() - _t0) * 1000),
    )

    report_id = uuid.uuid4()
    generated_at = datetime.now(timezone.utc)
    cid: Optional[uuid.UUID] = uuid.UUID(company_id) if company_id else None

    # Persist to physical_health_reports
    report_payload = {
        "report_id":                    str(report_id),
        "user_id":                      user_id,
        "company_id":                   str(cid) if cid else None,
        "period_start":                 period_start.isoformat(),
        "period_end":                   period_end.isoformat(),
        "report_type":                  report_type,
        "avg_energy":                   aggregates.get("avg_energy", 0),
        "avg_sleep_quality":            aggregates.get("avg_sleep_quality", 0),
        "avg_sleep_hours":              aggregates.get("avg_sleep_hours", 0),
        "avg_exercise_minutes_per_day": aggregates.get("avg_exercise_minutes_daily", 0),
        "avg_nutrition_quality":        aggregates.get("avg_nutrition_quality", 0),
        "avg_pain_level":               aggregates.get("avg_pain_level", 0),
        "avg_hydration":                aggregates.get("avg_hydration", 0),
        "exercise_days_count":          aggregates.get("exercise_days", 0),
        "overall_score":                llm_result.overall_score,
        "overall_level":                score_to_level(llm_result.overall_score),
        "trend":                        llm_result.trend,
        "summary":                      llm_result.summary,
        "strengths":                    llm_result.strengths,
        "concerns":                     llm_result.concerns,
        "recommendations":              llm_result.recommendations,
        "risk_flags":                   llm_result.risk_flags,
        "follow_up_suggested":          llm_result.follow_up_suggested,
    }

    try:
        with SessionLocal() as db:
            db.add(PhysicalHealthReport(
                id=report_id,
                user_id=user_id,
                company_id=cid,
                report=report_payload,
            ))
            db.commit()
    except Exception as e:
        print(f"[physical_health_agent] DB write error for periodic report: {e}")

    return PeriodicReportResponse(
        report_id=str(report_id),
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

def _build_checkin_context(user_id: str) -> str:
    """
    Build a short text summary of the user's last 7 days of check-ins
    for use in the suggestions prompt. Returns empty string on failure.
    """
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)

        with SessionLocal() as db:
            rows = (
                db.query(PhysicalHealthCheckin)
                .filter(
                    PhysicalHealthCheckin.user_id == user_id,
                    PhysicalHealthCheckin.created_at >= cutoff,
                )
                .order_by(PhysicalHealthCheckin.created_at.desc())
                .all()
            )

        totals = {
            "energy": [], "sleep_quality": [], "sleep_hours": [],
            "nutrition": [], "pain": [], "hydration": [], "exercise_min": [],
        }

        for row in rows:
            v = row.vitals or {}
            totals["energy"].append(v.get("energy_level", 0))
            totals["sleep_quality"].append(v.get("sleep_quality", 0))
            totals["sleep_hours"].append(v.get("sleep_hours", 0))
            totals["nutrition"].append(v.get("nutrition_quality", 0))
            totals["pain"].append(v.get("pain_level", 0))
            totals["hydration"].append(v.get("hydration", 0))
            totals["exercise_min"].append(v.get("exercise_minutes", 0))

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
