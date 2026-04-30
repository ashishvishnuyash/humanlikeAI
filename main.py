"""
Uma — High EQ companion API.

8-node agentic pipeline inspired by rumik.ai's Peek / Mesh / Silk architecture.

Pipeline:
  1. detect_signals    — language, emotion, intensity, tone shift detection
  2. read_subtext      — multi-turn trajectory, "what do they really mean?"
  3. extract_facts     — extract + categorise new facts (identity, preference, emotion, relationship)
  4. recall_memories   — proactively surface relevant past memories
  5. fetch_knowledge   — hybrid (semantic + keyword) knowledge retrieval
  6. plan_response     — choose conversational move + expression style
  7. generate_reply    — produce final reply in Uma's voice with tone/expression control
  8. END
"""

import os
import uuid
import operator

from dotenv import load_dotenv
load_dotenv()
from typing import Annotated, List, TypedDict, Optional
from contextlib import asynccontextmanager

import asyncio

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, END

from rag import get_rag_store
from docx_ingest import ingest_docx_folder
from prompts import (
    DETECT_SIGNALS,
    READ_SUBTEXT,
    EXTRACT_FACTS,
    RECALL_MEMORIES,
    PLAN_RESPONSE,
    build_reply_prompt,
)
from report_api import report_router
from routers.recommendations import router as recommendations_router
from routers.auth import router as auth_router
from routers.chat_wrapper import router as chat_wrapper_router
from routers.community_gamification import router as com_gam_router
from routers.reports_escalation import router as rep_esc_router
from routers.voice_calls import router as voice_calls_router
from routers.users import router as users_router
from routers.employer_dashboard import router as employer_dashboard_router
from routers.employer_org import router as employer_org_router
from routers.employer_insights import router as employer_insights_router, actions_router as employer_actions_router
from routers.employer import router as employer_crud_router
from routers.super_admin import router as super_admin_router
from routers.employee_import import router as employee_import_router
from routers.physical_health import router as physical_health_router
from routers.admin_metrics import router as admin_metrics_router
from middleware.activity_tracker import persist_chat_session
from auth.jwt_utils import InvalidTokenError, decode_access_token
from db.models import User
from db.session import get_session_factory

# Optional bearer for /chat — does not reject unauthenticated requests.
_optional_bearer = HTTPBearer(auto_error=False)

# ═══════════════════════════════════════════════════════════════════════════
# STATE
# ═══════════════════════════════════════════════════════════════════════════


# ... (Skipped lines to find FastAPI app setup) ...



class AgentState(TypedDict):
    messages: Annotated[List[BaseMessage], operator.add]

    # Peek: surface
    language: str
    emotion: str
    emotion_intensity: float       # 0.0–1.0
    tone_shift: str                # e.g. "escalating", "calming", "stable", "flip"

    # Peek: depth
    subtext: str
    deep_need: str
    conversation_phase: str        # "opening", "venting", "seeking", "closing", "playful"

    # Mesh
    new_memories: Annotated[List[str], operator.add]
    memory_categories: Annotated[List[str], operator.add]   # parallel list: category per memory
    recalled_memories: List[str]

    # RAG
    retrieved_context: List[str]

    # Strategy
    response_strategy: str
    expression_style: str          # "warm", "playful", "raw", "gentle", "hype"


# ═══════════════════════════════════════════════════════════════════════════
# STRUCTURED OUTPUTS
# ═══════════════════════════════════════════════════════════════════════════

class PeekAnalysis(BaseModel):
    language: str = Field(description="Primary language / mix e.g. English, Hindi, Hinglish, Spanglish")
    emotion: str = Field(description="Core emotion: Happy, Sad, Angry, Anxious, Tired, Excited, Lonely, Neutral, Confused, Grateful")
    emotion_intensity: float = Field(ge=0, le=1, description="How strong is the emotion (0=barely there, 1=overwhelming)")
    tone_shift: str = Field(description="Compared to conversation so far: escalating, calming, stable, or flip")


class PeekContext(BaseModel):
    subtext: str = Field(description="What they REALLY mean beneath the words")
    deep_need: str = Field(description="One of: Validation, Distraction, Tough Love, Advice, Reassurance, Companionship, Celebration, Space")
    conversation_phase: str = Field(description="One of: opening, venting, seeking, closing, playful, deep_talk, crisis")


