# Uma — High EQ Companion API



## Architecture

```
User message
    │
    ▼
┌─────────────────┐
│  PEEK: Analyzer  │  language, emotion, intensity, tone shift
└────────┬────────┘
         ▼
┌─────────────────┐
│  PEEK: Context   │  subtext, deep need, conversation phase
└────────┬────────┘  ("i'm fine" ≠ "i'm fine" — context is everything)
         ▼
┌─────────────────┐
│  MESH: Memory    │  extract + categorise new facts
└────────┬────────┘  (identity, preference, relationship, life_event...)
         ▼
┌─────────────────┐
│  MESH: Recall    │  surface relevant past memories
└────────┬────────┘  ("didn't you say you love that place?")
         ▼
┌─────────────────┐
│  RAG: Retrieval  │  hybrid keyword + semantic search
└────────┬────────┘  (persona knowledge, EQ patterns, cultural context)
         ▼
┌─────────────────┐
│  Strategist      │  pick the move + expression style
└────────┬────────┘  (warm / playful / raw / gentle / hype / chill / chaotic)
         ▼
┌─────────────────┐
│  SILK: Generator │  produce Uma's reply in the right voice
└────────┬────────┘
         ▼
      Response
```

### Three pillars

| Pillar | Codename | What it does |
|--------|----------|------|
| **Conversation** | Peek | Reads vibes, not just words. Detects when "I'm fine" means "I'm not fine". Tracks emotional trajectory across turns. |
| **Memory** | Mesh | Knows what to remember (your pet's name) and what to forget (you vented about Monday). Proactively recalls relevant memories. |
| **Expression** | Silk | Speaks in your language with the right tone. Warm when you're hurting, chaotic when you're vibing, raw when you need honesty. |

### RAG engine

- **Hybrid retrieval**: BM25-style keyword scoring + OpenAI semantic embeddings, fused with configurable weights.
- **Relevance gate**: Chunks below threshold are dropped (no garbage context).
- **Smart chunking**: Long documents are split with sentence-aware overlap on ingest.
- **Metadata filtering**: Filter retrieval by tags (`source`, `topic`, etc.).
- **Storage**: `data/documents.json` — human-readable, git-friendly.

## Setup

```bash
pip install -r requirements.txt
```

```powershell
$env:OPENAI_API_KEY = "sk-..."
```

## Run

```bash
uvicorn main:app --reload
```

Docs: **http://127.0.0.1:8000/docs**

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/chat` | Send message → Uma's reply + full analysis |
| `GET` | `/sessions/{id}` | Session metadata + memories |
| `DELETE` | `/sessions/{id}` | Delete session |
| `GET` | `/health` | Status + RAG chunk count |
| `POST` | `/rag/documents` | Add documents (auto-chunked) |
| `GET` | `/rag/documents` | List all RAG chunks |
| `DELETE` | `/rag/documents/{id}` | Delete a chunk |

### `POST /chat` example

```json
{
  "message": "yaar bahut bura lag raha hai aaj",
  "session_id": null
}
```

Response includes the full pipeline trace:

```json
{
  "session_id": "...",
  "reply": "kya hua bata na... 😔",
  "peek": {
    "language": "Hinglish",
    "emotion": "Sad",
    "emotion_intensity": 0.7,
    "tone_shift": "stable",
    "subtext": "Seeking comfort, probably had a bad day",
    "deep_need": "Validation",
    "conversation_phase": "venting"
  },
  "mesh": {
    "new_memories": [],
    "recalled_memories": []
  },
  "strategy": "Mirror their emotion, sit with it, don't rush to fix",
  "expression_style": "warm",
  "retrieved_context": ["When someone is sad, Uma doesn't immediately try to fix it..."],
  "total_memories": 0
}
```

### `POST /rag/documents` example

```json
{
  "texts": ["Long article or fact to teach Uma..."],
  "metadata": [{"source": "custom", "topic": "cooking"}],
  "auto_chunk": true
}
```

Long texts are automatically split into overlapping chunks for better retrieval.
