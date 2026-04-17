# Azure AI Foundry Migration Plan

**Goal:** Migrate the humanlikeAI app from OpenAI/Pinecone to Azure AI Foundry without affecting current production.
**Strategy:** Feature-flag (`AI_PROVIDER` env var) — toggle between providers without redeployment. Instant rollback.

---

## Current Stack → Azure Replacements

| Component | Current | Azure Target |
|---|---|---|
| LLM (main pipeline) | OpenAI `gpt-4o-mini` via LangChain `ChatOpenAI` | `AzureChatOpenAI` with Foundry deployment |
| LLM (chat/recs) | OpenAI `gpt-4` via direct `openai.OpenAI()` client | `openai.AzureOpenAI()` client |
| Embeddings | `text-embedding-3-large` via `OpenAIEmbeddings` | `AzureOpenAIEmbeddings` |
| Vector Store | Pinecone (`langchain-pinecone`) | Azure AI Search (`langchain-community.AzureSearch`) |
| STT | OpenAI Whisper (`whisper-1`) | Azure Speech-to-Text (optional) |
| TTS | ElevenLabs `eleven_flash_v2_5` | Azure Cognitive Services TTS (optional) |

---

## Phase 1 — Azure Foundry Portal Setup (No Code Changes)

1. Create an **Azure AI Foundry project** in the Azure portal
2. Deploy these models inside your Foundry project:
   - `gpt-4o-mini` → deployment name: `gpt-4o-mini`
   - `gpt-4` → deployment name: `gpt-4`
   - `text-embedding-3-large` → deployment name: `text-embedding-3-large`
3. Note down:
   - Endpoint URL: `https://<your-resource>.openai.azure.com/`
   - API Key
   - API Version (use `2024-02-01` or latest stable)
   - Deployment names for each model
4. Create an **Azure AI Search** resource
   - Note: Search endpoint + Admin key
5. *(Optional)* Create an **Azure Speech** resource if replacing ElevenLabs/Whisper

---

## Phase 2 — Environment Isolation (Zero Production Risk)

Add the following to `.env` **without removing existing OpenAI keys**:

```env
# Azure AI Foundry
AZURE_OPENAI_API_KEY=
AZURE_OPENAI_ENDPOINT=https://<your-resource>.openai.azure.com/
AZURE_OPENAI_API_VERSION=2024-02-01

# Azure deployment names (must match what you named them in Foundry)
AZURE_DEPLOY_GPT4O_MINI=gpt-4o-mini
AZURE_DEPLOY_GPT4=gpt-4
AZURE_DEPLOY_EMBEDDINGS=text-embedding-3-large

# Azure AI Search (Pinecone replacement)
AZURE_SEARCH_ENDPOINT=https://<your-search>.search.windows.net
AZURE_SEARCH_KEY=
AZURE_SEARCH_INDEX_NAME=diltak

# Feature flag — set to "azure" to switch, "openai" keeps existing behavior
AI_PROVIDER=openai

# Azure Speech (optional)
AZURE_SPEECH_KEY=
AZURE_SPEECH_REGION=eastus
```

---

## Phase 3 — Code Changes (Feature-Flagged)

6 files need changes. All changes are additive — existing code paths remain intact.

### 3a. `main.py` — LangGraph pipeline LLMs (lines 137–142)

```python
# BEFORE
def _get_llms():
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.2)
    creative_llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.85)
    return llm, creative_llm

# AFTER
import os
from langchain_openai import AzureChatOpenAI

def _get_llms():
    if os.getenv("AI_PROVIDER") == "azure":
        llm = AzureChatOpenAI(
            azure_deployment=os.getenv("AZURE_DEPLOY_GPT4O_MINI"),
            azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
            api_key=os.getenv("AZURE_OPENAI_API_KEY"),
            api_version=os.getenv("AZURE_OPENAI_API_VERSION"),
            temperature=0.2,
        )
        creative_llm = AzureChatOpenAI(
            azure_deployment=os.getenv("AZURE_DEPLOY_GPT4O_MINI"),
            azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
            api_key=os.getenv("AZURE_OPENAI_API_KEY"),
            api_version=os.getenv("AZURE_OPENAI_API_VERSION"),
            temperature=0.85,
        )
    else:
        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.2)
        creative_llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.85)
    return llm, creative_llm
```