class MemoryExtraction(BaseModel):
    facts: List[str] = Field(default_factory=list, description="New permanent facts found (empty list if none)")
    categories: List[str] = Field(default_factory=list, description="Category per fact: identity, preference, emotion_pattern, relationship, life_event, hobby")


class MemoryRecall(BaseModel):
    relevant: List[str] = Field(
        default_factory=list,
        description="Subset of provided memories relevant right now. Empty list if none."
    )


class StrategyPlan(BaseModel):
    strategy: str = Field(description="The conversational move in one sentence")
    expression_style: str = Field(description="One of: warm, playful, raw, gentle, hype, chill, chaotic")


# ═══════════════════════════════════════════════════════════════════════════
# LLMs
# ═══════════════════════════════════════════════════════════════════════════

_internal_llm: Optional[ChatOpenAI] = None
_creative_llm: Optional[ChatOpenAI] = None


def _get_llms():
    global _internal_llm, _creative_llm
    if _internal_llm is None:
        _internal_llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.2)
        _creative_llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.85)
    return _internal_llm, _creative_llm


def _recent_text(messages: List[BaseMessage], n: int = 8) -> str:
    """Format last N messages as readable context for internal nodes."""
    recent = messages[-n:]
    lines = []
    for m in recent:
        role = "User" if isinstance(m, HumanMessage) else "Uma"
        lines.append(f"{role}: {m.content}")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════
# NODE 1 — detect_signals
# ═══════════════════════════════════════════════════════════════════════════

def detect_signals(state: AgentState):
    llm, _ = _get_llms()
    structured = llm.with_structured_output(PeekAnalysis)
    convo = _recent_text(state["messages"], n=6)
    last = state["messages"][-1].content
    out = (DETECT_SIGNALS | structured).invoke({"text": last, "convo": convo})
    return {
        "language": out.language or "English",
        "emotion": out.emotion or "Neutral",
        "emotion_intensity": out.emotion_intensity,
        "tone_shift": out.tone_shift or "stable",
    }


# ═══════════════════════════════════════════════════════════════════════════
# NODE 2 — read_subtext
# ═══════════════════════════════════════════════════════════════════════════

def read_subtext(state: AgentState):
    llm, _ = _get_llms()
    structured = llm.with_structured_output(PeekContext)
    convo = _recent_text(state["messages"], n=10)
    out = (READ_SUBTEXT | structured).invoke({
        "convo": convo,
        "emotion": state["emotion"],
        "intensity": str(state["emotion_intensity"]),
        "shift": state["tone_shift"],
    })
    return {
        "subtext": out.subtext or "",
        "deep_need": out.deep_need or "Companionship",
        "conversation_phase": out.conversation_phase or "opening",
    }


# ═══════════════════════════════════════════════════════════════════════════
# NODE 3 — extract_facts
# ═══════════════════════════════════════════════════════════════════════════

def extract_facts(state: AgentState):
    llm, _ = _get_llms()
    structured = llm.with_structured_output(MemoryExtraction)
    last = state["messages"][-1].content
    existing = state.get("new_memories") or []
    existing_fmt = "\n".join(f"  - {m}" for m in existing) if existing else "  (none yet)"
    out = (EXTRACT_FACTS | structured).invoke({"text": last, "existing": existing_fmt})
    if not out.facts:
        return {}
    return {
        "new_memories": out.facts,
        "memory_categories": out.categories[:len(out.facts)],
    }


# ═══════════════════════════════════════════════════════════════════════════
# NODE 4 — recall_memories
# ═══════════════════════════════════════════════════════════════════════════

def recall_memories(state: AgentState):
    llm, _ = _get_llms()
    structured = llm.with_structured_output(MemoryRecall)
    all_memories = state.get("new_memories") or []
    if not all_memories:
        return {"recalled_memories": []}
    memory_list = "\n".join(f"- {m}" for m in all_memories)
    out = (RECALL_MEMORIES | structured).invoke({
        "memories": memory_list,
        "last": state["messages"][-1].content,
        "need": state["deep_need"],
    })
    return {"recalled_memories": out.relevant[:3]}


# ═══════════════════════════════════════════════════════════════════════════
# NODE 5 — fetch_knowledge
# ═══════════════════════════════════════════════════════════════════════════

