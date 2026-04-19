# Physical Health Feature — Data Flow & Architecture

> Internal reference: how data moves through every part of the physical health system.
> Covers all 5 workflows, storage locations, and key design decisions.

---

## 1. Daily Check-In

```
User submits form
      │
      ▼
POST /api/physical-health/check-in
      │
      ├── Firebase Auth token → get uid + company_id from Firestore users doc
      │
      ├── Write to Firestore: physical_health_checkins/{checkin_id}
      │     { user_id, company_id, energy_level, sleep_quality,
      │       sleep_hours, exercise_done, exercise_minutes,
      │       exercise_type, nutrition_quality, pain_level,
      │       hydration, notes, created_at }
      │
      ├── Rule-based nudge computed inline (no LLM)
      │     sleep_hours < 6  → sleep tip
      │     pain_level < 4   → stretch reminder
      │     energy < 4 + no exercise → walk suggestion
      │
      └── Returns { success, checkin_id, nudge }
```

---

## 2. Medical Document Upload

The most complex flow — upload is non-blocking, heavy AI work runs in background.

```
User uploads PDF/DOCX
      │
      ▼
POST /api/physical-health/medical/upload
      │
      ├── Validate: extension (.pdf/.docx), size (≤10 MB)
      ├── Read file bytes into memory
      ├── Generate doc_id = uuid
      │
      ├── Firebase Storage
      │     Upload to: medical_reports/{uid}/{doc_id}/{filename}
      │
      ├── Firestore: medical_documents/{doc_id}
      │     { user_id, company_id, status="uploaded",
      │       storage_path, filename, report_type, ... }
      │
      ├── asyncio.create_task(process_medical_document(...))
      │     ← fires in background, endpoint returns immediately
      │
      └── Returns { success, doc_id, status="processing" }


  BACKGROUND TASK: process_medical_document()
  (physical_health_agent.py)
        │
        ├── Step 1: Firestore status → "processing"
        │
        ├── Step 2: Extract text from file bytes
        │     utils/pdf_parser.py
        │       pdfplumber → (fallback) pypdf → (fallback) PyPDF2
        │
        ├── Step 3: LLM analysis
        │     analyze_medical_document(raw_text)
        │       → ANALYZE_MEDICAL_REPORT prompt
        │       → ChatOpenAI.with_structured_output(MedicalReportAnalysis)
        │       → Returns: summary, key_findings, flagged_values,
        │                  urgency_level, recommendations, report_type
        │
        ├── Step 4: RAG ingestion
        │     rag.get_rag_store().add_documents(
        │       texts=[raw_text],
        │       metadata={ type:"medical_report", user_id, doc_id, report_type }
        │       chunk_size=400, chunk_overlap=80
        │     )
        │     → Pinecone stores N chunks, returns chunk_ids[]
        │
        ├── Step 5: Fetch 7-day check-in averages
        │     _build_checkin_context(user_id, db)
        │     → Queries physical_health_checkins last 7 days
        │     → Returns text summary of averages
        │
        ├── Step 6: Personalised suggestions
        │     generate_health_suggestions(analysis, checkin_context)
        │       → GENERATE_HEALTH_SUGGESTIONS prompt
        │       → Returns list of 4–6 lifestyle tips
        │
        ├── Step 7: Firestore update
        │     medical_documents/{doc_id} → status="analyzed"
        │     + summary, key_findings, flagged_values,
        │       recommendations, rag_chunk_ids, urgency_level
        │
        └── Step 8 (if urgency="emergency"):
              Write to wellness_events collection
              { user_id, event_type="medical_emergency_flag",
                message="...seek immediate attention..." }
              ← user-only alert, never employer-visible
```

**Frontend polling:** `GET /medical/{doc_id}/status` every ~4 seconds until `status = "analyzed" | "failed"`.

---

## 3. Ask My Documents (RAG Q&A)

```
User types a question
      │
      ▼
POST /api/physical-health/ask
      │
      ├── Pinecone retrieval
      │     filter: { $and: [ user_id=$eq uid, type=$eq "medical_report" ] }
      │     top_k=4, relevance threshold=0.4
      │     → Returns matching chunks from this user's docs only
      │
      ├── If no chunks → return "no documents found" (no LLM call)
      │
      ├── LLM answer generation
      │     ANSWER_HEALTH_QUESTION prompt
      │       context_chunks = joined retrieved text
      │       question = user's question
      │     → ChatOpenAI(temperature=0.1)
      │     → Grounded answer + source doc_ids + confidence score
      │
      └── Returns { answer, source_doc_ids, confidence, disclaimer }
```

