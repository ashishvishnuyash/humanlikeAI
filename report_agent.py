

from __future__ import annotations

import time
from typing import List, Optional, TypedDict

from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, END

from middleware.usage_tracker import track_usage, tokens_from_langchain_raw
from report_prompts import (
    ANALYZE_MENTAL_HEALTH,
    ANALYZE_PHYSICAL_HEALTH,
    GENERATE_OVERALL,
)
from report_schemas import (
    MentalHealthLLMOutput,
    PhysicalHealthLLMOutput,
    OverallLLMOutput,
    ReportResponse,
    build_report_response,
)


class ReportState(TypedDict):
    user_id: str
    company_id: str                                 # for usage tracking
    conversation: str                               # formatted chat text

    # Node outputs (populated during execution)
    mental_health:  Optional[MentalHealthLLMOutput]
    physical_health: Optional[PhysicalHealthLLMOutput]
    overall:        Optional[OverallLLMOutput]



_analysis_llm: Optional[ChatOpenAI] = None


def _get_llm() -> ChatOpenAI:
    global _analysis_llm
    if _analysis_llm is None:
        _analysis_llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.2)
    return _analysis_llm



def _mental_summary_text(m: MentalHealthLLMOutput) -> str:
    """Build a concise text summary of mental-health scores for the overall node."""
    lines = [f"Mental Health Summary: {m.summary}", f"Trend: {m.trend}, Confidence: {m.confidence}"]
    for name in [
        "emotional_regulation", "stress_anxiety", "motivation_engagement",
        "social_connectedness", "self_esteem", "cognitive_functioning",
        "emotional_tone", "assertiveness", "work_life_balance", "substance_use",
    ]:
        metric = getattr(m, name)
        lines.append(f"  {name}: {metric.score}/10 (weight {metric.weight}) — {metric.reason}")
    return "\n".join(lines)


def _physical_summary_text(p: PhysicalHealthLLMOutput) -> str:
    """Build a concise text summary of physical-health scores for the overall node."""
    lines = [f"Physical Health Summary: {p.summary}", f"Trend: {p.trend}, Confidence: {p.confidence}"]
    for name in ["activity", "nutrition", "pain", "lifestyle", "absenteeism"]:
        metric = getattr(p, name)
        lines.append(f"  {name}: {metric.score}/10 (weight {metric.weight}) — {metric.reason}")
    return "\n".join(lines)


def _track(state: ReportState, raw_msg, node_name: str, latency_ms: int) -> None:
    """Extract token counts from a LangChain AIMessage and fire usage_log write."""
    try:
        tin, tout = tokens_from_langchain_raw(raw_msg)
        track_usage(
            user_id=state.get("user_id", ""),
            company_id=state.get("company_id", ""),
            feature="report",
            model="gpt-4o-mini",
            tokens_in=tin,
            tokens_out=tout,
            latency_ms=latency_ms,
        )
    except Exception as e:
        print(f"[report_agent] usage tracking error ({node_name}): {e}")


def _track(state: ReportState, raw_msg, node_name: str, latency_ms: int) -> None:
    """Extract token counts from a LangChain AIMessage and fire usage log."""
    try:
        from middleware.usage_tracker import track_usage, tokens_from_langchain_raw
        from firebase_config import get_db
        tin, tout = tokens_from_langchain_raw(raw_msg)
        track_usage(
            user_id    = state.get("user_id", ""),
            company_id = state.get("company_id", ""),
            feature    = "report",
            model      = "gpt-4o-mini",
            tokens_in  = tin,
            tokens_out = tout,
            db         = get_db(),
            latency_ms = latency_ms,
        )
    except Exception as e:
        print(f"[report_agent] usage tracking error ({node_name}): {e}")


def analyze_mental_health(state: ReportState) -> dict:
    llm = _get_llm()
    structured = llm.with_structured_output(MentalHealthLLMOutput, include_raw=True)
    t0 = time.time()
    raw = (ANALYZE_MENTAL_HEALTH | structured).invoke({"conversation": state["conversation"]})
    _track(state, raw["raw"], "analyze_mental_health", int((time.time() - t0) * 1000))
    return {"mental_health": raw["parsed"]}



def analyze_physical_health(state: ReportState) -> dict:
    llm = _get_llm()
    structured = llm.with_structured_output(PhysicalHealthLLMOutput, include_raw=True)
    t0 = time.time()
    raw = (ANALYZE_PHYSICAL_HEALTH | structured).invoke({"conversation": state["conversation"]})
    _track(state, raw["raw"], "analyze_physical_health", int((time.time() - t0) * 1000))
    return {"physical_health": raw["parsed"]}


def generate_overall(state: ReportState) -> dict:
    llm = _get_llm()
    structured = llm.with_structured_output(OverallLLMOutput, include_raw=True)
    t0 = time.time()
    raw = (GENERATE_OVERALL | structured).invoke({
        "conversation": state["conversation"],
        "mental_health_summary": _mental_summary_text(state["mental_health"]),
        "physical_health_summary": _physical_summary_text(state["physical_health"]),
    })
    _track(state, raw["raw"], "generate_overall", int((time.time() - t0) * 1000))
    return {"overall": raw["parsed"]}


# ═══════════════════════════════════════════════════════════════════════════
# GRAPH
# ═══════════════════════════════════════════════════════════════════════════

def build_report_graph():
    """Build and compile the 3-node report analysis graph."""
    g = StateGraph(ReportState)

    g.add_node("analyze_mental_health", analyze_mental_health)
    g.add_node("analyze_physical_health", analyze_physical_health)
    g.add_node("generate_overall", generate_overall)

    g.set_entry_point("analyze_mental_health")
    g.add_edge("analyze_mental_health", "analyze_physical_health")
    g.add_edge("analyze_physical_health", "generate_overall")
    g.add_edge("generate_overall", END)

    return g.compile()


report_graph = build_report_graph()



def run_report(user_id: str, conversation_text: str, company_id: str = "") -> ReportResponse:
    """Run the full report pipeline and return a ReportResponse."""
    result = report_graph.invoke({
        "user_id": user_id,
        "company_id": company_id,
        "conversation": conversation_text,
        "mental_health": None,
        "physical_health": None,
        "overall": None,
    })
    return build_report_response(
        user_id=user_id,
        mental=result["mental_health"],
        physical=result["physical_health"],
        overall=result["overall"],
    )
