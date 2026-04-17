# Physical Health Feature Plan

**Goal:** Allow users to track their physical health over time, upload medical reports for AI-powered analysis, receive personalized health suggestions, and view their health trends — all integrated into the existing wellness platform.

**Strategy:** Build in 5 phases: data schema → file ingestion → AI analysis → tracking/trends → recommendations.

---

## What Already Exists (Build On These)

| Component | File | Status |
|---|---|---|
| Physical health LLM analysis (from conversation) | `report_agent.py:79-85` | Done — but conversation-only |
| `PhysicalHealthLLMOutput` Pydantic schema | `report_schemas.py:68-82` | Done (activity, nutrition, pain, lifestyle, absenteeism) |
| `PhysicalHealthBlock` output wrapper | `report_schemas.py:125-131` | Done |
| Physical health prompt | `report_prompts.py:91-140` | Done |
| RAG ingestion system | `rag.py` | Ready — just needs medical metadata |
| DOCX parser utility | `docx_ingest.py` | Done — needs PDF counterpart |
| File upload pattern | `routers/voice_calls.py:139-167` | Done (UploadFile + async read) |
| Recommendations engine | `routers/recommendations.py` | Done — needs physical health metrics wired in |
| Check-in collection | `check_ins` Firestore | Exists — only has mood_score + stress_level |

## Critical Gaps

- No physical health check-in endpoint (no energy, sleep, exercise, nutrition inputs)
- No medical report upload endpoint
- No PDF parsing (existing parser is DOCX-only)
- No `physical_health_reports` Firestore collection
- No `medical_documents` collection (to track uploaded files + status)
- No `health_metrics` time-series collection
- Medical data not wired into overall health score in reports
- Recommendations don't factor in physical health metrics yet

---

## Phase 1 — New Firestore Collections (Data Schema)

### 1a. `physical_health_checkins`

Daily/periodic self-reported physical health snapshot by user.

```
physical_health_checkins/{auto_id} = {
    user_id: str,
    company_id: str,
    created_at: Timestamp,

    # Self-reported metrics (all 1-10 scale)
    energy_level: int,          # 1 = exhausted, 10 = fully energized
    sleep_quality: int,         # 1 = terrible, 10 = excellent
    sleep_hours: float,         # Actual hours slept
    exercise_done: bool,        # Did they exercise today?
    exercise_minutes: int,      # Minutes of exercise (0 if none)
    exercise_type: str,         # "walk" | "gym" | "yoga" | "sport" | "none"
    nutrition_quality: int,     # 1 = poor, 10 = excellent
    pain_level: int,            # 1 = severe pain, 10 = no pain (inverted)
    hydration: int,             # 1 = dehydrated, 10 = well-hydrated
    mood_physical: int,         # Physical mood (separate from mental mood)
    notes: Optional[str]        # Free-text optional note
}
```

### 1b. `medical_documents`

Tracks each uploaded medical report file and its processing status.

```
medical_documents/{doc_id} = {
    user_id: str,
    company_id: str,
    uploaded_at: Timestamp,

    # File metadata
    filename: str,
    file_type: str,             # "pdf" | "docx" | "image"
    file_size_bytes: int,
    storage_path: str,          # Firebase Storage path

    # Classification
    report_type: str,           # "lab_work" | "blood_test" | "xray_mri" | "prescription" | "general_checkup" | "specialist" | "other"
    report_date: Optional[str], # Date on the report (user-provided or extracted)
    issuing_doctor: Optional[str],
    issuing_facility: Optional[str],

    # Processing state
    status: str,                # "uploaded" | "processing" | "analyzed" | "failed"
    raw_text: Optional[str],    # Extracted text from file
    rag_chunk_ids: List[str],   # Chunk IDs stored in RAG/vector store

    # AI Analysis Output
    summary: Optional[str],             # Plain-language summary for user
    key_findings: Optional[List[str]],  # Bullet-point findings
    flagged_values: Optional[List[dict]], # Abnormal values: [{name, value, normal_range, status}]
    recommendations: Optional[List[str]],
    follow_up_needed: Optional[bool],
    urgency_level: str,         # "routine" | "follow_up" | "urgent" | "emergency"
    analyzed_at: Optional[Timestamp]
}
```

