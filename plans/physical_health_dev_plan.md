# Physical Health Feature — Implementation Plan

> Consolidated from `physical_health_feature.md` + live codebase analysis.
> Every file path, line number, and schema is verified against the actual project.

---

## Codebase Reality Check

### What already exists (reuse, don't rebuild)

| What | File | Lines | Notes |
|---|---|---|---|
| `PhysicalHealthLLMOutput` schema | `report_schemas.py` | 68–82 | 5 metrics: activity, nutrition, pain, lifestyle, absenteeism |
| `PhysicalHealthBlock` response wrapper | `report_schemas.py` | 125–131 | score, level, confidence, trend, summary, metrics |
| `_weighted_avg`, `_metric_dict`, `score_to_level` helpers | `report_schemas.py` | 13–19, 156–169 | Reuse directly |
| `ANALYZE_PHYSICAL_HEALTH` LLM prompt | `report_prompts.py` | 91–140 | Already scores 5 dimensions |
| `analyze_physical_health()` pipeline node | `report_agent.py` | 79–85 | Conversation-only; extend with check-in data |
| RAG store — `add_documents()` + `retrieve()` | `rag.py` | 46–233 | Just pass `type=medical_report` metadata |
| DOCX text extractor | `docx_ingest.py` | full | Reuse for .docx medical reports |
| File upload pattern (UploadFile + async read) | `routers/voice_calls.py` | 139–167 | Copy pattern exactly |
| Firebase Storage bucket | `firebase_config.py` | 14 | `mindtest-94298.firebasestorage.app` |
| `get_current_user` auth dependency | `routers/auth.py` | 26–48 | Use on all new endpoints |
| `get_employer_user` for employer-only routes | `routers/auth.py` | 51–82 | Use for employer dashboard metric |
| Router registration pattern | `main.py` | 422–438 | Add one line here |
| `python-docx` already installed | `requirements.txt` | — | No new dep for .docx |

### What does NOT exist (must build)

- No physical health check-in endpoint
- No PDF parser (`pdfplumber` not in requirements)
- No Firebase Storage upload in any router
- No `medical_documents` Firestore collection or logic
- No `physical_health_checkins` collection
- No `physical_health_reports` collection
- No trends/score computation
- No medical Q&A endpoint
- No physical health metrics in recommendations

---

## New Firestore Collections

### `physical_health_checkins`
```
{
    user_id: str,
    company_id: str,
    created_at: Timestamp,
    energy_level: int,        # 1–10
    sleep_quality: int,       # 1–10
    sleep_hours: float,
    exercise_done: bool,
    exercise_minutes: int,
    exercise_type: str,       # walk | gym | yoga | sport | other | none
    nutrition_quality: int,   # 1–10
    pain_level: int,          # 1–10, inverted (10 = no pain)
    hydration: int,           # 1–10
    notes: Optional[str]
}
```

### `medical_documents`
```
{
    user_id: str,
    company_id: str,
    uploaded_at: Timestamp,
    filename: str,
    file_type: str,           # pdf | docx | image
    file_size_bytes: int,
    storage_path: str,        # Firebase Storage path
    report_type: str,         # lab_work | blood_test | xray_mri | prescription | general_checkup | other
    report_date: Optional[str],
    issuing_facility: Optional[str],
    status: str,              # uploaded | processing | analyzed | failed
    raw_text: Optional[str],
    rag_chunk_ids: List[str],
    summary: Optional[str],
    key_findings: Optional[List[str]],
    flagged_values: Optional[List[dict]],
    recommendations: Optional[List[str]],
    follow_up_needed: Optional[bool],
    urgency_level: str,       # routine | follow_up | urgent | emergency
    analyzed_at: Optional[Timestamp]
}
```

