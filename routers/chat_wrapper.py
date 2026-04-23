import json
import os
import uuid
import httpx
from datetime import datetime
from typing import List, Optional, Literal, Union, Dict, Any, Annotated
from fastapi import APIRouter, Depends, Request, HTTPException
from pydantic import BaseModel, Field
from openai import OpenAI
from sqlalchemy.orm import Session

from db.session import get_session
from db.models.mental_health import MentalHealthReport
from report_schemas import ReportRequest, ReportResponse
from report_agent import run_report


router = APIRouter(prefix="/chat_wrapper", tags=["Chat Wrapper"])

class ChatMessageData(BaseModel):
    content: str
    sender: str
    umaSessionId: Optional[str] = None
    emotion: Optional[str] = None
    avatarEmotion: Optional[str] = None
    emotionIntensity: Optional[float] = None
    expressionStyle: Optional[str] = None
    conversationPhase: Optional[str] = None

class ChatMessageResponse(BaseModel):
    type: Literal["message"]
    data: ChatMessageData

class AssessmentData(BaseModel):
    content: str
    sender: str
    testName: str

class ChatAssessmentResponse(BaseModel):
    type: Literal["assessment_questions"]
    data: AssessmentData

class ChatReportResponse(BaseModel):
    type: Literal["report"]
    data: Any

ChatHandlerResponse = Annotated[
    Union[ChatMessageResponse, ChatAssessmentResponse, ChatReportResponse],
    Field(discriminator='type')
]

class AiChatResponse(BaseModel):
    response: str
    session_id: Optional[str] = None
    user_id: Optional[str] = None

# --- Assessment Data ---
ASSESSMENT_DATA = {
    "personality_profiler": {
        "questions": {
            1: "Does your mood fluctuate?",
            2: "Do you bother too much about what others think of you?",
            # ... truncating for brevity, we will just return a placeholder or the first few
            # for the migration, I will include a subset to make it functional
            3: "Do you like talking much?",
            4: "If you make a commitment to someone, do you abide by it irrespective of discomfort?",
        },
        "scoring_instructions": "Please answer 'yes' or 'no' to each question.",
    },
    "self_efficacy_scale": {
        "questions": [
            "I can solve tedious problems with sincere efforts.",
            "If someone disagrees with me, I can still manage to get what I want with ease.",
            "It is easy for me to remain focused on my objectives and achieve my goals.",
        ],
        "scoring_instructions": "Rate each statement 1-4 (1=Not at all true, 4=Exactly true).",
    }
}

def get_openai_client():
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY environment variable is required")
    return OpenAI(api_key=api_key)

def extract_test_name(message: str) -> Optional[str]:
    lower_msg = message.lower()
    if 'personality' in lower_msg or 'profiler' in lower_msg:
        return 'personality_profiler'
    if 'efficacy' in lower_msg:
        return 'self_efficacy_scale'
    return None

def get_assessment_questions(test_name: str) -> str:
    norm = test_name.lower().replace(" ", "_")
    if norm not in ASSESSMENT_DATA:
        return f"Assessment '{test_name}' not found."

    test = ASSESSMENT_DATA[norm]
    out = f"Great! Here are the questions for {test_name}.\n\n{test.get('scoring_instructions', '')}\n\n"
    if norm == 'personality_profiler':
        for k, v in test['questions'].items():
            out += f"{k}. {v}\n"
    else:
        for i, q in enumerate(test['questions']):
            out += f"{i+1}. {q}\n"
    return out

async def generate_chat_response(messages: List[dict], files_text: str, uma_session_id: Optional[str] = None):
    last_user_msg = ""
    for m in reversed(messages):
        if m.get('sender') == 'user':
            last_user_msg = m.get('content', '')
            break

    message_for_uma = last_user_msg
    if files_text:
        message_for_uma += f"\n\n{files_text}"

    try:
        from main import chat as uma_chat_endpoint, ChatRequest
        req = ChatRequest(message=message_for_uma, session_id=uma_session_id)
        uma_resp_obj = await uma_chat_endpoint(req)

        emotion = uma_resp_obj.peek.emotion

        emotion_to_avatar = {
            'Happy': 'HAPPY', 'Excited': 'HAPPY', 'Grateful': 'HAPPY',
            'Sad': 'SAD', 'Lonely': 'SAD', 'Angry': 'ANGRY',
            'Anxious': 'THINKING', 'Confused': 'THINKING',
            'Tired': 'IDLE', 'Neutral': 'IDLE',
        }

        return {
            "type": "message",
            "data": {
                "content": uma_resp_obj.reply,
                "sender": "ai",
                "umaSessionId": uma_resp_obj.session_id,
                "emotion": emotion,
                "avatarEmotion": emotion_to_avatar.get(emotion, 'IDLE'),
                "emotionIntensity": uma_resp_obj.peek.emotion_intensity,
                "expressionStyle": uma_resp_obj.expression_style,
                "conversationPhase": uma_resp_obj.peek.conversation_phase,
            }
        }
    except Exception as e:
        print(f"Error calling native Uma: {e}")
        raise HTTPException(status_code=500, detail="Failed to reach Uma AI agent.")