---

## 4. Health Score & Trends

```
GET /score
      │
      ├── Query physical_health_checkins (last 30 days, user_id filter)
      ├── Weighted composite score:
      │     energy(25%) + sleep_quality(20%) + sleep_hours_normalised(15%)
      │     + nutrition(20%) + pain(10%) + hydration(10%)
      │     sleep_hours_normalised = min(sleep_hours / 8.0, 1.0) * 10
      ├── Streak: count consecutive days backward from today with ≥1 check-in
      └── Returns score (0–10), level, streak_days, highlights, concerns


GET /trends?period=7d|30d|90d
      │
      ├── Query physical_health_checkins (user_id + created_at >= cutoff)
      ├── Group by date → daily averages per metric
      ├── trend_direction per metric:
      │     compare first-half avg vs second-half avg of the period
      │     second_half > first_half + 0.5 → "improving"
      │     first_half > second_half       → "declining"
      │     else                           → "stable"
      └── Returns data_points[], averages{}, trend_direction{}
```

---

## 5. Periodic Health Report

```
POST /reports/generate
      │
      ├── Query physical_health_checkins (user_id, last N days)
      ├── Require ≥3 check-ins — else 422 error
      ├── Compute aggregates:
      │     avg_energy, avg_sleep_quality, avg_sleep_hours,
      │     avg_nutrition_quality, avg_pain_level, avg_hydration,
      │     exercise_days, avg_exercise_minutes_daily
      │
      ├── RAG retrieval (medical context)
      │     Same Pinecone filter as /ask
      │     → Injects relevant medical history into report prompt
      │     → Falls back to "No uploaded documents" if nothing found
      │
      ├── LLM report generation
      │     GENERATE_PERIODIC_REPORT prompt
      │       { period, metrics_summary, medical_context }
      │     → ChatOpenAI.with_structured_output(_PeriodicReportLLMOutput)
      │     → overall_score, trend, summary, strengths,
      │       concerns, recommendations, risk_flags, follow_up_suggested
      │
      ├── Write to Firestore: physical_health_reports/{report_id}
      │
      └── Returns full PeriodicReportResponse
```

---

## Storage Map

| Data | Storage | Collection / Path |
|---|---|---|
| Daily check-ins | Firestore | `physical_health_checkins` |
| Document metadata + AI analysis | Firestore | `medical_documents` |
| Raw PDF/DOCX files | Firebase Storage | `medical_reports/{uid}/{doc_id}/` |
| Document text chunks (Q&A) | Pinecone | index: `uma-rag`, metadata: `user_id` + `type` |
| Periodic health reports | Firestore | `physical_health_reports` |
| Emergency alerts | Firestore | `wellness_events` |

---

## File Responsibility Map

| File | Role |
|---|---|
| `routers/physical_health.py` | All 13 API endpoints, request validation, auth, Firestore reads/writes |
| `physical_health_agent.py` | All LLM calls: document analysis, suggestions, periodic report generation |
| `physical_health_prompts.py` | 4 `ChatPromptTemplate` prompts used by the agent |
| `physical_health_schemas.py` | All Pydantic request/response models |
| `utils/pdf_parser.py` | PDF and DOCX text extraction (pdfplumber → pypdf fallback chain) |
| `rag.py` | Pinecone vector store: `add_documents()`, `retrieve()`, `delete_chunk()` |
| `firebase_config.py` | Firestore `get_db()` and Firebase Storage bucket reference |

---

## Key Design Decisions

**No LLM on check-in**
Nudges are rule-based. Keeps the check-in endpoint fast (<100ms) — no waiting on OpenAI.

**Upload is non-blocking**
`asyncio.create_task()` kicks off the background pipeline after the HTTP response is already sent. The user gets `status="processing"` immediately and polls for completion.

**Pinecone always user-scoped**
Every query filters by `user_id` using Pinecone's explicit `$and`/`$eq` operators. One user can never retrieve another user's medical chunks.

```python
metadata_filter={
    "$and": [
        {"user_id": {"$eq": uid}},
        {"type": {"$eq": "medical_report"}},
    ]
}
```

**Employer blind spot**
`medical_documents` is never queried by `company_id`. Employers only see aggregated, anonymised check-in metrics. The `wellness_events` emergency alert is user-visible only.

**RAG enriches periodic reports**
When generating a periodic report, the system retrieves relevant chunks from the user's uploaded medical documents and injects them as context alongside the check-in aggregates. This means a periodic report can reference lab results, flagged values, and prescriptions without the user having to re-upload anything.