### `physical_health_reports`
```
{
    user_id: str,
    company_id: str,
    generated_at: Timestamp,
    period_start: Timestamp,
    period_end: Timestamp,
    report_type: str,         # weekly | monthly | on_demand
    avg_energy: float,
    avg_sleep_quality: float,
    avg_sleep_hours: float,
    avg_exercise_minutes_per_day: float,
    avg_nutrition_quality: float,
    avg_pain_level: float,
    avg_hydration: float,
    exercise_days_count: int,
    overall_score: float,
    overall_level: str,
    trend: str,
    summary: str,
    strengths: List[str],
    concerns: List[str],
    recommendations: List[str],
    medical_doc_ids: List[str],
    risk_flags: List[str],
    follow_up_suggested: bool
}
```

---

## New Files to Create

| File | Purpose |
|---|---|
| `physical_health_schemas.py` | All Pydantic models |
| `physical_health_prompts.py` | LLM prompts (medical analysis, report gen, Q&A) |
| `physical_health_agent.py` | LLM pipeline functions |
| `utils/pdf_parser.py` | PDF → text extraction |
| `routers/physical_health.py` | All API endpoints |

## Files to Modify

| File | What changes |
|---|---|
| `main.py` | Add 2 lines: import + `include_router` |
| `requirements.txt` | Add `pdfplumber`, `pypdf2` |
| `routers/recommendations.py` | Add optional physical health fields to request + new categories |
| `routers/employer_dashboard.py` | Add `team_physical_health_index` metric |

---

## Build Steps (in order)

---

### Step 1 — `physical_health_schemas.py`

**Reuse from codebase:** `MetricOutput`, `score_to_level` from `report_schemas.py`

```python
# Models to create:

class PhysicalCheckInRequest(BaseModel):
    energy_level: int        # 1–10
    sleep_quality: int       # 1–10
    sleep_hours: float       # 0–24
    exercise_done: bool
    exercise_minutes: int    # default 0
    exercise_type: str       # default "none"
    nutrition_quality: int   # 1–10
    pain_level: int          # 1–10
    hydration: int           # 1–10
    notes: Optional[str]

class PhysicalCheckInResponse(BaseModel):
    success: bool
    checkin_id: str
    nudge: Optional[str]     # short personalised tip

class FlaggedValue(BaseModel):
    name: str
    value: str
    normal_range: str
    status: str              # high | low | normal | borderline
    plain_explanation: str

class MedicalReportAnalysis(BaseModel):
    report_type: str
    report_date: Optional[str]
    summary: str
    key_findings: List[str]
    flagged_values: List[FlaggedValue]
    follow_up_needed: bool
    urgency_level: str       # routine | follow_up | urgent | emergency
    recommendations: List[str]
    confidence: float

class MedicalDocumentUploadResponse(BaseModel):
    success: bool
    doc_id: str
    status: str
    message: str

class MedicalDocumentStatusResponse(BaseModel):
    doc_id: str
    status: str
    analyzed_at: Optional[str]
    urgency_level: Optional[str]

class MedicalDocumentDetail(BaseModel):
    doc_id: str
    filename: str
    report_type: str
    report_date: Optional[str]
    issuing_facility: Optional[str]
    status: str
    uploaded_at: str
    analyzed_at: Optional[str]
    summary: Optional[str]
    key_findings: Optional[List[str]]
    flagged_values: Optional[List[FlaggedValue]]
    recommendations: Optional[List[str]]
    follow_up_needed: Optional[bool]
    urgency_level: str

class TrendPoint(BaseModel):
    date: str
    energy_level: Optional[float]
    sleep_quality: Optional[float]
    sleep_hours: Optional[float]
    exercise_minutes: Optional[int]
    nutrition_quality: Optional[float]
    pain_level: Optional[float]
    hydration: Optional[float]

class HealthTrendsResponse(BaseModel):
    period: str
    data_points: List[TrendPoint]
    averages: dict
    trend_direction: dict
    total_checkins: int

class PhysicalHealthScoreResponse(BaseModel):
    score: float
    level: str
    last_checkin_date: Optional[str]
    days_since_checkin: Optional[int]
    streak_days: int
    highlights: List[str]
    concerns: List[str]

class PeriodicReportRequest(BaseModel):
    report_type: str = "on_demand"   # weekly | monthly | on_demand
    days: int = 30                    # lookback window

class PeriodicReportResponse(BaseModel):
    report_id: str
    period_start: str
    period_end: str
    report_type: str
    overall_score: float
    overall_level: str
    trend: str
    avg_energy: float
    avg_sleep_quality: float
    avg_sleep_hours: float
    avg_exercise_minutes_daily: float
    avg_nutrition_quality: float
    avg_pain_level: float
    exercise_days: int
    summary: str
    strengths: List[str]
    concerns: List[str]
    recommendations: List[str]
    follow_up_suggested: bool
    generated_at: str

class AskRequest(BaseModel):
    question: str

class AskResponse(BaseModel):
    answer: str
    source_doc_ids: List[str]
    confidence: float
    disclaimer: str
```