### 1c. `physical_health_reports`

Periodic (weekly/monthly) aggregated physical health report — analogous to `mental_health_reports`.

```
physical_health_reports/{report_id} = {
    user_id: str,
    company_id: str,
    generated_at: Timestamp,
    period_start: Timestamp,
    period_end: Timestamp,
    report_type: str,           # "weekly" | "monthly" | "on_demand"

    # Aggregated scores from check-ins
    avg_energy: float,
    avg_sleep_quality: float,
    avg_sleep_hours: float,
    avg_exercise_minutes_per_day: float,
    avg_nutrition_quality: float,
    avg_pain_level: float,
    avg_hydration: float,
    exercise_days_count: int,

    # LLM-generated analysis
    overall_score: float,       # 0-10 composite
    overall_level: str,         # "low" | "medium" | "high"
    trend: str,                 # "improving" | "stable" | "declining"
    summary: str,
    strengths: List[str],       # Areas doing well
    concerns: List[str],        # Areas needing attention
    recommendations: List[str],

    # Medical context (from uploaded reports in same period)
    medical_doc_ids: List[str], # References to medical_documents used
    clinical_context: Optional[str],

    # Risk signals
    risk_flags: List[str],      # e.g. ["chronic_low_energy", "sleep_deficit", "sedentary"]
    follow_up_suggested: bool
}
```

### 1d. `health_metrics` (Time-Series Aggregates)

Daily/weekly aggregated metrics per user — for trend charts and KPI tracking.

```
health_metrics/{user_id}_{date} = {
    user_id: str,
    company_id: str,
    date: str,                  # "YYYY-MM-DD"
    week: str,                  # "YYYY-WXX"

    # Daily averages (from check-ins that day)
    energy_avg: float,
    sleep_quality_avg: float,
    sleep_hours_avg: float,
    exercise_minutes: int,
    nutrition_avg: float,
    pain_avg: float,
    hydration_avg: float,
    check_in_count: int         # How many check-ins contributed
}
```

---

## Phase 2 — File Ingestion & Medical Report Upload

### New Router: `routers/physical_health.py`

New router mounted at `/api/physical-health`.

### 2a. Medical Report Upload Endpoint

`POST /api/physical-health/upload-report`

**Flow:**
1. Receive `UploadFile` (PDF, DOCX, JPG/PNG for scans)
2. Validate file type and size (max 10MB)
3. Upload raw file to **Firebase Storage** at `medical_reports/{user_id}/{doc_id}/{filename}`
4. Create `medical_documents` Firestore doc with `status: "uploaded"`
5. Trigger async background task: text extraction → RAG ingestion → AI analysis
6. Return `{ doc_id, status: "processing" }` immediately (non-blocking)

**File Type Handlers:**

| File Type | Parser | Library |
|---|---|---|
| `.pdf` | Extract text layer; OCR fallback for scans | `pypdf2` or `pdfplumber` |
| `.docx` | Existing `docx_ingest.py` pattern | `python-docx` (already in requirements) |
| `.jpg` / `.png` | OCR for scanned reports | `pytesseract` or Azure Vision API |

Add to `requirements.txt`:
```
pdfplumber>=0.10.0
pypdf2>=3.0.0
# pytesseract>=0.3.10  # optional, for scanned image OCR
# azure-cognitiveservices-vision-computervision  # optional, Azure OCR
```

### 2b. RAG Ingestion for Medical Documents

Reuse existing `rag.py` `RAGStore.add_documents()` with medical-specific metadata:

```python
metadata = {
    "type": "medical_report",
    "user_id": user_id,
    "doc_id": doc_id,
    "report_type": report_type,   # "lab_work", "blood_test", etc.
    "report_date": report_date,
}
store.add_documents(
    texts=[extracted_text],
    metadata_per_doc=[metadata],
    chunk_size=400,               # Smaller chunks for clinical text
    chunk_overlap=80,
)
```

This enables: "What did my last blood test say?" → RAG retrieves the relevant chunk → LLM answers.

### 2c. Document Status Poll Endpoint

`GET /api/physical-health/report/{doc_id}/status`

Returns current `status` of document processing. Frontend polls this until `"analyzed"`.

### 2d. Get All Documents

`GET /api/physical-health/reports`

Returns paginated list of user's uploaded medical documents with their summaries and urgency levels.

---

## Phase 3 — AI Analysis Pipeline

### 3a. Medical Report Analysis

New file: `physical_health_agent.py`

**Analysis Node 1 — Extract Structured Findings**

LLM prompt: Analyze this medical report text and extract:
- Key findings in plain language (avoid medical jargon)
- Any abnormal/flagged values with their normal ranges
- What each finding means for the patient
- Whether follow-up is required and how urgently
- Overall health signal (routine / follow-up / urgent / emergency)

Output schema — new Pydantic model `MedicalReportAnalysis`:

```python
class FlaggedValue(BaseModel):
    name: str                   # e.g. "Blood Glucose"
    value: str                  # e.g. "126 mg/dL"
    normal_range: str           # e.g. "70-99 mg/dL"
    status: str                 # "high" | "low" | "normal" | "borderline"
    plain_explanation: str      # What this means for the user

class MedicalReportAnalysis(BaseModel):
    report_type: str
    report_date: Optional[str]
    summary: str                # 3-5 sentence plain-language summary
    key_findings: List[str]     # Bullet points
    flagged_values: List[FlaggedValue]
    follow_up_needed: bool
    urgency_level: str          # "routine" | "follow_up" | "urgent" | "emergency"
    recommendations: List[str]  # Actionable next steps
    confidence: float           # 0-1
```

**Analysis Node 2 — Personalized Suggestions**

After extracting findings, second LLM call that:
- Cross-references findings with user's recent check-in history
- Generates personalized lifestyle suggestions (diet, exercise, sleep) based on results
- Flags if any finding warrants an HR wellness program referral (without exposing specifics)

### 3b. Physical Health Check-In Analysis

`POST /api/physical-health/check-in` → after saving check-in, run a lightweight LLM pass:
- Compare today's values against user's historical averages
- Detect anomalies (e.g., sleep_hours < 5 for 3+ days, pain_level < 4 sustained)
- Generate a short 1-2 sentence personalized nudge returned in the API response

### 3c. Periodic Report Generation

`POST /api/physical-health/generate-report` (or auto-triggered weekly)

Aggregates all check-ins and medical documents from the period into a `physical_health_reports` doc using a 3-node pipeline:

1. **aggregate_metrics** — compute period averages from `physical_health_checkins`
2. **fetch_medical_context** — RAG retrieval of user's medical documents for the period
3. **generate_report** — LLM synthesizes check-in trends + medical context into full report

---

## Phase 4 — Health Tracking & Trends API

Additional endpoints in `routers/physical_health.py`:

| Method | Endpoint | Purpose |
|---|---|---|
| POST | `/api/physical-health/check-in` | Submit daily physical health check-in |
| GET | `/api/physical-health/check-ins` | Get paginated check-in history |
| GET | `/api/physical-health/trends` | Time-series data for charts (energy, sleep, exercise etc.) |
| GET | `/api/physical-health/score` | Current composite physical health score |
| GET | `/api/physical-health/reports` | List all generated health reports |
| GET | `/api/physical-health/reports/{id}` | Full single health report |
| POST | `/api/physical-health/upload-report` | Upload medical document |
| GET | `/api/physical-health/medical/{doc_id}` | Get analyzed medical document |
| GET | `/api/physical-health/medical/{doc_id}/status` | Poll processing status |
| GET | `/api/physical-health/medical` | List all user's medical documents |
| POST | `/api/physical-health/ask` | Ask a question about uploaded medical history (RAG-powered) |
| POST | `/api/physical-health/generate-report` | Trigger on-demand periodic report |

