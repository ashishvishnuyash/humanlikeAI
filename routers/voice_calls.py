import os
import uuid
import httpx
from datetime import datetime
from fastapi import APIRouter, HTTPException, UploadFile, File, Depends, Response
from pydantic import BaseModel
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session

from db.session import get_session
from db.models.calls import Call, CallSession
from routers.auth import get_current_user

router = APIRouter(tags=["Voice Calls"], dependencies=[Depends(get_current_user)])


class CallRequest(BaseModel):
    action: str
    callData: Dict[str, Any]


class TTSRequest(BaseModel):
    text: str
    voice: Optional[str] = 'pNInz6obpgDQGcFmaJgB'
    addEmotion: Optional[bool] = True


class CallResponse(BaseModel):
    success: bool
    message: str
    callId: Optional[str] = None


class TranscribeResponse(BaseModel):
    text: str


def _parse_uuid(value: str, field: str = "id") -> uuid.UUID:
    """Convert a string to UUID, raising HTTP 400 on failure."""
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail=f"Invalid {field}: {value!r}")


@router.post("/call", response_model=CallResponse)
async def handle_call(req: CallRequest, db: Session = Depends(get_session)):
    action = req.action
    data = req.callData

    if action == 'initiate':
        caller_id = data.get('callerId')
        receiver_id = data.get('receiverId')
        if not caller_id or not receiver_id:
            raise HTTPException(status_code=400, detail="IDs required")

        call = Call(
            id=uuid.uuid4(),
            caller_id=caller_id,
            callee_id=receiver_id,
            status='initiating',
        )
        db.add(call)
        db.commit()
        call_id = str(call.id)

        session = CallSession(
            id=call.id,
            call_id=call.id,
            status='initiating',
            call_metadata={
                'callerId': caller_id,
                'receiverId': receiver_id,
                'callType': data.get('callType', 'voice'),
                'participants': [caller_id, receiver_id],
                'metadata': data.get('metadata', {}),
            },
        )
        db.add(session)
        db.commit()

        return {"success": True, "callId": call_id, "message": "Call initiated"}

    elif action == 'accept':
        cid = data.get('callId')
        if not cid:
            raise HTTPException(status_code=400, detail="callId required")
        call_uuid = _parse_uuid(cid, "callId")

        db.query(Call).filter(Call.id == call_uuid).update(
            {'status': 'active', 'answered_at': datetime.utcnow()}
        )
        db.query(CallSession).filter(CallSession.id == call_uuid).update(
            {'status': 'active'}
        )
        db.commit()
        return {"success": True, "message": "Call accepted"}

    elif action == 'reject':
        cid = data.get('callId')
        reason = data.get('reason', 'rejected')
        if not cid:
            raise HTTPException(status_code=400, detail="callId required")
        call_uuid = _parse_uuid(cid, "callId")

        db.query(Call).filter(Call.id == call_uuid).update(
            {'status': 'rejected', 'end_reason': reason}
        )
        db.query(CallSession).filter(CallSession.id == call_uuid).update(
            {'status': 'rejected'}
        )
        db.commit()
        return {"success": True, "message": "Call rejected"}

    elif action == 'end':
        cid = data.get('callId')
        uid = data.get('userId')
        reason = data.get('reason', 'ended')
        if not cid or not uid:
            raise HTTPException(status_code=400, detail="callId and userId required")
        call_uuid = _parse_uuid(cid, "callId")

        db.query(Call).filter(Call.id == call_uuid).update(
            {'status': 'ended', 'end_reason': reason, 'ended_by': uid}
        )
        db.query(CallSession).filter(CallSession.id == call_uuid).update(
            {'status': 'ended'}
        )
        db.commit()
        return {"success": True, "message": "Call ended"}

    elif action == 'update_status':
        cid = data.get('callId')
        status = data.get('status')
        if not cid or not status:
            raise HTTPException(status_code=400, detail="callId and status required")
        call_uuid = _parse_uuid(cid, "callId")

        cs = db.query(CallSession).filter(CallSession.id == call_uuid).first()
        if cs:
            merged_metadata = dict(cs.call_metadata or {})
            merged_metadata.update(data.get('metadata', {}))
            db.query(CallSession).filter(CallSession.id == call_uuid).update(
                {'status': status, 'call_metadata': merged_metadata}
            )
            db.commit()
        return {"success": True, "message": "Call status updated"}

    raise HTTPException(status_code=400, detail="Invalid action")


@router.post("/text-to-speech", response_class=Response)
async def handle_tts(req: TTSRequest):
    api_key = os.environ.get('ELEVENLABS_API_KEY')
    if not api_key:
        raise HTTPException(status_code=500, detail="ElevenLabs API key not configured.")

    # We skip emotion tagging in Python for low-latency if not needed,
    # but send direct text to ElevenLabs Flash model via HTTP

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{req.voice}"
    headers = {
        "Accept": "audio/mpeg",
        "Content-Type": "application/json",
        "xi-api-key": api_key
    }

    payload = {
        "text": req.text,
        "model_id": "eleven_flash_v2_5",
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75,
            "style": 0.0,
            "use_speaker_boost": True
        }
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)

    return Response(content=resp.content, media_type="audio/mpeg", headers={"Cache-Control": "public, max-age=3600"})


@router.post("/transcribe", response_model=TranscribeResponse)
async def handle_transcribe(audio: UploadFile = File(...)):
    api_key = os.environ.get('OPENAI_API_KEY')
    if not api_key:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY required")

    # Send HTTP request to OpenAI Whisper API
    url = "https://api.openai.com/v1/audio/transcriptions"
    headers = {
        "Authorization": f"Bearer {api_key}"
    }

    contents = await audio.read()
    files = {
        "file": (audio.filename or "audio.webm", contents, audio.content_type or "audio/webm")
    }
    data = {
        "model": "whisper-1",
        "language": "en",
        "response_format": "json"
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(url, headers=headers, data=data, files=files, timeout=30.0)
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)

    result = resp.json()
    return {"text": result.get("text", "")}