---

### Step 2 — `physical_health_prompts.py`

Three prompts:

**ANALYZE_MEDICAL_REPORT** — input: `{report_text}`
- Instructs LLM to extract findings in plain language
- List abnormal values with normal ranges and explanations
- Classify urgency: routine / follow_up / urgent / emergency
- Do NOT diagnose — only summarise findings
- Output: `MedicalReportAnalysis` (structured output)

**GENERATE_HEALTH_SUGGESTIONS** — input: `{findings_summary}`, `{checkin_context}`
- Given medical findings + recent check-in averages
- Generate 3–5 personalised lifestyle suggestions
- Keep suggestions actionable: diet, exercise, sleep, hydration
- Output: plain list of strings

**GENERATE_PERIODIC_REPORT** — input: `{metrics_summary}`, `{medical_context}`
- Aggregated check-in averages over a period
- Optional medical context from RAG retrieval
- Output structured: overall_score, trend, summary, strengths, concerns, recommendations, risk_flags

**ANSWER_HEALTH_QUESTION** — input: `{question}`, `{context_chunks}`
- RAG-grounded Q&A about user's own medical history
- Only answer from provided context, never hallucinate
- Always append: "This is not medical advice. Consult a healthcare professional."

---

### Step 3 — `utils/pdf_parser.py`

```python
def extract_text_from_pdf(file_bytes: bytes) -> str:
    # Try pdfplumber first (best for text-layer PDFs)
    # Fallback to pypdf2
    # Return extracted text or raise ValueError if both fail
```

```python
def extract_text_from_docx(file_bytes: bytes) -> str:
    # Use python-docx (already in requirements)
    # Extract paragraphs + table cells (same as docx_ingest.py pattern)
```

```python
def extract_text(file_bytes: bytes, filename: str) -> str:
    # Route by file extension: .pdf → pdf, .docx → docx, else raise
```

---

### Step 4 — `physical_health_agent.py`

```python
def analyze_medical_document(raw_text: str) -> MedicalReportAnalysis:
    """Node 1: LLM extracts structured findings from medical report text."""
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.1)
    structured = llm.with_structured_output(MedicalReportAnalysis)
    return (ANALYZE_MEDICAL_REPORT | structured).invoke({"report_text": raw_text})

def generate_health_suggestions(analysis: MedicalReportAnalysis, checkin_context: str) -> List[str]:
    """Node 2: LLM generates personalised lifestyle suggestions."""
    # Simple invoke, returns list of suggestion strings

def generate_periodic_report(metrics: dict, medical_context: str, user_id: str) -> PeriodicReportResponse:
    """Full periodic report: aggregate check-ins + RAG context → LLM synthesis."""

async def process_medical_document(doc_id: str, user_id: str, company_id: str, raw_text: str, report_type: str):
    """
    Background task called after file upload:
    1. analyze_medical_document(raw_text) → MedicalReportAnalysis
    2. Chunk raw_text → ingest into RAG with metadata {type, user_id, doc_id, report_type}
    3. generate_health_suggestions(analysis, recent_checkins)
    4. Update medical_documents Firestore doc with analysis results + status="analyzed"
    5. If urgency_level == "emergency": log wellness_event for urgent alert
    """
```