### `/api/physical-health/trends` Response Shape

```json
{
  "period": "30d",
  "data_points": [
    {
      "date": "2026-04-01",
      "energy_level": 7.2,
      "sleep_quality": 6.8,
      "sleep_hours": 7.1,
      "exercise_minutes": 35,
      "nutrition_quality": 6.5,
      "pain_level": 8.2,
      "hydration": 7.0
    }
  ],
  "averages": {
    "energy_level": 6.9,
    "sleep_quality": 6.5,
    "sleep_hours": 6.8,
    "exercise_days_per_week": 3.2
  },
  "trend_direction": {
    "energy_level": "improving",
    "sleep_quality": "stable",
    "exercise_minutes": "declining"
  }
}
```

### `/api/physical-health/ask` — Medical History Q&A

```json
// Request
{ "question": "What were my cholesterol levels in my last blood test?" }

// Response
{
  "answer": "Your last blood test (March 2026) showed total cholesterol at 198 mg/dL (normal range: < 200), LDL at 112 mg/dL (borderline high), and HDL at 58 mg/dL (healthy). Overall within normal range but LDL is worth monitoring.",
  "source_doc_ids": ["doc_abc123"],
  "confidence": 0.87
}
```

**Implementation:** RAG retrieval filtered by `user_id` + `type=medical_report`, then LLM generates grounded answer.

---

## Phase 5 — Recommendations Integration

### 5a. Wire Physical Health Into Existing Recommendations Engine

Extend `RecommendationRequest` in `routers/recommendations.py` with physical health fields:

```python
class RecommendationRequest(BaseModel):
    # Existing fields
    employee_id: str
    company_id: str
    current_mood: int
    current_stress: int
    current_energy: int
    time_available: int

    # New physical health fields (all optional, gracefully degrade)
    energy_level: Optional[int] = None      # From latest physical check-in
    sleep_quality: Optional[int] = None
    sleep_hours: Optional[float] = None
    exercise_minutes_today: Optional[int] = None
    nutrition_quality: Optional[int] = None
    pain_level: Optional[int] = None
    has_medical_flags: Optional[bool] = None  # Are there flagged values in recent reports?
```

### 5b. New Recommendation Categories for Physical Health

Add to fallback recommendations and LLM prompt:

| Category | Trigger Condition | Examples |
|---|---|---|
| `sleep_improvement` | sleep_quality < 5 OR sleep_hours < 6 | Sleep hygiene tips, wind-down routine, screen time reduction |
| `movement` | exercise_minutes_today == 0 AND energy > 4 | Desk stretches, walking breaks, 10-min workout |
| `nutrition` | nutrition_quality < 5 | Hydration reminder, meal prep tips, healthy snack ideas |
| `pain_management` | pain_level < 5 | Ergonomics check, posture correction, rest guidance |
| `recovery` | energy_level < 4 | Rest prioritization, stress reduction, light activity only |
| `hydration` | hydration < 5 | Water intake reminder, hydration tracking |

### 5c. Smart Medical Nudges

After a medical document is analyzed with `follow_up_needed: true`:
- Send a gentle, privacy-preserving nudge to the user via in-app notification (not email)
- Language: "Your recent health document has some findings worth discussing with a healthcare professional."
- Never expose specific values in notifications

---

## Phase 6 — Employer/Admin Visibility (Aggregate Only)

Extend existing employer dashboard with anonymized physical health signals:

### New Metric: `team_physical_health_index`