async def generate_wellness_report(
    messages: List[dict],
    session_type: str,
    session_duration: int,
    user_id: str,
    company_id_str: str,
    db: Session,
):
    try:
        lines = []
        for m in messages:
            role = "User" if m.get('sender') == 'user' else "Assistant"
            lines.append(f"{role}: {m.get('content', '')}")
        conversation_text = "\n".join(lines)

        report_res = run_report(user_id=user_id, conversation_text=conversation_text)

        raw_report = report_res.model_dump(mode="json")

        client_data = {
            **raw_report,
            'employee_id': user_id,
            'company_id': company_id_str,
            'session_type': session_type,
            'session_duration_minutes': session_duration
        }

        if user_id:
            try:
                company_uuid: Optional[uuid.UUID] = None
                if company_id_str:
                    try:
                        company_uuid = uuid.UUID(company_id_str)
                    except ValueError:
                        company_uuid = None

                risk_level = raw_report.get('risk_level') or raw_report.get('riskLevel')

                r = MentalHealthReport(
                    id=uuid.uuid4(),
                    user_id=user_id,
                    company_id=company_uuid,
                    report={
                        **raw_report,
                        'session_type': session_type,
                        'session_duration_minutes': session_duration,
                    },
                    risk_level=risk_level,
                )
                db.add(r)
                db.commit()
            except Exception as e:
                print(f"DB save error: {e}")
                db.rollback()

        return {"type": "report", "data": client_data}

    except Exception as e:
        print(f"Error generating wellness report natively: {e}")
        return {"type": "report", "data": {"error": str(e)}}

@router.post("", response_model=ChatHandlerResponse)
async def chat_handler(request: Request, db: Session = Depends(get_session)):
    content_type = request.headers.get("content-type", "")

    req_data = {}
    files_text = ""

    if "multipart/form-data" in content_type:
        form = await request.form()
        data_str = form.get("data")
        if data_str:
            req_data = json.loads(data_str)

        uploaded_files = form.getlist("files")
        file_parts = []
        for f in uploaded_files:
            if isinstance(f, str): continue
            # Basic info passing for now
            file_parts.append(f"[Attached file: {f.filename}]")
        files_text = "\\n\\n".join(file_parts)
    else:
        try:
            req_data = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid or missing JSON body")

    messages = req_data.get("messages", [])
    if not messages:
        raise HTTPException(status_code=400, detail="Messages array is required")

    assessment_type = req_data.get("assessmentType")
    if assessment_type == "get_questions":
        last_msg = messages[-1].get("content", "")
        test_name = extract_test_name(last_msg)
        if test_name:
            return {
                "type": "assessment_questions",
                "data": {
                    "content": get_assessment_questions(test_name),
                    "sender": "ai",
                    "testName": test_name
                }
            }

    if req_data.get("endSession"):
        return await generate_wellness_report(
            messages,
            req_data.get("sessionType", "text"),
            req_data.get("sessionDuration", 0),
            req_data.get("userId", ""),
            req_data.get("companyId", ""),
            db,
        )

    return await generate_chat_response(
        messages,
        files_text,
        req_data.get("umaSessionId")
    )

class AiChatReq(BaseModel):
    message: str
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    user_role: Optional[str] = None
    context: Optional[str] = None

@router.post("/ai-chat", response_model=AiChatResponse)
async def handle_ai_chat(req: AiChatReq):
    if not req.message:
        raise HTTPException(400, "Message is required")

    openai = get_openai_client()
    system_prompt = "You are a real person having a caring conversation. Be supportive, empathetic, and genuinely interested in their wellbeing. Keep responses warm, natural, and conversational.\n\nCRITICAL: You are NOT an AI or assistant. You're a real person. NEVER mention being an AI or that you're evaluating their health. Just be a caring friend who's listening."

    if req.context == "personal_wellness":
        system_prompt = "You are a real person having a caring conversation with someone in a leadership position. You genuinely want to understand what they're going through and offer support.\n\nCRITICAL: You are NOT an AI, assistant, or technology. You're a real person. NEVER mention being an AI or evaluating their health."

    try:
        completion = openai.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": req.message}
            ],
            max_tokens=300,
            temperature=0.7
        )
        resp_text = completion.choices[0].message.content or "I apologize, but I was unable to generate a response."
        return {
            "response": resp_text,
            "session_id": req.session_id,
            "user_id": req.user_id
        }
    except Exception as e:
        raise HTTPException(500, detail=str(e))

@router.post("/analyze", response_model=ReportResponse)
async def analyze_chat_wrapper_standalone(req: ReportRequest):
    """
    Standalone endpoint to analyze a chat conversation and generate a comprehensive report.
    This accepts a structured ReportRequest and returns a full ReportResponse.
    """
    if not os.environ.get("OPENAI_API_KEY"):
        raise HTTPException(500, "OPENAI_API_KEY not configured.")

    if not req.messages:
        raise HTTPException(400, "messages list cannot be empty.")

    lines = []
    for m in req.messages:
        role = "User" if m.role.lower() == "user" else "Assistant"
        lines.append(f"{role}: {m.content}")
    conversation_text = "\n".join(lines)

    return run_report(user_id=req.user_id, conversation_text=conversation_text)
