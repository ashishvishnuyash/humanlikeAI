import logging
import os
import json
import time
import uuid
from datetime import datetime
from typing import List
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from openai import OpenAI
from sqlalchemy.orm import Session

from db.session import get_session
from db.models.mental_health import AIRecommendation as AIRecommendationModel, ChatSession
from middleware.usage_tracker import track_usage, tokens_from_openai_completion
from routers.auth import get_current_user
from middleware.usage_tracker import track_usage, tokens_from_openai_completion

_logger = logging.getLogger(__name__)


def _coerce_company_uuid(value: str | None) -> uuid.UUID | None:
    """Convert a company_id string to UUID. If the value is not a valid UUID
    (e.g. Firebase-style 'company_<uid>'), log a warning and return None.

    This preserves existing silent-fallback behavior so production callers
    don't break, but makes the misuse observable in logs."""
    if not value:
        return None
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError):
        _logger.warning("non-UUID company_id received in recommendations: %r", value)
        return None


router = APIRouter(prefix="/recommendations", tags=["Recommendations"], dependencies=[Depends(get_current_user)])

class RecommendationRequest(BaseModel):
    employee_id: str
    company_id: str
    current_mood: int
    current_stress: int
    current_energy: int
    time_available: int

class AIRecommendation(BaseModel):
    id: str
    recommendation_type: str
    title: str
    description: str
    instructions: List[str]
    duration_minutes: int
    difficulty_level: str
    mood_targets: List[str]
    wellness_metrics_affected: List[str]
    ai_generated: bool
    personalized_for_user: bool
    created_at: str

class RecommendationContext(BaseModel):
    current_mood: int
    current_stress: int
    current_energy: int
    time_available: int

class RecommendationResponse(BaseModel):
    success: bool
    recommendations: List[AIRecommendation]
    generated_at: str
    context: RecommendationContext

def get_openai_client():
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="OpenAI API key not configured")
    return OpenAI(api_key=api_key)

