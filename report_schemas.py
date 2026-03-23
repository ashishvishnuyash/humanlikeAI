

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import List, Optional

from pydantic import BaseModel, Field



def score_to_level(score: float) -> str:
    """Convert a 0-10 score to low / medium / high."""
    if score < 3:
        return "low"
    elif score < 6:
        return "medium"
    return "high"



class ChatMessage(BaseModel):
    role: str = Field(description="'user' or 'assistant'")
    content: str


class ReportRequest(BaseModel):
    user_id: str = Field(description="Unique ID of the user")
    messages: List[ChatMessage] = Field(
        description="Full chat conversation to analyse"
    )



class MetricOutput(BaseModel):
    """Single metric scored by the LLM."""
    score: float = Field(ge=0, le=10, description="Score from 0 to 10")
    reason: str = Field(description="Brief justification for the score")
    weight: float = Field(
        ge=0, le=1, default=1.0,
        description="Importance weight 0-1 (1 = most important)"
    )


class MentalHealthLLMOutput(BaseModel):
    """Structured output the LLM must return for mental-health analysis."""
    emotional_regulation: MetricOutput
    stress_anxiety: MetricOutput
    motivation_engagement: MetricOutput
    social_connectedness: MetricOutput
    self_esteem: MetricOutput
    cognitive_functioning: MetricOutput
    emotional_tone: MetricOutput
    assertiveness: MetricOutput
    work_life_balance: MetricOutput
    substance_use: MetricOutput

    trend: str = Field(description="One of: improving, stable, declining")
    summary: str = Field(description="2-3 sentence narrative of mental health")
    confidence: float = Field(
        ge=0, le=1,
        description="How confident is the analysis (0-1)"
    )



class PhysicalHealthLLMOutput(BaseModel):
    """Structured output the LLM must return for physical-health analysis."""
    activity: MetricOutput
    nutrition: MetricOutput
    pain: MetricOutput
    lifestyle: MetricOutput
    absenteeism: MetricOutput

    trend: str = Field(description="One of: improving, stable, declining")
    summary: str = Field(description="2-3 sentence narrative of physical health")
    confidence: float = Field(
        ge=0, le=1,
        description="How confident is the analysis (0-1)"
    )


class OverallLLMOutput(BaseModel):
    """Structured output the LLM must return for the final overall report."""
    score: float = Field(ge=0, le=10)
    confidence: float = Field(ge=0, le=1)
    trend: str = Field(description="One of: improving, stable, declining")
    priority: str = Field(description="One of: low, medium, high")

    summary: str = Field(description="Short overall-health summary")
    full_report: str = Field(
        description="Detailed paragraph-style report covering both mental and physical health"
    )

    key_insights: List[str] = Field(description="3-5 bullet-point insights")
    strengths: List[str] = Field(description="2-4 strengths identified")
    risks: List[str] = Field(description="2-4 risk areas identified")
    recommendations: List[str] = Field(description="3-5 actionable recommendations")


class MetricDetail(BaseModel):
    score: float
    level: str
    reason: str
    weight: float


class MetaBlock(BaseModel):
    report_id: str
    user_id: str
    generated_at: str
    version: str = "1.0"


class MentalHealthBlock(BaseModel):
    score: float
    level: str
    confidence: float
    trend: str
    summary: str
    metrics: dict  # keys are metric names → MetricDetail dicts


class PhysicalHealthBlock(BaseModel):
    score: float
    level: str
    confidence: float
    trend: str
    summary: str
    metrics: dict


class OverallBlock(BaseModel):
    score: float
    level: str
    confidence: float
    trend: str
    priority: str
    summary: str
    full_report: str
    key_insights: List[str]
    strengths: List[str]
    risks: List[str]
    recommendations: List[str]


class ReportResponse(BaseModel):
    meta: MetaBlock
    mental_health: MentalHealthBlock
    physical_health: PhysicalHealthBlock
    overall: OverallBlock



def _metric_dict(metric: MetricOutput) -> dict:
    return {
        "score": round(metric.score, 2),
        "level": score_to_level(metric.score),
        "reason": metric.reason,
        "weight": round(metric.weight, 2),
    }


def _weighted_avg(metrics: dict[str, MetricOutput]) -> float:
    total_w = sum(m.weight for m in metrics.values()) or 1
    return round(
        sum(m.score * m.weight for m in metrics.values()) / total_w, 2
    )


def build_report_response(
    user_id: str,
    mental: MentalHealthLLMOutput,
    physical: PhysicalHealthLLMOutput,
    overall: OverallLLMOutput,
) -> ReportResponse:
    """Assemble the final JSON-ready response from LLM outputs."""

    # ---- mental health ----
    mh_metrics = {
        "emotional_regulation": mental.emotional_regulation,
        "stress_anxiety": mental.stress_anxiety,
        "motivation_engagement": mental.motivation_engagement,
        "social_connectedness": mental.social_connectedness,
        "self_esteem": mental.self_esteem,
        "cognitive_functioning": mental.cognitive_functioning,
        "emotional_tone": mental.emotional_tone,
        "assertiveness": mental.assertiveness,
        "work_life_balance": mental.work_life_balance,
        "substance_use": mental.substance_use,
    }
    mh_score = _weighted_avg(mh_metrics)

    # ---- physical health ----
    ph_metrics = {
        "activity": physical.activity,
        "nutrition": physical.nutrition,
        "pain": physical.pain,
        "lifestyle": physical.lifestyle,
        "absenteeism": physical.absenteeism,
    }
    ph_score = _weighted_avg(ph_metrics)

    return ReportResponse(
        meta=MetaBlock(
            report_id=str(uuid.uuid4()),
            user_id=user_id,
            generated_at=datetime.now(timezone.utc).isoformat(),
        ),
        mental_health=MentalHealthBlock(
            score=mh_score,
            level=score_to_level(mh_score),
            confidence=round(mental.confidence, 2),
            trend=mental.trend,
            summary=mental.summary,
            metrics={k: _metric_dict(v) for k, v in mh_metrics.items()},
        ),
        physical_health=PhysicalHealthBlock(
            score=ph_score,
            level=score_to_level(ph_score),
            confidence=round(physical.confidence, 2),
            trend=physical.trend,
            summary=physical.summary,
            metrics={k: _metric_dict(v) for k, v in ph_metrics.items()},
        ),
        overall=OverallBlock(
            score=round(overall.score, 2),
            level=score_to_level(overall.score),
            confidence=round(overall.confidence, 2),
            trend=overall.trend,
            priority=overall.priority,
            summary=overall.summary,
            full_report=overall.full_report,
            key_insights=overall.key_insights,
            strengths=overall.strengths,
            risks=overall.risks,
            recommendations=overall.recommendations,
        ),
    )