Added to `routers/employer_dashboard.py`:

```python
# Computed from physical_health_checkins, aggregated at company level
team_physical_health_index = {
    "score": 6.8,                # 0-10 composite
    "avg_energy": 6.4,
    "avg_sleep_hours": 6.9,
    "exercise_participation_pct": 43.2,   # % who logged exercise this week
    "low_energy_pct": 18.5,               # % with energy < 4 (anonymized)
    "sleep_deficit_pct": 22.1,            # % averaging < 6 hrs
    "trend": "stable"
}
```

**Privacy rules (same as mental health):**
- All values are percentages or averages — never individual data
- Suppress cohort if team size < K_ANON_THRESHOLD
- No medical document data ever surfaced to employers (documents are user-private only)
- Medical documents are 100% private to the individual user

---

## File Change Summary

| File | Type | Change |
|---|---|---|
| `routers/physical_health.py` | **New** | All physical health endpoints |
| `physical_health_agent.py` | **New** | LLM analysis pipeline for check-ins + medical reports |
| `physical_health_schemas.py` | **New** | Pydantic models: MedicalReportAnalysis, FlaggedValue, PhysicalCheckIn, HealthTrend |
| `physical_health_prompts.py` | **New** | LLM prompts for medical report analysis and report generation |
| `utils/pdf_parser.py` | **New** | PDF text extraction utility (pdfplumber) |
| `utils/image_ocr.py` | **New** | Optional OCR for scanned report images |
| `main.py` | **Modify** | Include new `physical_health` router |
| `routers/recommendations.py` | **Modify** | Extend request schema with physical health fields; add new recommendation categories |
| `routers/employer_dashboard.py` | **Modify** | Add `team_physical_health_index` metric |
| `report_agent.py` | **Modify** | Optionally pull latest physical check-in data into existing report analysis |
| `requirements.txt` | **Modify** | Add `pdfplumber`, `pypdf2`, optionally `pytesseract` |

## New Firestore Collections

| Collection | Purpose | Privacy |
|---|---|---|
| `physical_health_checkins` | Daily self-reported health metrics | User-private + employer aggregate only |
| `medical_documents` | Uploaded file metadata, extracted text, AI analysis | **User-private only — never surfaced to employer** |
| `physical_health_reports` | Periodic aggregated health reports | User-private + employer aggregate only |
| `health_metrics` | Daily time-series aggregates for trend charts | User-private + employer aggregate only |

## Firebase Storage Paths

```
medical_reports/{user_id}/{doc_id}/{original_filename}   # Raw uploaded files
```

---

## New Dependencies

```
pdfplumber>=0.10.0          # PDF text extraction
pypdf2>=3.0.0               # PDF fallback parser
# pytesseract>=0.3.10       # OCR for scanned images (optional)
# Pillow>=10.0.0            # Image handling for OCR (optional)
```

---

## Privacy & Safety Guardrails

1. **Medical documents are 100% user-private** — never accessible to employer, HR, or admin
2. **No diagnoses** — LLM is instructed to summarize findings, never diagnose conditions
3. **Emergency escalation** — if `urgency_level == "emergency"` detected in a report, immediately surface a prominent in-app alert: "This report contains findings that may require immediate medical attention. Please contact a healthcare professional."
4. **Data residency** — medical documents stored in Firebase Storage with user-scoped security rules (only the user's UID can access their own path)
5. **Deletion** — user can delete any uploaded document at any time; triggers deletion from Storage, Firestore, and all RAG vector chunks

---

## Build Order (Recommended)

1. Firestore collections + security rules
2. Physical health check-in endpoint (quick win, no file handling)
3. Trends + score endpoints (unblocks frontend)
4. PDF parser utility
5. Medical report upload + async processing pipeline
6. Medical Q&A endpoint (`/ask`)
7. Periodic report generation
8. Recommendations integration
9. Employer dashboard aggregate metrics
