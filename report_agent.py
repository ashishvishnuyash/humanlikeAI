

from __future__ import annotations

from typing import List, Optional, TypedDict

from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, END, START

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
    conversation: str                               # formatted chat text

    # Node outputs (populated during execution)
    mental_health: Optional[MentalHealthLLMOutput]
    physical_health: Optional[PhysicalHealthLLMOutput]
    overall: Optional[OverallLLMOutput]



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



def analyze_mental_health(state: ReportState) -> dict:
    llm = _get_llm()
    structured = llm.with_structured_output(MentalHealthLLMOutput)
    result = (ANALYZE_MENTAL_HEALTH | structured).invoke({
        "conversation": state["conversation"],
    })
    return {"mental_health": result}



def analyze_physical_health(state: ReportState) -> dict:
    llm = _get_llm()
    structured = llm.with_structured_output(PhysicalHealthLLMOutput)
    result = (ANALYZE_PHYSICAL_HEALTH | structured).invoke({
        "conversation": state["conversation"],
    })
    return {"physical_health": result}

 
def generate_overall(state: ReportState) -> dict:
    if state["mental_health"] is None:
        raise ValueError("generate_overall: mental_health analysis missing from state.")
    if state["physical_health"] is None:
        raise ValueError("generate_overall: physical_health analysis missing from state.")

    llm = _get_llm()
    structured = llm.with_structured_output(OverallLLMOutput)
    result = (GENERATE_OVERALL | structured).invoke({
        "conversation": state["conversation"],
        "mental_health_summary": _mental_summary_text(state["mental_health"]),
        "physical_health_summary": _physical_summary_text(state["physical_health"]),
    })
    return {"overall": result}


# ═══════════════════════════════════════════════════════════════════════════
# GRAPH
# ═══════════════════════════════════════════════════════════════════════════

def build_report_graph():
    """Build and compile the 3-node report analysis graph."""
    g = StateGraph(ReportState)

    g.add_node("analyze_mental_health", analyze_mental_health)
    g.add_node("analyze_physical_health", analyze_physical_health)
    g.add_node("generate_overall", generate_overall)

    # Fan-out: both analysis nodes run in parallel
    g.add_edge(START, "analyze_mental_health")
    g.add_edge(START, "analyze_physical_health")

    # Join: generate_overall waits for both branches
    g.add_edge("analyze_mental_health", "generate_overall")
    g.add_edge("analyze_physical_health", "generate_overall")

    g.add_edge("generate_overall", END)

    return g.compile()


report_graph = build_report_graph()



def run_report(user_id: str, conversation_text: str) -> ReportResponse:
    """Run the full report pipeline and return a ReportResponse."""
    result = report_graph.invoke({
        "user_id": user_id,
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
