# Frontend Guide — Bulk Employee Import

Everything the frontend team needs to know to integrate the bulk employee
import feature. APIs, request/response shapes, recommended UI flow, and
the CSV schema to share with clients.

---

## APIs

### 1. Download the CSV Template
```
GET /api/employees/import/template
Authorization: Bearer <token>
```
Returns a `.csv` file. Frontend should show a **"Download Template"** button.
The user downloads it, fills it in Excel / Google Sheets, saves as CSV, and uploads it.

---

### 2. Validate Before Uploading (Dry Run)
```
POST /api/employees/import
Authorization: Bearer <token>
Content-Type: multipart/form-data

file: <the .csv or .xlsx file>
dry_run: true
```
**Call this first** — when the user selects a file, before they confirm.
Validates the entire file without creating any accounts.
Show errors so the user can fix the file before committing.

**Success response:**
```json
{
  "valid": true,
  "total_rows": 50,
  "valid_rows": 50,
  "error_count": 0,
  "errors": [],
  "duplicate_emails": [],
  "preview": [
    { "row": 2, "email": "john@co.com", "name": "John Doe", "role": "employee", "department": "Engineering" },
    { "row": 3, "email": "jane@co.com", "name": "Jane Smith", "role": "manager", "department": "Engineering" }
  ],
  "message": "File is valid. 50 employee(s) ready to import. Remove dry_run=true to proceed."
}
```

**Error response (HTTP 400):**
```json
{
  "valid": false,
  "total_rows": 50,
  "valid_rows": 47,
  "error_count": 3,
  "errors": [
    { "row_number": 4,  "column": "email",      "value": "notanemail", "message": "Invalid email address." },
    { "row_number": 9,  "column": "role",        "value": "admin",      "message": "Invalid role 'admin'. Must be one of: employee, hr, manager." },
    { "row_number": 12, "column": "first_name",  "value": "",           "message": "First name is required." }
  ],
  "duplicate_emails": ["duplicate@co.com"],
  "message": "Found 3 error(s). Fix them and re-upload."
}
```
Show a table of errors with row numbers so the user knows exactly what to fix.

---

### 3. Run the Import
```
POST /api/employees/import
Authorization: Bearer <token>
Content-Type: multipart/form-data

file: <the .csv or .xlsx file>
dry_run: false   ← or just omit it
```
Returns **immediately** with a `job_id`. Account creation runs in the background.
Frontend does not wait — it starts polling (see API 4).

**Response (HTTP 202):**
```json
{
  "job_id": "abc-123-xyz",
  "status": "pending",
  "total_rows": 50,
  "message": "Import started for 50 employee(s). Poll the URL below for progress.",
  "poll_url": "/api/employees/import/abc-123-xyz"
}
```

---

### 4. Poll for Progress
```
GET /api/employees/import/{job_id}
Authorization: Bearer <token>
```
Poll every **2-3 seconds** while the job is running. Stop when `status == "done"` or `"failed"`.

**Response:**
```json
{
  "job_id": "abc-123-xyz",
  "status": "creating",
  "total_rows": 50,
  "processed": 23,
  "created_count": 21,
  "failed_count": 2,
  "skipped_count": 0,
  "progress_pct": 46.0,
  "results_csv_url": null,
  "created_at": "2026-04-10T10:00:00Z",
  "updated_at": "2026-04-10T10:00:14Z"
}
```

**`status` lifecycle:**
```
pending → creating → done
                   ↘ failed
```

When `status == "done"`:
- Stop polling
- `results_csv_url` will contain a signed download link (valid 7 days)
- Show summary: `created_count` succeeded, `failed_count` failed

---

### 5. Resend Invite Emails
```
POST /api/employees/import/{job_id}/resend-invites
Authorization: Bearer <token>
Content-Type: application/json

{}                              ← resend ALL failed invites
{ "emails": ["x@co.com"] }     ← or target specific ones
```

**Response:**
```json
{
  "resent": 2,
  "failed": 0,
  "details": [
    { "email": "x@co.com", "success": true },
    { "email": "y@co.com", "success": true }
  ]
}
```

---

## Recommended Frontend Flow

```
1. "Download Template" button
        ↓
2. User fills CSV in Excel, saves file
        ↓
3. User clicks "Upload File" → file picker opens
        ↓
4. File selected → POST /import?dry_run=true
   Show: spinner "Validating..."
   ├── Errors found → show error table with row numbers → user fixes and re-uploads
   └── Valid → show preview table ("50 employees ready") + "Confirm Import" button
        ↓
5. User clicks "Confirm Import" → POST /import (dry_run=false)
   Show: progress bar, poll GET /import/{job_id} every 2s
   Update: "23 / 50 created..."
        ↓
6. status == "done"
   Show: "48 created, 2 failed"
   Show: "Download Results" button → results_csv_url
   If failed_count > 0 → show "Resend Invites" button
```

---

## What Employees Receive

Every successfully created employee gets an email with a **one-time secure link**
to set their own password. No temporary passwords are ever sent.

```
Subject: You've been added to Acme Corp on Diltak

Hi John,
Acme Corp has created your account on Diltak — your company's mental wellness platform.

[ Set My Password ]   ← secure Firebase one-time link

This link expires in 72 hours.
```

The link is generated by Firebase and expires after use. If it expires before the
employee clicks it, use the **Resend Invites** endpoint (API 5) to generate a fresh one.

---

## CSV Schema — Share This With Clients

Clients fill in this file. Only the first 4 columns are required.
Point them to `GET /api/employees/import/template` for a pre-filled example file.

| Column | Required | Example | Notes |
|--------|----------|---------|-------|
| `email` | **Yes** | `john@company.com` | Must be unique across the platform |
| `first_name` | **Yes** | `John` | — |
| `last_name` | **Yes** | `Doe` | — |
| `role` | **Yes** | `employee` | Only accepted values: `employee`, `manager`, `hr` |
| `department` | No | `Engineering` | Free text |
| `position` | No | `Backend Developer` | Job title |
| `phone` | No | `+919876543210` | Any format |
| `manager_email` | No | `manager@company.com` | Must exist in same file OR already in the company |
| `hierarchy_level` | No | `3` | Positive integer. Auto-assigned as `manager_level + 1` if blank |

**Accepted file formats:** `.csv`, `.xlsx`
**Maximum file size:** 5 MB
**Maximum employees per upload:** No hard limit (processed in background)

---

## Error Codes

| HTTP Status | Meaning | What to show |
|-------------|---------|--------------|
| `400` | Validation errors in file | Error table with row numbers |
| `401` | Token expired or missing | Redirect to login |
| `403` | User is not employer or HR | "You don't have permission to import employees" |
| `413` | File too large (> 5MB) | "File is too large. Max 5MB." |
| `404` | Job ID not found | "Import job not found" |
| `503` | Database unavailable | "Server error, try again" |
