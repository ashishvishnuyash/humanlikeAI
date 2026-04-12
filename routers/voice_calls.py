import os
import io
import httpx
from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Response, Depends
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
from google.cloud.firestore_v1 import SERVER_TIMESTAMP
from firebase_config import get_db
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

@router.post("/call", response_model=CallResponse)
async def handle_call(req: CallRequest):
    db = get_db()
    action = req.action
    data = req.callData
    
    if action == 'initiate':
        caller_id = data.get('callerId')
        receiver_id = data.get('receiverId')
        if not caller_id or not receiver_id:
            raise HTTPException(400, "IDs required")
            
        call_doc = {
            'callerId': caller_id,
            'receiverId': receiver_id,
            'callType': data.get('callType', 'voice'),
            'status': 'initiating',
            'startTime': SERVER_TIMESTAMP,
            'metadata': data.get('metadata', {}),
            'createdAt': SERVER_TIMESTAMP,
            'updatedAt': SERVER_TIMESTAMP
        }
        _, ref = db.collection('calls').add(call_doc)
        
        session_doc = {
            'callId': ref.id,
            'callerId': caller_id,
            'receiverId': receiver_id,
            'status': 'initiating',
            'participants': [caller_id, receiver_id],
            'startTime': SERVER_TIMESTAMP,
            'updatedAt': SERVER_TIMESTAMP
        }
        db.collection('callSessions').document(ref.id).set(session_doc)
        return {"success": True, "callId": ref.id, "message": "Call initiated"}
        
    elif action == 'accept':
        cid = data.get('callId')
        if not cid: raise HTTPException(400, "callId required")
        
        db.collection('calls').document(cid).update({'status': 'active', 'answeredAt': SERVER_TIMESTAMP, 'updatedAt': SERVER_TIMESTAMP})
        db.collection('callSessions').document(cid).update({'status': 'active', 'answeredAt': SERVER_TIMESTAMP, 'updatedAt': SERVER_TIMESTAMP})
        return {"success": True, "message": "Call accepted"}
        
    elif action == 'reject':
        cid = data.get('callId')
        reason = data.get('reason', 'rejected')
        if not cid: raise HTTPException(400, "callId required")
        
        db.collection('calls').document(cid).update({'status': 'rejected', 'endTime': SERVER_TIMESTAMP, 'endReason': reason, 'updatedAt': SERVER_TIMESTAMP})
        db.collection('callSessions').document(cid).update({'status': 'rejected', 'endTime': SERVER_TIMESTAMP, 'endReason': reason, 'updatedAt': SERVER_TIMESTAMP})
        return {"success": True, "message": "Call rejected"}
        
    elif action == 'end':
        cid = data.get('callId')
        uid = data.get('userId')
        reason = data.get('reason', 'ended')
        if not cid or not uid: raise HTTPException(400, "callId and userId required")
        
        db.collection('calls').document(cid).update({'status': 'ended', 'endTime': SERVER_TIMESTAMP, 'endReason': reason, 'endedBy': uid, 'updatedAt': SERVER_TIMESTAMP})
        db.collection('callSessions').document(cid).update({'status': 'ended', 'endTime': SERVER_TIMESTAMP, 'endReason': reason, 'endedBy': uid, 'updatedAt': SERVER_TIMESTAMP})
        return {"success": True, "message": "Call ended"}
        
    elif action == 'update_status':
        cid = data.get('callId')
        status = data.get('status')
        if not cid or not status: raise HTTPException(400, "callId and status required")
        
        db.collection('callSessions').document(cid).update({'status': status, 'metadata': data.get('metadata', {}), 'updatedAt': SERVER_TIMESTAMP})
        return {"success": True, "message": "Call status updated"}
        
    raise HTTPException(400, "Invalid action")


@router.post("/text-to-speech", response_class=Response)
async def handle_tts(req: TTSRequest):
    api_key = os.environ.get('ELEVENLABS_API_KEY')
    if not api_key:
        raise HTTPException(500, "ElevenLabs API key not configured.")
        
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
        raise HTTPException(500, "OPENAI_API_KEY required")
        
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
            raise HTTPException(resp.status_code, resp.text)
            
    result = resp.json()
    return {"text": result.get("text", "")}