def fetch_knowledge(state: AgentState):
    last = state["messages"][-1].content
    store = get_rag_store()
    results = store.retrieve(last, top_k=4)
    return {"retrieved_context": [r["text"] for r in results]}


# ═══════════════════════════════════════════════════════════════════════════
# NODE 6 — plan_response
# ═══════════════════════════════════════════════════════════════════════════

def plan_response(state: AgentState):
    llm, _ = _get_llms()
    structured = llm.with_structured_output(StrategyPlan)
    recalled = state.get("recalled_memories") or []
    knowledge = state.get("retrieved_context") or []
    knowledge_summary = "; ".join(k[:80] for k in knowledge) if knowledge else "none"
    out = (PLAN_RESPONSE | structured).invoke({
        "emotion": state["emotion"],
        "intensity": str(state["emotion_intensity"]),
        "shift": state["tone_shift"],
        "subtext": state["subtext"],
        "need": state["deep_need"],
        "phase": state["conversation_phase"],
        "memories": ", ".join(recalled) if recalled else "none",
        "knowledge": knowledge_summary,
    })
    return {
        "response_strategy": out.strategy or "Just be present",
        "expression_style": out.expression_style or "warm",
    }


# ═══════════════════════════════════════════════════════════════════════════
# NODE 7 — generate_reply
# ═══════════════════════════════════════════════════════════════════════════

def generate_reply(state: AgentState):
    _, creative = _get_llms()
    recalled = state.get("recalled_memories") or []
    knowledge = state.get("retrieved_context") or []
    prompt = build_reply_prompt(
        lang=state["language"],
        emotion=state["emotion"],
        intensity=state["emotion_intensity"],
        phase=state["conversation_phase"],
        strategy=state["response_strategy"],
        style=state["expression_style"],
        recalled=recalled,
        knowledge=knowledge,
    )
    response = (prompt | creative).invoke({"messages": state["messages"]})
    return {"messages": [response]}


# ═══════════════════════════════════════════════════════════════════════════
# GRAPH
# ═══════════════════════════════════════════════════════════════════════════

def _build_graph():
    g = StateGraph(AgentState)
    g.add_node("detect_signals", detect_signals)
    g.add_node("read_subtext", read_subtext)
    g.add_node("extract_facts", extract_facts)
    g.add_node("recall_memories", recall_memories)
    g.add_node("fetch_knowledge", fetch_knowledge)
    g.add_node("plan_response", plan_response)
    g.add_node("generate_reply", generate_reply)

    g.set_entry_point("detect_signals")
    g.add_edge("detect_signals", "read_subtext")
    g.add_edge("read_subtext", "extract_facts")
    g.add_edge("extract_facts", "recall_memories")
    g.add_edge("recall_memories", "fetch_knowledge")
    g.add_edge("fetch_knowledge", "plan_response")
    g.add_edge("plan_response", "generate_reply")
    g.add_edge("generate_reply", END)

    return g.compile()


graph = _build_graph()


def save_graph_png(path: str = "graph.png") -> None:
    """Save a visual PNG of the current pipeline graph using Mermaid.ink API.
    Called automatically on startup — regenerates whenever the graph changes."""
    try:
        png_bytes = graph.get_graph().draw_mermaid_png()
        with open(path, "wb") as f:
            f.write(png_bytes)
        print(f"Graph saved to {path}")
    except Exception as e:
        print(f"Could not save graph PNG (requires internet): {e}")


save_graph_png()


# ═══════════════════════════════════════════════════════════════════════════
# SESSION STORE
# ═══════════════════════════════════════════════════════════════════════════

sessions: dict[str, dict] = {}


def _get_or_create_session(session_id: Optional[str] = None) -> tuple[str, dict]:
    if session_id and session_id in sessions:
        return session_id, sessions[session_id]
    sid = session_id or str(uuid.uuid4())
    sessions[sid] = {"messages": [], "memories": [], "memory_categories": []}
    return sid, sessions[sid]


# ═══════════════════════════════════════════════════════════════════════════
# SCHEMAS
# ═══════════════════════════════════════════════════════════════════════════

class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None


class PeekDetail(BaseModel):
    language: str
    emotion: str
    emotion_intensity: float
    tone_shift: str
    subtext: str
    deep_need: str
    conversation_phase: str