**RAG ingestion inside `process_medical_document`:**
```python
store = get_rag_store()
chunk_ids = store.add_documents(
    texts=[raw_text],
    metadata_per_doc=[{
        "type": "medical_report",
        "user_id": user_id,
        "doc_id": doc_id,
        "report_type": report_type,
    }],
    chunk_size=400,
    chunk_overlap=80,
)
```

---

### Step 5 — `routers/physical_health.py`

Router: `prefix="/api/physical-health"`, `tags=["Physical Health"]`

Auth: All endpoints use `get_current_user` → fetch user profile (uid + company_id).
No employer guard needed — these are user-facing.

#### Complete endpoint list:

```
POST   /check-in                        → submit daily check-in
GET    /check-ins                       → paginated history (page, limit, days)
GET    /score                           → current composite physical health score
GET    /trends                          → time-series data (period: 7d | 30d | 90d)

POST   /medical/upload                  → upload medical report file
GET    /medical                         → list all user's medical docs
GET    /medical/{doc_id}                → full analyzed document detail
GET    /medical/{doc_id}/status         → poll processing status
DELETE /medical/{doc_id}                → delete doc + Storage file + RAG chunks

POST   /reports/generate                → trigger on-demand periodic report
GET    /reports                         → list all generated health reports
GET    /reports/{report_id}             → full single report

POST   /ask                             → RAG-powered Q&A on own medical history
```

#### Key implementation notes per endpoint:

**POST /check-in**
1. Get uid + company_id from token → fetch user profile from Firestore
2. Write to `physical_health_checkins` with SERVER_TIMESTAMP
3. Compute nudge: compare today's values vs 7-day averages (no LLM, rule-based for speed)
   - sleep_hours < 6 → "Try to get at least 7 hours tonight."
   - pain_level < 4 → "Consider taking a short break and stretching."
   - energy_level < 4 AND exercise_done == False → "A short walk might help boost your energy."
   - All good → positive reinforcement message
4. Return `PhysicalCheckInResponse`

**POST /medical/upload**
1. Validate: max 10MB, types: `.pdf`, `.docx` only (image OCR deferred)
2. Generate `doc_id = str(uuid.uuid4())`
3. Upload to Firebase Storage: `medical_reports/{uid}/{doc_id}/{filename}`
   - Use `firebase_admin.storage.bucket().blob(path).upload_from_string(bytes, content_type)`
4. Create Firestore doc in `medical_documents` with `status="uploaded"`
5. Launch `asyncio.create_task(process_medical_document(...))` — non-blocking
6. Return `MedicalDocumentUploadResponse(status="processing")`

**DELETE /medical/{doc_id}**
1. Verify doc belongs to requesting user
2. Delete from Firebase Storage
3. Delete RAG chunks: `store.delete_chunk(chunk_id)` for each id in `rag_chunk_ids`
4. Delete Firestore doc

**GET /trends**
- Query `physical_health_checkins` where `user_id == uid` and `created_at >= cutoff`
- Group by date (YYYY-MM-DD), compute daily averages
- Compute trend_direction per metric: compare first-half avg vs second-half avg
  - first_half > second_half → "declining"
  - second_half > first_half + 0.5 → "improving"
  - else → "stable"

**GET /score**
- Fetch last 30 days of check-ins
- Weighted composite: energy(25%) + sleep_quality(20%) + sleep_hours_score(15%) + nutrition(20%) + pain(10%) + hydration(10%)
- `sleep_hours_score` = min(sleep_hours / 8.0, 1.0) * 10
- streak_days: count consecutive days with at least 1 check-in (most recent streak)