def generate_fallback_recommendations(current_mood: int, current_stress: int, current_energy: int, time_available: int) -> List[dict]:
    now = datetime.utcnow().isoformat() + "Z"
    recommendations = []

    if current_stress >= 7:
        recommendations.append({
            "id": f"stress_relief_{int(datetime.now().timestamp()*1000)}",
            "recommendation_type": "breathing",
            "title": "4-7-8 Breathing Exercise",
            "description": "A simple breathing technique to quickly reduce stress and anxiety.",
            "instructions": [
                "Sit comfortably with your back straight",
                "Place the tip of your tongue against the ridge behind your upper teeth",
                "Exhale completely through your mouth",
                "Close your mouth and inhale through your nose for 4 counts",
                "Hold your breath for 7 counts",
                "Exhale through your mouth for 8 counts",
                "Repeat this cycle 4 times"
            ],
            "duration_minutes": min(time_available, 5),
            "difficulty_level": "beginner",
            "mood_targets": ["stress_relief", "calm"],
            "wellness_metrics_affected": ["stress_anxiety", "emotional_regulation"],
            "ai_generated": True,
            "personalized_for_user": True,
            "created_at": now
        })

    if current_energy <= 4:
        recommendations.append({
            "id": f"energy_boost_{int(datetime.now().timestamp()*1000)}",
            "recommendation_type": "exercise",
            "title": "Quick Energy Boost Movement",
            "description": "Gentle movements to increase energy and alertness.",
            "instructions": [
                "Stand up and stretch your arms overhead",
                "Do 10 gentle arm circles forward and backward",
                "March in place for 30 seconds",
                "Do 5-10 gentle squats",
                "Stretch your neck and shoulders",
                "Take 3 deep breaths"
            ],
            "duration_minutes": min(time_available, 3),
            "difficulty_level": "beginner",
            "mood_targets": ["energy_boost", "motivation"],
            "wellness_metrics_affected": ["motivation_engagement", "cognitive_functioning"],
            "ai_generated": True,
            "personalized_for_user": True,
            "created_at": now
        })

    if current_mood <= 4:
        recommendations.append({
            "id": f"mood_lift_{int(datetime.now().timestamp()*1000)}",
            "recommendation_type": "journaling",
            "title": "Gratitude Journaling",
            "description": "Write down positive thoughts to improve your mood and perspective.",
            "instructions": [
                "Find a quiet space to write",
                "List 3 things you're grateful for today",
                "Write about one positive interaction you had",
                "Note one thing you accomplished today",
                "End with a positive affirmation about yourself"
            ],
            "duration_minutes": min(time_available, 10),
            "difficulty_level": "beginner",
            "mood_targets": ["motivation", "calm"],
            "wellness_metrics_affected": ["emotional_tone", "self_esteem"],
            "ai_generated": True,
            "personalized_for_user": True,
            "created_at": now
        })

    recommendations.append({
        "id": f"work_life_balance_{int(datetime.now().timestamp()*1000)}",
        "recommendation_type": "work_life_balance",
        "title": "Mindful Break",
        "description": "Take a mindful break to reset and refocus.",
        "instructions": [
            "Step away from your workspace",
            "Take 3 deep breaths",
            "Notice your surroundings - what do you see, hear, feel?",
            "Set an intention for the rest of your day",
            "Return to work with renewed focus"
        ],
        "duration_minutes": min(time_available, 5),
        "difficulty_level": "beginner",
        "mood_targets": ["focus", "calm"],
        "wellness_metrics_affected": ["work_life_balance_metric", "cognitive_functioning"],
        "ai_generated": True,
        "personalized_for_user": True,
        "created_at": now
    })

    recommendations.append({
        "id": f"meditation_{int(datetime.now().timestamp()*1000)}",
        "recommendation_type": "meditation",
        "title": "Mindfulness Meditation",
        "description": "A brief mindfulness practice to center yourself.",
        "instructions": [
            "Sit comfortably with your eyes closed",
            "Focus on your breath - don't try to change it",
            "When your mind wanders, gently return to your breath",
            "Notice any sounds around you without judgment",
            "Slowly open your eyes and return to the present moment"
        ],
        "duration_minutes": min(time_available, 8),
        "difficulty_level": "beginner",
        "mood_targets": ["calm", "focus"],
        "wellness_metrics_affected": ["emotional_regulation", "stress_anxiety"],
        "ai_generated": True,
        "personalized_for_user": True,
        "created_at": now
    })

    recommendations.append({
        "id": f"social_connection_{int(datetime.now().timestamp()*1000)}",
        "recommendation_type": "social",
        "title": "Reach Out to Someone",
        "description": "Connect with a colleague or friend to boost your mood.",
        "instructions": [
            "Think of someone you haven't connected with recently",
            "Send them a brief, positive message",
            "Ask how they're doing",
            "Share something positive about your day",
            "Express appreciation for their friendship"
        ],
        "duration_minutes": min(time_available, 5),
        "difficulty_level": "beginner",
        "mood_targets": ["motivation", "calm"],
        "wellness_metrics_affected": ["social_connectedness", "emotional_tone"],
        "ai_generated": True,
        "personalized_for_user": True,
        "created_at": now
    })

    return recommendations[:6]

async def get_user_chat_history(user_id: str, days: int, db: Session) -> List[dict]:
    """Fetch recent chat sessions for a user from Postgres."""
    try:
        from datetime import timedelta
        cutoff = datetime.utcnow() - timedelta(days=days)
        sessions = (
            db.query(ChatSession)
            .filter(ChatSession.user_id == user_id, ChatSession.created_at >= cutoff)
            .order_by(ChatSession.created_at.desc())
            .limit(20)
            .all()
        )
        result = []
        for s in sessions:
            result.append({
                "id": str(s.id),
                "user_id": s.user_id,
                "messages": s.messages if isinstance(s.messages, list) else [],
                "created_at": s.created_at,
            })
        return result
    except Exception as e:
        print(f"Error fetching chat history: {e}")
        return []