class MeshDetail(BaseModel):
    new_memories: List[str]
    recalled_memories: List[str]


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    peek: PeekDetail
    mesh: MeshDetail
    strategy: str
    expression_style: str
    retrieved_context: List[str]
    total_memories: int


class SessionInfo(BaseModel):
    session_id: str
    message_count: int
    memories: List[str]
    memory_categories: List[str]


# ═══════════════════════════════════════════════════════════════════════════
# FASTAPI
# ═══════════════════════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app_: FastAPI):
    if not os.environ.get("OPENAI_API_KEY"):
        print("WARNING: OPENAI_API_KEY is not set.")
    get_rag_store().load()
    yield


app = FastAPI(
    title="Uma — High EQ Companion API",
    description=(
        "8-node agentic pipeline with Peek (context understanding), "
        "Mesh (memory), Silk (expression), and hybrid RAG."
    ),
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(report_router)
app.include_router(recommendations_router, prefix="/api")
app.include_router(auth_router, prefix="/api")
app.include_router(chat_wrapper_router, prefix="/api")
app.include_router(com_gam_router, prefix="/api")
app.include_router(rep_esc_router, prefix="/api")
app.include_router(voice_calls_router, prefix="/api")
app.include_router(users_router, prefix="/api")

# ── Employer Analytics ──────────────────────────────────────────────────────
app.include_router(employer_dashboard_router, prefix="/api")
app.include_router(employer_org_router, prefix="/api")
app.include_router(employer_insights_router, prefix="/api")
app.include_router(employer_actions_router, prefix="/api")
app.include_router(employer_crud_router, prefix="/api")
app.include_router(super_admin_router, prefix="/api")
app.include_router(employee_import_router, prefix="/api")
app.include_router(physical_health_router, prefix="/api")
app.include_router(admin_metrics_router, prefix="/api")


@app.post("/chat", response_model=ChatResponse)
async def chat(
    req: ChatRequest,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_optional_bearer),
):
    if not os.environ.get("OPENAI_API_KEY"):
        raise HTTPException(500, "OPENAI_API_KEY not configured.")

    # Resolve caller identity (optional auth — anonymous /chat is still allowed).
    uid = "anonymous"
    company_id = ""
    if credentials:
        try:
            claims = decode_access_token(credentials.credentials)
            uid = claims.get("sub") or "anonymous"
            cid = claims.get("company_id")
            if cid:
                company_id = str(cid)
            elif uid != "anonymous":
                # Fallback: look up the company on the user row.
                SessionLocal = get_session_factory()
                with SessionLocal() as s:
                    user = s.query(User).filter(User.id == uid).one_or_none()
                    if user is not None and user.company_id is not None:
                        company_id = str(user.company_id)
        except InvalidTokenError:
            pass  # Non-fatal — fall through as anonymous.
        except Exception:
            pass

    sid, session = _get_or_create_session(req.session_id)
    session["messages"].append(HumanMessage(content=req.message))

    result = graph.invoke({
        "messages": list(session["messages"]),
        "language": "",
        "emotion": "",
        "emotion_intensity": 0.0,
        "tone_shift": "stable",
        "subtext": "",
        "deep_need": "",
        "conversation_phase": "opening",
        "new_memories": list(session.get("memories", [])),
        "memory_categories": list(session.get("memory_categories", [])),
        "recalled_memories": [],
        "retrieved_context": [],
        "response_strategy": "",
        "expression_style": "",
    })

    ai_msg = result["messages"][-1]
    session["messages"].append(ai_msg)

    all_mem = result.get("new_memories", [])
    all_cat = result.get("memory_categories", [])
    session["memories"] = all_mem
    session["memory_categories"] = all_cat

    prev_mem_count = len(session.get("memories", [])) - len(result.get("new_memories", []))
    fresh = result.get("new_memories", [])[prev_mem_count:] if prev_mem_count >= 0 else []

    # Fire-and-forget chat-session persistence (skipped for anonymous callers
    # since chat_sessions.user_id is NOT NULL and we don't write a real user
    # row for them).
    if uid and uid != "anonymous":
        asyncio.create_task(persist_chat_session(
            session_id=sid,
            user_id=uid,
            company_id=company_id,
            message_count=len(session["messages"]),
        ))

    return ChatResponse(
        session_id=sid,
        reply=ai_msg.content,
        peek=PeekDetail(
            language=result.get("language", ""),
            emotion=result.get("emotion", ""),
            emotion_intensity=result.get("emotion_intensity", 0),
            tone_shift=result.get("tone_shift", ""),
            subtext=result.get("subtext", ""),
            deep_need=result.get("deep_need", ""),
            conversation_phase=result.get("conversation_phase", ""),
        ),
        mesh=MeshDetail(
            new_memories=fresh,
            recalled_memories=result.get("recalled_memories", []),
        ),
        strategy=result.get("response_strategy", ""),
        expression_style=result.get("expression_style", ""),
        retrieved_context=result.get("retrieved_context", []),
        total_memories=len(all_mem),
    )