**POST /ask**
- RAG retrieval: `store.retrieve(question, metadata_filter={"user_id": uid, "type": "medical_report"}, top_k=4)`
- If no results: return "No medical documents found. Please upload a report first."
- Build context from retrieved chunks
- LLM call with ANSWER_HEALTH_QUESTION prompt
- Return answer + source doc_ids + confidence + disclaimer

---

### Step 6 — Wire into `main.py`

Add 2 lines:

```python
# Line ~57 (with other imports):
from routers.physical_health import router as physical_health_router

# Line ~438 (with other include_router calls):
app.include_router(physical_health_router, prefix="/api")
```

---

### Step 7 — `requirements.txt` additions

```
pdfplumber>=0.10.0
pypdf2>=3.0.0
```

---

### Step 8 — `routers/recommendations.py` (optional, wire in physical health)

Extend `RecommendationRequest` with optional fields:
```python
sleep_quality: Optional[int] = None
sleep_hours: Optional[float] = None
exercise_minutes_today: Optional[int] = None
nutrition_quality: Optional[int] = None
pain_level: Optional[int] = None
```

Add 6 new fallback recommendation objects (sleep_improvement, movement, nutrition, pain_management, recovery, hydration) and update the LLM prompt to reference these if provided.

---

### Step 9 — `routers/employer_dashboard.py` (aggregate only)

Add function `_fetch_team_physical_health(company_id, db, days=7)`:
- Query `physical_health_checkins` where `company_id == x` and `created_at >= cutoff`
- Compute: avg_energy, avg_sleep_hours, exercise_participation_pct (% who logged exercise), low_energy_pct (% with energy < 4), sleep_deficit_pct (% with avg sleep_hours < 6)
- Return `team_physical_health_index` dict
- Privacy: suppress if team_size < K_ANON_THRESHOLD (same rule as mental health)
- **Never** query `medical_documents` from employer side

---

## Privacy Rules (Hard Requirements)

1. `medical_documents` — queried ONLY by matching `user_id`. Never by `company_id`.
2. All employer-facing metrics come from `physical_health_checkins` only — never from `medical_documents`.
3. `urgency_level == "emergency"` → write a `wellness_events` doc for the user (not employer-visible), surface in-app alert to user only.
4. Deletion: user can delete any doc. All three stores must be cleaned: Storage + Firestore + RAG chunks.

---

## Firestore Indexes to Add (append to `firestore.indexes.json`)

```json
{ "collectionGroup": "physical_health_checkins", "fields": [
    { "fieldPath": "user_id",    "order": "ASCENDING" },
    { "fieldPath": "created_at", "order": "DESCENDING" }
]},
{ "collectionGroup": "physical_health_checkins", "fields": [
    { "fieldPath": "company_id", "order": "ASCENDING" },
    { "fieldPath": "created_at", "order": "DESCENDING" }
]},
{ "collectionGroup": "medical_documents", "fields": [
    { "fieldPath": "user_id",    "order": "ASCENDING" },
    { "fieldPath": "uploaded_at","order": "DESCENDING" }
]},
{ "collectionGroup": "physical_health_reports", "fields": [
    { "fieldPath": "user_id",    "order": "ASCENDING" },
    { "fieldPath": "generated_at","order": "DESCENDING" }
]}
```

---

## Summary: What Gets Built, in Order

| Step | File | Status |
|---|---|---|
| 1 | `physical_health_schemas.py` | Create |
| 2 | `physical_health_prompts.py` | Create |
| 3 | `utils/pdf_parser.py` | Create |
| 4 | `physical_health_agent.py` | Create |
| 5 | `routers/physical_health.py` | Create |
| 6 | `main.py` (2 lines) | Modify |
| 7 | `requirements.txt` | Modify |
| 8 | `firestore.indexes.json` | Modify |
| 9 | `routers/recommendations.py` | Modify (optional, Phase 2) |
| 10 | `routers/employer_dashboard.py` | Modify (optional, Phase 2) |