@router.post("/generate", response_model=RecommendationResponse)
async def generate_recommendations(
    req: RecommendationRequest,
    db: Session = Depends(get_session),
):
    if req.current_mood < 1 or req.current_mood > 10 or req.current_stress < 1 or req.current_stress > 10 or req.current_energy < 1 or req.current_energy > 10:
        raise HTTPException(status_code=400, detail="Mood, stress, and energy values must be between 1 and 10")

    try:
        chat_history = await get_user_chat_history(req.employee_id, 7, db)

        chat_context = "No recent chat history available."
        if chat_history:
            parts = []
            for s in chat_history:
                dt_str = "Unknown"
                if 'created_at' in s and hasattr(s['created_at'], 'strftime'):
                    dt_str = s['created_at'].strftime('%Y-%m-%d')

                messages = " ".join([m.get("content", "") for m in s.get("messages", [])])
                parts.append(f"Session {dt_str}: {messages}")
            chat_context = "\n".join(parts)

        openai_client = get_openai_client()

        prompt = f"""You are an AI wellness coach analyzing an employee's recent chat conversations and current state to generate personalized wellness recommendations.

EMPLOYEE CONTEXT:
- Current Mood: {req.current_mood}/10 (1=very low, 10=excellent)
- Current Stress: {req.current_stress}/10 (1=very low, 10=very high)
- Current Energy: {req.current_energy}/10 (1=very low, 10=very high)
- Time Available: {req.time_available} minutes

RECENT CHAT HISTORY (Last 7 days):
{chat_context}

Based on this information, generate 6 personalized wellness recommendations. Each recommendation should be:
1. Relevant to their current state and chat patterns
2. Actionable within their available time
3. Appropriate for their stress/mood/energy levels
4. Evidence-based wellness practices

Return ONLY a valid JSON array with this exact structure:
[
  {{
    "id": "unique_id_1",
    "recommendation_type": "meditation|journaling|breathing|exercise|sleep|nutrition|social|work_life_balance",
    "title": "Specific, engaging title",
    "description": "Brief description of what this activity involves",
    "instructions": ["Step 1", "Step 2", "Step 3", "Step 4"],
    "duration_minutes": {req.time_available},
    "difficulty_level": "beginner|intermediate|advanced",
    "mood_targets": ["stress_relief", "energy_boost", "focus", "calm", "motivation"],
    "wellness_metrics_affected": ["stress_anxiety", "emotional_tone", "motivation_engagement", "work_life_balance"],
    "ai_generated": true,
    "personalized_for_user": true,
    "created_at": "{datetime.utcnow().isoformat()}Z"
  }}
]

Guidelines:
- Prioritize activities that address their current stress/mood/energy levels
- If chat history shows work stress, include work-life balance activities
- If low energy, include energizing activities
- If high stress, include calming/meditation activities
- Make instructions specific and actionable
- Ensure duration matches their available time
- Include variety in recommendation types"""

        _t0 = time.time()
        completion = openai_client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "You are a professional wellness coach AI that generates personalized, actionable wellness recommendations based on user context and chat history. You must reply strictly with a JSON array."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=2000,
        )
        _latency_ms = int((time.time() - _t0) * 1000)
        _tin, _tout = tokens_from_openai_completion(completion)
        track_usage(
            user_id=req.employee_id,
            company_id=req.company_id,
            feature="recommendation",
            model="gpt-4",
            tokens_in=_tin,
            tokens_out=_tout,
            latency_ms=_latency_ms,
        )

        response_text = completion.choices[0].message.content
        try:
            recommendations = json.loads(response_text)
            if not isinstance(recommendations, list):
                raise ValueError("Response is not a JSON array")
        except:
            recommendations = generate_fallback_recommendations(req.current_mood, req.current_stress, req.current_energy, req.time_available)

    except Exception as e:
        print(f"Error generating AI recommendations: {e}")
        recommendations = generate_fallback_recommendations(req.current_mood, req.current_stress, req.current_energy, req.time_available)

    # Parse company_id — treat invalid/empty strings as None (logs warning for non-UUID values)
    company_uuid = _coerce_company_uuid(req.company_id)

    now_iso = datetime.utcnow().isoformat() + "Z"

    # Persist to Postgres
    try:
        ar = AIRecommendationModel(
            id=uuid.uuid4(),
            user_id=req.employee_id,
            company_id=company_uuid,
            recommendation={
                "recommendations": recommendations,
                "context": {
                    "current_mood": req.current_mood,
                    "current_stress": req.current_stress,
                    "current_energy": req.current_energy,
                    "time_available": req.time_available,
                },
                "generated_at": now_iso,
            },
            category="wellness",
        )
        db.add(ar)
        db.commit()
    except Exception as e:
        print(f"Error saving recommendation to db: {e}")
        db.rollback()

    return {
        "success": True,
        "recommendations": recommendations,
        "generated_at": now_iso,
        "context": {
            "current_mood": req.current_mood,
            "current_stress": req.current_stress,
            "current_energy": req.current_energy,
            "time_available": req.time_available
        }
    }