@app.get("/sessions/{session_id}", response_model=SessionInfo)
async def get_session(session_id: str):
    if session_id not in sessions:
        raise HTTPException(404, "Session not found.")
    s = sessions[session_id]
    return SessionInfo(
        session_id=session_id,
        message_count=len(s["messages"]),
        memories=s.get("memories", []),
        memory_categories=s.get("memory_categories", []),
    )


@app.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    if session_id not in sessions:
        raise HTTPException(404, "Session not found.")
    del sessions[session_id]
    return {"detail": "Session deleted."}


@app.get("/health")
async def health():
    store = get_rag_store()
    return {
        "status": "ok",
        "api_key_set": bool(os.environ.get("OPENAI_API_KEY")),
        "rag_chunks": store.count,
    }


# ═══════════════════════════════════════════════════════════════════════════
# RAG CRUD API
# ═══════════════════════════════════════════════════════════════════════════

class AddDocumentsRequest(BaseModel):
    texts: List[str] = Field(description="Text chunks or long documents to add")
    metadata: Optional[List[dict]] = None
    auto_chunk: bool = Field(default=True, description="Split long texts into overlapping chunks")


class AddDocumentsResponse(BaseModel):
    chunk_ids: List[str]
    message: str


@app.post("/rag/documents", response_model=AddDocumentsResponse)
async def rag_add(req: AddDocumentsRequest):
    if not req.texts:
        raise HTTPException(400, "texts cannot be empty")
    if not os.environ.get("OPENAI_API_KEY"):
        raise HTTPException(500, "OPENAI_API_KEY required.")
    store = get_rag_store()
    ids = store.add_documents(req.texts, metadata_per_doc=req.metadata, auto_chunk=req.auto_chunk)
    return AddDocumentsResponse(chunk_ids=ids, message=f"Added {len(ids)} chunk(s).")


@app.get("/rag/documents")
async def rag_list():
    return {"chunks": get_rag_store().list_chunks(), "total": get_rag_store().count}


@app.delete("/rag/documents/{chunk_id}")
async def rag_delete(chunk_id: str):
    if not get_rag_store().delete_chunk(chunk_id):
        raise HTTPException(404, "Chunk not found.")
    return {"detail": "Deleted."}


# ═══════════════════════════════════════════════════════════════════════════
# Psychologist docs — ingest .docx from folder (for fine-tune / RAG knowledge)
# ═══════════════════════════════════════════════════════════════════════════

class IngestDocxRequest(BaseModel):
    folder_path: str = Field(description="Full path to folder containing .docx files")
    pattern: str = Field(default="*.docx", description="Glob pattern for files to ingest")


class IngestDocxResponse(BaseModel):
    files_processed: int
    chunks_added: int
    chunk_ids: List[str]
    errors: List[str]


@app.post("/rag/ingest-docx", response_model=IngestDocxResponse)
async def rag_ingest_docx(req: IngestDocxRequest):
    """Ingest all .docx files from a folder into RAG (e.g. psychology tests, scales, interpretations)."""
    if not os.environ.get("OPENAI_API_KEY"):
        raise HTTPException(500, "OPENAI_API_KEY required.")
    store = get_rag_store()
    n, ids, errs = ingest_docx_folder(
        req.folder_path,
        rag_store=store,
        pattern=req.pattern,
    )
    return IngestDocxResponse(
        files_processed=n,
        chunks_added=len(ids),
        chunk_ids=ids,
        errors=errs,
    )
