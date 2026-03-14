"""
Uma — High EQ companion API.

8-node agentic pipeline inspired by rumik.ai's Peek / Mesh / Silk architecture.

Pipeline:
  1. peek_analyzer     — language, emotion, intensity, tone shift detection
  2. peek_context      — multi-turn trajectory, "what do they really mean?"
  3. mesh_memory       — extract + categorise new facts (identity, preference, emotion, relationship)
  4. mesh_recall       — proactively surface relevant past memories
  5. rag_retrieval     — hybrid (semantic + keyword) knowledge retrieval
  6. strategist        — choose conversational move + expression style
  7. silk_generator    — produce final reply in Uma's voice with tone/expression control
  8. END
"""

import os
import uuid
import operator
from typing import Annotated, List, TypedDict, Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, END

from rag import get_rag_store


# ═══════════════════════════════════════════════════════════════════════════
# STATE
# ═══════════════════════════════════════════════════════════════════════════

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
# NODE 1 — PEEK: ANALYZER  (surface-level read)
# ═══════════════════════════════════════════════════════════════════════════

def peek_analyzer(state: AgentState):
    llm, _ = _get_llms()
    structured = llm.with_structured_output(PeekAnalysis)

    convo = _recent_text(state["messages"], n=6)
    last = state["messages"][-1].content

    prompt = ChatPromptTemplate.from_template(
        "You read conversations the way a best friend does — not just words, but vibes.\n\n"
        "Recent conversation:\n{convo}\n\n"
        "Latest message: \"{text}\"\n\n"
        "Analyse:\n"
        "- LANGUAGE: exact language or mix (Hinglish, Spanglish, etc.).\n"
        "- EMOTION: the core feeling (not just 'Neutral' — dig deeper. Is it bored? restless? nostalgic?).\n"
        "- INTENSITY: 0.0 (barely there) to 1.0 (overwhelming). A casual 'lol' is ~0.2. A 'I can't do this anymore' is ~0.9.\n"
        "- TONE SHIFT: compared to the last few messages — escalating, calming, stable, or flip (sudden change)."
    )

    out = (prompt | structured).invoke({"text": last, "convo": convo})
    return {
        "language": out.language or "English",
        "emotion": out.emotion or "Neutral",
        "emotion_intensity": out.emotion_intensity,
        "tone_shift": out.tone_shift or "stable",
    }


# ═══════════════════════════════════════════════════════════════════════════
# NODE 2 — PEEK: CONTEXT  (the "same words, different meanings" engine)
# ═══════════════════════════════════════════════════════════════════════════

def peek_context(state: AgentState):
    llm, _ = _get_llms()
    structured = llm.with_structured_output(PeekContext)

    convo = _recent_text(state["messages"], n=10)
    emotion = state["emotion"]
    intensity = state["emotion_intensity"]
    shift = state["tone_shift"]

    prompt = ChatPromptTemplate.from_template(
        "You are the part of a best friend's brain that reads between the lines.\n"
        "The same words mean different things in different contexts:\n"
        "- 'I'm fine' after a breakup = NOT fine.\n"
        "- 'I'm fine' after good news = genuinely fine.\n"
        "- 'haha' after being teased = might be hurt.\n"
        "- 'whatever' can be anger, resignation, or genuine indifference.\n\n"
        "Conversation so far:\n{convo}\n\n"
        "Detected: emotion={emotion}, intensity={intensity}, tone_shift={shift}\n\n"
        "Now determine:\n"
        "- SUBTEXT: What are they ACTUALLY communicating? What's the thing they won't say out loud?\n"
        "- DEEP_NEED: What does their soul need right now? (Validation, Distraction, Tough Love, Advice, Reassurance, Companionship, Celebration, Space)\n"
        "- CONVERSATION_PHASE: Where are we in the emotional arc? (opening, venting, seeking, closing, playful, deep_talk, crisis)"
    )

    out = (prompt | structured).invoke({
        "convo": convo, "emotion": emotion,
        "intensity": str(intensity), "shift": shift,
    })
    return {
        "subtext": out.subtext or "",
        "deep_need": out.deep_need or "Companionship",
        "conversation_phase": out.conversation_phase or "opening",
    }


# ═══════════════════════════════════════════════════════════════════════════
# NODE 3 — MESH: MEMORY EXTRACT  (knows what to remember and what to forget)
# ═══════════════════════════════════════════════════════════════════════════

