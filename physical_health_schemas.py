"""
Physical Health — Pydantic Schemas
====================================
All request/response models for routers/physical_health.py
and physical_health_agent.py.

Reuses score_to_level from report_schemas.py — do not redefine it.
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


# ─── Check-in ────────────────────────────────────────────────────────────────

class PhysicalCheckInRequest(BaseModel):
    energy_level:      int   = Field(..., ge=1, le=10, description="1=exhausted, 10=fully energised")
    sleep_quality:     int   = Field(..., ge=1, le=10, description="1=terrible, 10=excellent")
    sleep_hours:       float = Field(..., ge=0, le=24, description="Hours slept last night")
    exercise_done:     bool  = Field(...,               description="Did you exercise today?")
    exercise_minutes:  int   = Field(0,  ge=0,          description="Minutes of exercise (0 if none)")
    exercise_type:     str   = Field("none",             description="walk|gym|yoga|sport|other|none")
    nutrition_quality: int   = Field(..., ge=1, le=10,  description="1=poor diet, 10=excellent")
    pain_level:        int   = Field(..., ge=1, le=10,  description="1=severe pain, 10=no pain")
    hydration:         int   = Field(..., ge=1, le=10,  description="1=dehydrated, 10=well-hydrated")
    notes:             Optional[str] = Field(None,       description="Optional free-text note")


class PhysicalCheckInResponse(BaseModel):
    success:    bool
    checkin_id: str
    nudge:      Optional[str] = None   # short personalised tip


# ─── Medical document upload / status ────────────────────────────────────────

class MedicalDocumentUploadResponse(BaseModel):
    success: bool
    doc_id:  str
    status:  str      # always "processing" on upload
    message: str


class MedicalDocumentStatusResponse(BaseModel):
    doc_id:        str
    status:        str            # uploaded | processing | analyzed | failed
    analyzed_at:   Optional[str] = None
    urgency_level: Optional[str] = None   # routine | follow_up | urgent | emergency


# ─── Medical document detail ─────────────────────────────────────────────────

class FlaggedValue(BaseModel):
    name:              str
    value:             str
    normal_range:      str
    status:            str   # high | low | normal | borderline
    plain_explanation: str


class MedicalDocumentDetail(BaseModel):
    doc_id:           str
    filename:         str
    report_type:      str
    report_date:      Optional[str] = None
    issuing_facility: Optional[str] = None
    status:           str
    uploaded_at:      str
    analyzed_at:      Optional[str]       = None
    summary:          Optional[str]       = None
    key_findings:     Optional[List[str]] = None
    flagged_values:   Optional[List[FlaggedValue]] = None
    recommendations:  Optional[List[str]] = None
    follow_up_needed: Optional[bool]      = None
    urgency_level:    str = "routine"


class MedicalDocumentListResponse(BaseModel):
    success:   bool
    documents: List[MedicalDocumentDetail]
    total:     int


# ─── LLM output schema (used by physical_health_agent.py) ────────────────────

class MedicalReportAnalysis(BaseModel):
    """Structured output the LLM must return when analysing a medical document."""
    report_type:      str
    report_date:      Optional[str]  = None
    summary:          str
    key_findings:     List[str]
    flagged_values:   List[FlaggedValue]
    follow_up_needed: bool
    urgency_level:    str             # routine | follow_up | urgent | emergency
    recommendations:  List[str]
    confidence:       float = Field(ge=0, le=1)


# ─── Trends ──────────────────────────────────────────────────────────────────

class TrendPoint(BaseModel):
    date:              str
    energy_level:      Optional[float] = None
    sleep_quality:     Optional[float] = None
    sleep_hours:       Optional[float] = None
    exercise_minutes:  Optional[int]   = None
    nutrition_quality: Optional[float] = None
    pain_level:        Optional[float] = None
    hydration:         Optional[float] = None


class HealthTrendsResponse(BaseModel):
    period:          str
    data_points:     List[TrendPoint]
    averages:        Dict[str, Any]
    trend_direction: Dict[str, str]
    total_checkins:  int


# ─── Current composite score ─────────────────────────────────────────────────

class PhysicalHealthScoreResponse(BaseModel):
    score:              float
    level:              str             # low | medium | high
    last_checkin_date:  Optional[str]  = None
    days_since_checkin: Optional[int]  = None
    streak_days:        int
    highlights:         List[str]       # positive notes
    concerns:           List[str]       # areas needing attention


# ─── Periodic report ─────────────────────────────────────────────────────────

class PeriodicReportRequest(BaseModel):
    report_type: str = Field("on_demand", description="weekly | monthly | on_demand")
    days:        int = Field(30, ge=7, le=365, description="Lookback window in days")


class PeriodicReportResponse(BaseModel):
    report_id:                   str
    period_start:                str
    period_end:                  str
    report_type:                 str
    overall_score:               float
    overall_level:               str
    trend:                       str
    avg_energy:                  float
    avg_sleep_quality:           float
    avg_sleep_hours:             float
    avg_exercise_minutes_daily:  float
    avg_nutrition_quality:       float
    avg_pain_level:              float
    exercise_days:               int
    summary:                     str
    strengths:                   List[str]
    concerns:                    List[str]
    recommendations:             List[str]
    follow_up_suggested:         bool
    generated_at:                str


# ─── Check-in history ────────────────────────────────────────────────────────

class CheckInHistoryItem(BaseModel):
    checkin_id:        str
    created_at:        str
    energy_level:      int
    sleep_quality:     int
    sleep_hours:       float
    exercise_done:     bool
    exercise_minutes:  int
    exercise_type:     str
    nutrition_quality: int
    pain_level:        int
    hydration:         int
    notes:             Optional[str] = None


class CheckInHistoryResponse(BaseModel):
    success:   bool
    checkins:  List[CheckInHistoryItem]
    total:     int
    page:      int
    limit:     int
    totalPages: int
    hasNext:   bool
    hasPrev:   bool


# ─── Medical Q&A ─────────────────────────────────────────────────────────────

class AskRequest(BaseModel):
    question: str = Field(..., min_length=5)


class AskResponse(BaseModel):
    answer:         str
    source_doc_ids: List[str]
    confidence:     float
    disclaimer:     str = (
        "This information is derived from your uploaded documents and is not medical advice. "
        "Always consult a qualified healthcare professional."
    )
