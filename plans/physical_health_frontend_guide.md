# Physical Health Feature — Frontend Engineering Guide

> **For:** Frontend Engineer  
> **Base URL:** All endpoints are prefixed `/api/physical-health`  
> **Auth:** Every request must include `Authorization: Bearer <firebase_id_token>` header  
> **Content-Type:** `application/json` unless noted (file upload uses `multipart/form-data`)

---

## Table of Contents

1. [Feature Overview](#1-feature-overview)
2. [Screens & Components](#2-screens--components)
3. [API Reference](#3-api-reference)
4. [Data Types & Enums](#4-data-types--enums)
5. [UX Rules & Edge Cases](#5-ux-rules--edge-cases)
6. [Component Details](#6-component-details)

---

## 1. Feature Overview

The Physical Health section lets employees:

- Submit a **daily check-in** (energy, sleep, exercise, nutrition, pain, hydration)
- View their **current health score** and **streaks**
- See **trends over time** in charts
- **Upload medical documents** (PDF/DOCX) and get AI-powered plain-language analysis
- **Ask questions** about their uploaded medical history (RAG-powered)
- Generate a **periodic health report** (weekly / monthly / on-demand)

**Privacy note for UI copy:** Medical documents are 100% private to the user. Never shown to employer. Make this visible in the UI.

---

## 2. Screens & Components

### Screen Map

```
Physical Health (tab/section)
├── Dashboard                   ← Health score card + today's nudge + streak
│   ├── ScoreCard
│   ├── StreakBadge
│   ├── CheckInPromptBanner      ← shown if no check-in today
│   └── QuickStatsRow            ← avg energy, sleep, exercise this week
│
├── Check-In                    ← Daily check-in form
│   └── CheckInForm
│
├── Trends                      ← Charts
│   ├── MetricSelector           ← toggle between metrics
│   └── TrendChart               ← line/area chart, period selector (7d / 30d / 90d)
│
├── Medical Documents           ← Upload + list
│   ├── UploadCard
│   ├── DocumentList
│   │   └── DocumentCard         ← per document
│   └── DocumentDetailDrawer     ← slide-in or modal with full analysis
│       ├── SummaryBlock
│       ├── FlaggedValuesList
│       ├── RecommendationsList
│       └── UrgencyBadge
│
├── Health Reports              ← Periodic AI reports
│   ├── GenerateReportButton
│   ├── ReportList
│   └── ReportDetailView
│       ├── ScoreGauge
│       ├── StrengthsConcernsList
│       └── RecommendationsList
│
└── Ask My Documents            ← RAG chat interface
    ├── QuestionInput
    ├── AnswerCard
    └── DisclaimerBanner
```

---

## 3. API Reference

### 3.1 Submit Daily Check-In

**`POST /api/physical-health/check-in`**

Used by: `CheckInForm`

**Request body:**
```json
{
  "energy_level": 7,
  "sleep_quality": 6,
  "sleep_hours": 7.5,
  "exercise_done": true,
  "exercise_minutes": 30,
  "exercise_type": "walk",
  "nutrition_quality": 8,
  "pain_level": 9,
  "hydration": 7,
  "notes": "Felt a bit tired in the afternoon"
}
```

| Field | Type | Range | Notes |
|---|---|---|---|
| `energy_level` | int | 1–10 | 1 = exhausted, 10 = fully energised |
| `sleep_quality` | int | 1–10 | 1 = terrible, 10 = excellent |
| `sleep_hours` | float | 0–24 | Hours slept |
| `exercise_done` | bool | — | Did they exercise today? |
| `exercise_minutes` | int | 0+ | 0 if no exercise |
| `exercise_type` | string | enum | `walk \| gym \| yoga \| sport \| other \| none` |
| `nutrition_quality` | int | 1–10 | 1 = poor diet, 10 = excellent |
| `pain_level` | int | 1–10 | **Inverted**: 1 = severe pain, 10 = no pain |
| `hydration` | int | 1–10 | 1 = dehydrated, 10 = well-hydrated |
| `notes` | string? | optional | Free text |

**Response `201`:**
```json
{
  "success": true,
  "checkin_id": "uuid",
  "nudge": "Great job exercising today! Keep the momentum going."
}
```

`nudge` is a short personalised tip (can be null). Show as a toast or inline tip after submission.

---

### 3.2 Check-In History

**`GET /api/physical-health/check-ins`**

Used by: history tab, trends page

**Query params:**

| Param | Default | Notes |
|---|---|---|
| `page` | 1 | Page number |
| `limit` | 20 | Items per page |
| `days` | 30 | Lookback window in days |

**Response `200`:**
```json
{
  "success": true,
  "checkins": [
    {
      "checkin_id": "uuid",
      "created_at": "2026-04-15T08:30:00+00:00",
      "energy_level": 7,
      "sleep_quality": 6,
      "sleep_hours": 7.5,
      "exercise_done": true,
      "exercise_minutes": 30,
      "exercise_type": "walk",
      "nutrition_quality": 8,
      "pain_level": 9,
      "hydration": 7,
      "notes": null
    }
  ],
  "total": 45,
  "page": 1,
  "limit": 20,
  "totalPages": 3,
  "hasNext": true,
  "hasPrev": false
}
```

---

### 3.3 Current Health Score

**`GET /api/physical-health/score`**

Used by: `ScoreCard`, `Dashboard`

**Response `200`:**
```json
{
  "score": 7.4,
  "level": "high",
  "last_checkin_date": "2026-04-15T08:30:00+00:00",
  "days_since_checkin": 0,
  "streak_days": 5,
  "highlights": ["Good energy levels this week", "Consistent hydration"],
  "concerns": ["Sleep hours below 7 on 3 days"]
}
```

| Field | Notes |
|---|---|
| `score` | 0–10 float |
| `level` | `low \| medium \| high` |
| `streak_days` | Consecutive days with at least one check-in |
| `highlights` | Positive notes — show in green |
| `concerns` | Areas needing attention — show in amber/orange |

**No data case:** Returns `score: 0`, `streak_days: 0`, `last_checkin_date: null`. Show "No check-ins yet" state.

---

### 3.4 Health Trends

**`GET /api/physical-health/trends`**

Used by: `TrendChart`

**Query params:**

| Param | Default | Options |
|---|---|---|
| `period` | `30d` | `7d \| 30d \| 90d` |

**Response `200`:**
```json
{
  "period": "30d",
  "data_points": [
    {
      "date": "2026-03-17",
      "energy_level": 7.0,
      "sleep_quality": 6.5,
      "sleep_hours": 7.2,
      "exercise_minutes": 25,
      "nutrition_quality": 8.0,
      "pain_level": 8.5,
      "hydration": 7.0
    }
  ],
  "averages": {
    "avg_energy": 6.8,
    "avg_sleep_quality": 6.2,
    "avg_sleep_hours": 7.1,
    "avg_nutrition_quality": 7.5,
    "avg_pain_level": 8.0,
    "avg_hydration": 6.9,
    "exercise_days": 18
  },
  "trend_direction": {
    "energy_level": "improving",
    "sleep_quality": "stable",
    "sleep_hours": "declining",
    "nutrition_quality": "stable",
    "pain_level": "improving",
    "hydration": "stable"
  },
  "total_checkins": 24
}
```

`data_points` are daily aggregated averages. Some days may be missing (no check-in that day) — handle gaps gracefully in the chart.

`trend_direction` values: `improving | stable | declining`

---

### 3.5 Upload Medical Document

**`POST /api/physical-health/medical/upload`**

Used by: `UploadCard`

**Content-Type:** `multipart/form-data`

| Field | Type | Notes |
|---|---|---|
| `file` | file | PDF or DOCX only, max 10 MB |
| `report_type` | string | See report type enum below |
| `issuing_facility` | string? | Optional: hospital/lab name |
| `report_date` | string? | Optional: `YYYY-MM-DD` |

**Response `201`:**
```json
{
  "success": true,
  "doc_id": "uuid",
  "status": "processing",
  "message": "Your document is being analysed. This usually takes under a minute."
}
```

**Important UX:** Upload returns immediately with `status: "processing"`. Poll `/medical/{doc_id}/status` every 3–5 seconds until status becomes `analyzed` or `failed`.

---

### 3.6 Poll Document Status

**`GET /api/physical-health/medical/{doc_id}/status`**

Used by: processing spinner on `DocumentCard`

**Response `200`:**
```json
{
  "doc_id": "uuid",
  "status": "analyzed",
  "analyzed_at": "2026-04-15T08:35:00+00:00",
  "urgency_level": "follow_up"
}
```

Status values: `uploaded | processing | analyzed | failed`

Stop polling when status is `analyzed` or `failed`. On `failed`, show error state with retry option.

---

### 3.7 List Medical Documents

**`GET /api/physical-health/medical`**

Used by: `DocumentList`

**Query params:**

| Param | Default |
|---|---|
| `page` | 1 |
| `limit` | 10 |

**Response `200`:**
```json
{
  "success": true,
  "documents": [
    {
      "doc_id": "uuid",
      "filename": "blood_test_march.pdf",
      "report_type": "blood_test",
      "report_date": "2026-03-10",
      "issuing_facility": null,
      "status": "analyzed",
      "uploaded_at": "2026-04-15T08:30:00+00:00",
      "analyzed_at": "2026-04-15T08:32:00+00:00",
      "summary": null,
      "key_findings": null,
      "flagged_values": null,
      "recommendations": null,
      "follow_up_needed": null,
      "urgency_level": "routine"
    }
  ],
  "total": 3
}
```

> The list response shows minimal fields. Tap a document to call `/medical/{doc_id}` for the full analysis.

---

### 3.8 Get Document Detail

**`GET /api/physical-health/medical/{doc_id}`**

Used by: `DocumentDetailDrawer`

**Response `200`:**
```json
{
  "doc_id": "uuid",
  "filename": "blood_test_march.pdf",
  "report_type": "blood_test",
  "report_date": "2026-03-10",
  "issuing_facility": "City Medical Lab",
  "status": "analyzed",
  "uploaded_at": "2026-04-15T08:30:00+00:00",
  "analyzed_at": "2026-04-15T08:32:00+00:00",
  "summary": "Your blood test results are mostly within normal ranges. Vitamin D levels are slightly low, which is common and easy to address with diet and sunlight. Your cholesterol is borderline and worth monitoring.",
  "key_findings": [
    "Haemoglobin: 13.8 g/dL — within normal range",
    "Vitamin D: 18 ng/mL — slightly below recommended level",
    "LDL Cholesterol: 128 mg/dL — borderline high"
  ],
  "flagged_values": [
    {
      "name": "Vitamin D",
      "value": "18 ng/mL",
      "normal_range": "20–50 ng/mL",
      "status": "low",
      "plain_explanation": "Your Vitamin D is slightly low. This can cause fatigue and affect mood. Spending time in sunlight and eating fortified foods can help."
    },
    {
      "name": "LDL Cholesterol",
      "value": "128 mg/dL",
      "normal_range": "< 100 mg/dL",
      "status": "borderline",
      "plain_explanation": "Your LDL (bad cholesterol) is a little above ideal. Reducing saturated fats and increasing exercise can help bring it down."
    }
  ],
  "recommendations": [
    "Increase Vitamin D through 15 minutes of sunlight daily or fortified dairy products.",
    "Reduce saturated fats — choose grilled over fried, and limit processed snacks.",
    "Aim for 30 minutes of moderate exercise 5 days a week to help manage cholesterol.",
    "Schedule a follow-up blood test in 3 months to track changes."
  ],
  "follow_up_needed": true,
  "urgency_level": "follow_up"
}
```

---

### 3.9 Delete Medical Document

**`DELETE /api/physical-health/medical/{doc_id}`**

Used by: delete button on `DocumentCard`

**Response `200`:**
```json
{ "success": true, "message": "Document deleted." }
```

Cleans up: Firebase Storage file + AI analysis data + all references. Show a confirmation dialog before calling this.

---

### 3.10 Generate Periodic Report

**`POST /api/physical-health/reports/generate`**

Used by: `GenerateReportButton`

**Request body:**
```json
{
  "report_type": "on_demand",
  "days": 30
}
```

| Field | Options | Notes |
|---|---|---|
| `report_type` | `weekly \| monthly \| on_demand` | Use `on_demand` for manual trigger |
| `days` | 7–365 | Lookback window |

**Response `200`:** Full `PeriodicReportResponse` (see §3.11)

**Error `400`:** Fewer than 3 check-ins in the period → `"Not enough check-in data to generate a report. Please complete at least 3 check-ins first."`

---

### 3.11 Get Report Detail / List

**`GET /api/physical-health/reports/{report_id}`**

**Response `200`:**
```json
{
  "report_id": "uuid",
  "period_start": "2026-03-16T00:00:00+00:00",
  "period_end": "2026-04-15T00:00:00+00:00",
  "report_type": "on_demand",
  "overall_score": 6.8,
  "overall_level": "medium",
  "trend": "improving",
  "avg_energy": 6.5,
  "avg_sleep_quality": 6.2,
  "avg_sleep_hours": 7.1,
  "avg_exercise_minutes_daily": 22.5,
  "avg_nutrition_quality": 7.0,
  "avg_pain_level": 8.1,
  "exercise_days": 18,
  "summary": "Over the past 30 days your energy and nutrition have been reasonably good, but sleep consistency could be improved. You've been active on more than half the days, which is great progress.",
  "strengths": [
    "Good nutrition quality across the period",
    "Active on 18 out of 30 days",
    "Pain levels consistently low"
  ],
  "concerns": [
    "Average sleep hours slightly below the recommended 8h",
    "Energy levels dipped noticeably in the second half of the period"
  ],
  "recommendations": [
    "Set a consistent bedtime — even on weekends — to stabilise sleep quality.",
    "Add a 10-minute wind-down routine before bed to improve sleep onset.",
    "On low-energy days, a 20-minute walk can naturally boost alertness.",
    "Continue tracking nutrition to maintain the positive trend."
  ],
  "follow_up_suggested": false,
  "generated_at": "2026-04-15T09:00:00+00:00"
}
```

**`GET /api/physical-health/reports`**

Query params: `page` (default 1), `limit` (default 10)

Returns a list of past reports (same shape, array in `reports` field).

---

### 3.12 Ask My Documents (RAG Q&A)

**`POST /api/physical-health/ask`**

Used by: `Ask My Documents` screen

**Request body:**
```json
{
  "question": "What were my cholesterol levels in the last blood test?"
}
```

**Response `200`:**
```json
{
  "answer": "According to your March 2026 blood test, your LDL cholesterol was 128 mg/dL, which is borderline high (normal is under 100 mg/dL). Your HDL was 52 mg/dL, which is within the healthy range.",
  "source_doc_ids": ["uuid-1"],
  "confidence": 0.87,
  "disclaimer": "This information is derived from your uploaded documents and is not medical advice. Always consult a qualified healthcare professional."
}
```

**No documents case:** Returns `answer: "No medical documents found. Please upload a report first."` with empty `source_doc_ids`.

Always show `disclaimer` text below every answer — this is a hard requirement.

---

## 4. Data Types & Enums

### `exercise_type`
`walk | gym | yoga | sport | other | none`

### `report_type` (medical document)
| Value | Display label |
|---|---|
| `lab_work` | Lab Work |
| `blood_test` | Blood Test |
| `xray_mri` | X-Ray / MRI |
| `prescription` | Prescription |
| `general_checkup` | General Check-up |
| `specialist` | Specialist Report |
| `other` | Other |

### `urgency_level`
| Value | Colour | Display |
|---|---|---|
| `routine` | Green | All good |
| `follow_up` | Amber | Worth discussing at next appointment |
| `urgent` | Orange | See a doctor soon |
| `emergency` | Red | Seek immediate medical attention |

### `status` (medical document)
| Value | Display |
|---|---|
| `uploaded` | Uploaded |
| `processing` | Analysing… |
| `analyzed` | Analysis ready |
| `failed` | Analysis failed |

### `level` (health score)
| Value | Score range | Colour |
|---|---|---|
| `low` | 0–4 | Red |
| `medium` | 4–7 | Amber |
| `high` | 7–10 | Green |

### `trend` (periodic report)
`improving | stable | declining`

---

## 5. UX Rules & Edge Cases

### 5.1 Check-In

- **One check-in per day** is enforced server-side. If the user has already submitted today, show today's summary instead of the form. Check `last_checkin_date` from `/score` — if it equals today's date, suppress the form.
- `pain_level` slider is **inverted** — label the low end "Severe pain" and the high end "No pain". Do not label it "Pain level: 1–10" without context.
- After submission, show the `nudge` as a toast or inline card.

### 5.2 Medical Document Upload

- Show a clear privacy notice before the upload button: _"Your documents are encrypted and only visible to you. They are never shared with your employer."_
- Accepted formats: **PDF and DOCX only**, max **10 MB**.
- After uploading, the file goes into a `processing` state. Show a spinner/animated badge on the document card. Poll `/medical/{doc_id}/status` every **4 seconds**. Stop polling at `analyzed` or `failed`.
- On `urgency_level: emergency`, show a red alert banner inside the document detail (not a push notification). Copy: _"This document contains findings that may need immediate attention. Please contact a healthcare professional as soon as possible."_
- On `failed`, show: _"We couldn't analyse this document. Please check it's a readable PDF or DOCX file and try uploading again."_

### 5.3 Ask My Documents

- Show the disclaimer on **every** response, not just the first one.
- If `source_doc_ids` is empty, show: _"No relevant information found in your uploaded documents."_
- Disable the send button if no documents exist (`/medical` returns empty list).

### 5.4 Periodic Reports

- Minimum 3 check-ins required. If the user hasn't checked in enough, show: _"Complete at least 3 check-ins to generate your health report."_
- Report generation is synchronous (takes 5–10 seconds). Show a loading state on the button.
- `follow_up_suggested: true` → show a soft amber banner: _"Based on your data, speaking with a healthcare professional might be beneficial."_

### 5.5 Trends Chart

- Handle **missing dates** (days with no check-in) — show gaps or interpolate, don't break the chart.
- Show `trend_direction` per metric as a small arrow or label (`↑ Improving`, `→ Stable`, `↓ Declining`).
- `pain_level` is inverted (10 = no pain). Label the Y-axis accordingly or flip it visually so "better" always goes up.

### 5.6 Score Card

- If `days_since_checkin` is null or > 1, show a prompt to check in today.
- `streak_days: 0` → _"Start your streak — check in today!"_
- `streak_days >= 7` → show a streak badge.

---

## 6. Component Details

### `CheckInForm`

Sliders or segmented controls for each metric (1–10). Consider grouping:
- **Energy & Sleep**: energy_level, sleep_quality, sleep_hours (numeric input)
- **Activity**: exercise_done (toggle), exercise_minutes, exercise_type (dropdown)
- **Body**: nutrition_quality, pain_level, hydration
- **Notes**: optional textarea at the bottom

Submit → `POST /check-in` → show nudge toast → navigate to dashboard.

---

### `ScoreCard`

Circular gauge or large number showing `score` (0–10).
- Color: red (`low`), amber (`medium`), green (`high`)
- Sub-text: `level` label
- Bottom row: streak badge, last check-in date

---

### `TrendChart`

Line or area chart. Recommended library: Recharts, Chart.js, or Victory.

- X-axis: date
- Y-axis: 1–10 (note: `sleep_hours` goes 0–12+)
- `MetricSelector`: tabs or multi-select chips to toggle which metrics are visible
- Period selector: pill buttons for `7d / 30d / 90d` — on change, re-fetch `/trends?period=Xd`

---

### `DocumentCard`

Compact card showing:
- Filename + report type label
- Uploaded date
- Status badge (colour-coded)
- Urgency badge (if analyzed)
- Tap → opens `DocumentDetailDrawer`
- Long-press or overflow menu → delete (with confirmation)

---

### `DocumentDetailDrawer`

Full-height bottom sheet or side drawer:

1. **Header**: filename, report type, report date, issuing facility
2. **Urgency badge**: colour-coded, copy from urgency label table
3. **Summary block**: paragraph of AI-generated plain-language summary
4. **Key findings**: bulleted list
5. **Flagged values** (if any): table or card list — name, value vs. normal range, status badge, plain explanation
6. **Recommendations**: numbered list
7. **Footer**: _"This AI summary is for informational purposes only. Always consult your doctor."_

---

### `ReportDetailView`

1. **Score gauge** (0–10 circular) + overall_level badge + trend arrow
2. **Period**: start → end date
3. **Metric summary bar**: mini-stats for energy, sleep, exercise, nutrition, pain, hydration
4. **Strengths** (green section): bulleted
5. **Concerns** (amber section): bulleted
6. **Recommendations**: numbered list
7. **Follow-up banner** (if `follow_up_suggested: true`)

---

### `AskMyDocuments`

Chat-style interface:
- Input box at the bottom: "Ask a question about your health documents…"
- Each answer card shows:
  - The question (user bubble)
  - The answer (AI bubble)
  - Disclaimer in small grey text below every answer
  - Optional: "Based on: [filename]" link if `source_doc_ids` is populated
- If no documents uploaded: show empty state with a link to upload section

---

## Appendix — HTTP Status Codes

| Code | Meaning |
|---|---|
| `200` | Success |
| `201` | Created (check-in, upload) |
| `400` | Bad request / validation error |
| `401` | Unauthenticated |
| `403` | Forbidden (wrong user) |
| `404` | Resource not found |
| `413` | File too large (>10 MB) |
| `415` | Unsupported file type |
| `503` | Backend unavailable |