### 3b. `rag.py` — Embeddings + Vector Store (lines 69–90)

```python
# AFTER
from langchain_openai import AzureOpenAIEmbeddings
from langchain_community.vectorstores import AzureSearch

def _get_embeddings():
    if os.getenv("AI_PROVIDER") == "azure":
        return AzureOpenAIEmbeddings(
            azure_deployment=os.getenv("AZURE_DEPLOY_EMBEDDINGS"),
            azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
            api_key=os.getenv("AZURE_OPENAI_API_KEY"),
            api_version=os.getenv("AZURE_OPENAI_API_VERSION"),
        )
    return OpenAIEmbeddings(model="text-embedding-3-large", dimensions=1024)

def _get_vectorstore(embeddings):
    if os.getenv("AI_PROVIDER") == "azure":
        return AzureSearch(
            azure_search_endpoint=os.getenv("AZURE_SEARCH_ENDPOINT"),
            azure_search_key=os.getenv("AZURE_SEARCH_KEY"),
            index_name=os.getenv("AZURE_SEARCH_INDEX_NAME"),
            embedding_function=embeddings.embed_query,
        )
    return PineconeVectorStore(...)  # existing code unchanged
```

### 3c. `report_agent.py` — Report LLM (line 41)

Same `AI_PROVIDER` check, swap `ChatOpenAI` → `AzureChatOpenAI` with `AZURE_DEPLOY_GPT4O_MINI`.

### 3d. `routers/chat_wrapper.py` — Direct OpenAI client (lines 76–80, 269–277)

```python
# AFTER
from openai import AzureOpenAI

def _get_client():
    if os.getenv("AI_PROVIDER") == "azure":
        return AzureOpenAI(
            api_key=os.getenv("AZURE_OPENAI_API_KEY"),
            azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
            api_version=os.getenv("AZURE_OPENAI_API_VERSION"),
        )
    return OpenAI()

model = os.getenv("AZURE_DEPLOY_GPT4") if os.getenv("AI_PROVIDER") == "azure" else "gpt-4"
```

### 3e. `routers/recommendations.py` — Same pattern as chat_wrapper (lines 47–51, 298–306)

Identical change — swap client init + model name via flag.

### 3f. `routers/voice_calls.py` — Whisper STT (line 146)

Gate with a separate flag: `SPEECH_PROVIDER=azure|openai`. Azure Speech SDK uses a different library (`azure-cognitiveservices-speech`).

---

## Phase 4 — Dependency Updates

Add to `requirements.txt` (keep all existing packages):

```
langchain-community>=0.2.0
azure-search-documents>=11.4.0
azure-identity>=1.15.0
# azure-cognitiveservices-speech>=1.35.0  # only if replacing voice
```

`langchain-openai` already ships `AzureChatOpenAI` and `AzureOpenAIEmbeddings` — no new package needed for LLM/embeddings.

---

## Phase 5 — Testing & Cutover

1. **Local test** — set `AI_PROVIDER=azure` in local `.env`, run `uvicorn main:app`, hit each endpoint
2. **Staging** — deploy to staging with `AI_PROVIDER=azure`; production stays `AI_PROVIDER=openai`
3. **RAG migration** — run a one-time script to re-embed all Pinecone documents into Azure AI Search
4. **Smoke test** these endpoints:
   - `POST /chat`
   - `POST /api/reports/generate`
   - `POST /api/recommendations/generate`
   - `POST /api/chat_wrapper/ai-chat`
   - `POST /api/voice_calls/transcribe`
5. **Flip the flag** — set `AI_PROVIDER=azure` in production `.env` and restart

---

## Rollback

Set `AI_PROVIDER=openai` → restart → instantly back to current behavior. No code rollback needed.

---

## Files to Change Summary

| File | Change | Risk |
|---|---|---|
| `.env` | Add Azure vars + flag | None |
| `main.py` | Feature-flag `_get_llms()` | Low |
| `rag.py` | Feature-flag embeddings + vectorstore | Medium (needs RAG re-index) |
| `report_agent.py` | Feature-flag `_get_llm()` | Low |
| `routers/chat_wrapper.py` | Feature-flag client init + model name | Low |
| `routers/recommendations.py` | Feature-flag client init + model name | Low |
| `routers/voice_calls.py` | Feature-flag STT (optional) | Low |
| `requirements.txt` | Add 2–3 Azure packages | Low |