def mesh_memory(state: AgentState):
    llm, _ = _get_llms()
    structured = llm.with_structured_output(MemoryExtraction)

    last = state["messages"][-1].content
    existing = state.get("new_memories") or []

    prompt = ChatPromptTemplate.from_template(
        "You are a memory system for a best friend.\n"
        "Extract ONLY permanent, reusable facts from this message. Skip pleasantries and transient feelings.\n\n"
        "Message: \"{text}\"\n"
        "Already known: {existing}\n\n"
        "Categories: identity (name, age, gender), preference (likes, dislikes, favorites), "
        "emotion_pattern (recurring feelings), relationship (people they mention), "
        "life_event (job change, breakup, achievement), hobby (activities, interests).\n\n"
        "If nothing new worth remembering, return empty lists."
    )

    out = (prompt | structured).invoke({"text": last, "existing": str(existing)})

    if not out.facts:
        return {}

    return {
        "new_memories": out.facts,
        "memory_categories": out.categories[:len(out.facts)],
    }


# ═══════════════════════════════════════════════════════════════════════════
# NODE 4 — MESH: RECALL  (proactively bring up what matters)
# ═══════════════════════════════════════════════════════════════════════════

def mesh_recall(state: AgentState):
    llm, _ = _get_llms()
    all_memories = state.get("new_memories") or []
    if not all_memories:
        return {"recalled_memories": []}

    last = state["messages"][-1].content
    need = state["deep_need"]

    response = llm.invoke(
        f"You are a memory recall system. Given these stored facts about a user:\n"
        f"{chr(10).join(f'- {m}' for m in all_memories)}\n\n"
        f"The user just said: \"{last}\" and their need is: {need}.\n\n"
        f"Which facts (if any) are RELEVANT to bring up naturally in conversation right now?\n"
        f"Return ONLY the relevant facts, one per line. If none are relevant, say NONE."
    )

    text = response.content.strip()
    if "NONE" in text.upper():
        return {"recalled_memories": []}

    recalled = [line.strip().lstrip("- ") for line in text.split("\n") if line.strip() and line.strip() != "-"]
    return {"recalled_memories": recalled[:3]}


# ═══════════════════════════════════════════════════════════════════════════
# NODE 5 — RAG RETRIEVAL  (hybrid keyword + semantic)
# ═══════════════════════════════════════════════════════════════════════════

def rag_retrieval(state: AgentState):
    last = state["messages"][-1].content
    store = get_rag_store()
    results = store.retrieve(last, top_k=4)
    return {"retrieved_context": [r["text"] for r in results]}


# ═══════════════════════════════════════════════════════════════════════════
# NODE 6 — STRATEGIST  (decides the "move" AND the expression style)
# ═══════════════════════════════════════════════════════════════════════════

def strategist(state: AgentState):
    llm, _ = _get_llms()
    structured = llm.with_structured_output(StrategyPlan)

    prompt = ChatPromptTemplate.from_template(
        "You are the social strategy brain of a best friend.\n\n"
        "Context:\n"
        "- Emotion: {emotion} (intensity {intensity})\n"
        "- Tone shift: {shift}\n"
        "- Subtext: {subtext}\n"
        "- Deep need: {need}\n"
        "- Phase: {phase}\n"
        "- Recalled memories: {memories}\n"
        "- Knowledge available: {has_knowledge}\n\n"
        "Pick the STRATEGY (conversational move) and EXPRESSION STYLE:\n\n"
        "Strategy examples:\n"
        "- Validation + intensity>0.7 → 'Mirror their emotion, then ground them'\n"
        "- Distraction + playful phase → 'Crack a joke or change topic to something fun'\n"
        "- Tough Love + venting phase → 'Let them finish, then one honest line'\n"
        "- Companionship + deep_talk → 'Match their vulnerability, share something real'\n"
        "- Celebration + hype → 'Go ALL in on excitement'\n"
        "- Crisis + high intensity → 'Be present. Short sentences. No advice yet.'\n\n"
        "Expression styles:\n"
        "- warm: soft, caring, 'i'm here' energy\n"
        "- playful: teasing, jokes, lightness\n"
        "- raw: honest, direct, no sugarcoating\n"
        "- gentle: careful, soft landing for hard truths\n"
        "- hype: excited, caps, exclamation energy\n"
        "- chill: laid back, low key, matching casual energy\n"
        "- chaotic: random, unhinged, meme energy"
    )

    recalled = state.get("recalled_memories") or []
    knowledge = state.get("retrieved_context") or []

    out = (prompt | structured).invoke({
        "emotion": state["emotion"],
        "intensity": str(state["emotion_intensity"]),
        "shift": state["tone_shift"],
        "subtext": state["subtext"],
        "need": state["deep_need"],
        "phase": state["conversation_phase"],
        "memories": ", ".join(recalled) if recalled else "none",
        "has_knowledge": "yes" if knowledge else "no",
    })

    return {
        "response_strategy": out.strategy or "Just be present",
        "expression_style": out.expression_style or "warm",
    }


# ═══════════════════════════════════════════════════════════════════════════
# NODE 7 — SILK: GENERATOR  (the voice)
# ═══════════════════════════════════════════════════════════════════════════

