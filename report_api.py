

from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException

from report_schemas import ReportRequest, ReportResponse
from report_agent import run_report


report_router = APIRouter(prefix="/report", tags=["Report Analysis"])


def _format_conversation(messages) -> str:
    """Convert list of ChatMessage into readable text for the LLM."""
    lines = []
    for m in messages:
        label = "User" if m.role.lower() == "user" else "Assistant"
        lines.append(f"{label}: {m.content}")
    return "\n".join(lines)


@report_router.post("/analyze", response_model=ReportResponse)
async def analyze_chat(req: ReportRequest):
    """
    Analyse a chat conversation and generate a comprehensive
    mental + physical health report.
    """
    if not os.environ.get("OPENAI_API_KEY"):
        raise HTTPException(500, "OPENAI_API_KEY not configured.")

    if not req.messages:
        raise HTTPException(400, "messages list cannot be empty.")

    if len(req.messages) < 3:
        raise HTTPException(400, "At least 3 messages are required for a meaningful report.")

    conversation_text = _format_conversation(req.messages)
    report = run_report(user_id=req.user_id, conversation_text=conversation_text)
    return report