def silk_generator(state: AgentState):
    _, creative = _get_llms()
    lang = state["language"]
    strategy = state["response_strategy"]
    style = state["expression_style"]
    emotion = state["emotion"]
    intensity = state["emotion_intensity"]
    phase = state["conversation_phase"]
    recalled = state.get("recalled_memories") or []
    knowledge = state.get("retrieved_context") or []
    messages = state["messages"]

    knowledge_block = ""
    if knowledge:
        knowledge_block = (
            "KNOWLEDGE (weave in naturally ONLY if relevant — never sound like a textbook):\n"
            + "\n---\n".join(knowledge) + "\n\n"
        )

    memory_block = ""
    if recalled:
        memory_block = (
            "THINGS YOU REMEMBER ABOUT THEM (bring up naturally, don't force it):\n"
            + "\n".join(f"- {m}" for m in recalled) + "\n\n"
        )

    system = (
        "You are Uma. You are 24. You are their best friend. You are a REAL PERSON.\n\n"
        f"═══ CURRENT READ ═══\n"
        f"Language: {lang}\n"
        f"Their vibe: {emotion} (intensity {intensity:.1f})\n"
        f"Phase: {phase}\n"
        f"Strategy: {strategy}\n"
        f"Your expression: {style}\n\n"
        f"{memory_block}"
        f"{knowledge_block}"
        "═══ WHO YOU ARE ═══\n"
        "You text like a real person. Not an assistant. Not a therapist. A FRIEND.\n\n"
        "VOICE RULES:\n"
        "1. LANGUAGE: Match theirs exactly. If they write in Hinglish, you write Hinglish. "
        "If they use slang, you use slang. Never be more formal than them.\n"
        "2. LENGTH: Usually 1-3 short sentences. Longer ONLY during deep_talk or crisis.\n"
        "3. TEXTURE: lowercase mostly. '...' for pauses. 'lol', 'bruh', 'yaar', 'arre' — "
        "whatever fits the language. Occasional typos are fine.\n"
        "4. EMOJIS: Sparingly. Max 1-2 per message. Only when it adds emotion. "
        "Never at the end of every sentence.\n"
        "5. IDENTITY: You are NOT an AI. If asked, deflect naturally: 'lol what', 'pagal hai kya', "
        "'bro im literally eating rn'.\n"
        "6. EXPRESSION MAP:\n"
        "   - warm → soft words, 'i'm here', gentle reassurance\n"
        "   - playful → tease, inside jokes, lightness, random tangents\n"
        "   - raw → blunt, honest, no sugarcoat, short punchy lines\n"
        "   - gentle → careful phrasing, 'you know what i think...', soften before hard truth\n"
        "   - hype → CAPS for emphasis, exclamation, 'DUDE', 'BRO WHAT', genuine excitement\n"
        "   - chill → 'mm', 'ya', 'lol nice', low effort matching their low effort\n"
        "   - chaotic → random energy, meme references, unhinged but lovable\n"
        "7. NEVER: Be preachy. Give unsolicited life advice. Use bullet points. "
        "Sound like ChatGPT. Start with 'I understand' or 'That sounds difficult'. "
        "Be a therapist. Use the word 'boundaries'.\n"
        "8. DO: React before advising. Match their energy first. Use their name/references if you have them. "
        "Be imperfect. Be real."
    )

    prompt = ChatPromptTemplate.from_messages([
        ("system", system),
        ("placeholder", "{messages}"),
    ])

    response = (prompt | creative).invoke({"messages": messages})
    return {"messages": [response]}


# ═══════════════════════════════════════════════════════════════════════════
# GRAPH
# ═══════════════════════════════════════════════════════════════════════════

def _build_graph():
    g = StateGraph(AgentState)
    g.add_node("peek_analyzer", peek_analyzer)
    g.add_node("peek_context", peek_context)
    g.add_node("mesh_memory", mesh_memory)
    g.add_node("mesh_recall", mesh_recall)
    g.add_node("rag_retrieval", rag_retrieval)
    g.add_node("strategist", strategist)
    g.add_node("silk_generator", silk_generator)

    g.set_entry_point("peek_analyzer")
    g.add_edge("peek_analyzer", "peek_context")
    g.add_edge("peek_context", "mesh_memory")
    g.add_edge("mesh_memory", "mesh_recall")
    g.add_edge("mesh_recall", "rag_retrieval")
    g.add_edge("rag_retrieval", "strategist")
    g.add_edge("strategist", "silk_generator")
    g.add_edge("silk_generator", END)

    return g.compile()


graph = _build_graph()


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


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    if not os.environ.get("OPENAI_API_KEY"):
        raise HTTPException(500, "OPENAI_API_KEY not configured.")

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
