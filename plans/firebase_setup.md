# Firebase Setup Guide — Diltak / Uma

This guide walks you through setting up Firebase from zero for this project.
By the end you will have Authentication, Firestore, and Storage working,
with all the collections and indexes this project needs.

---

## What Firebase does in this project

| Firebase Service | What it's used for |
|------------------|--------------------|
| **Authentication** | Login tokens — every API request is verified against Firebase Auth |
| **Firestore** | The main database — users, companies, sessions, reports, memories, etc. |
| **Storage** | Stores bulk import result CSVs |

---

## Step 1 — Create a Firebase Project

1. Go to **https://console.firebase.google.com**
2. Click **"Add project"**
3. Name it: `mindtest-94298` (or whatever you want — just keep it consistent)
4. Disable Google Analytics if you don't need it (saves setup time)
5. Click **"Create project"** → wait ~30 seconds

You now have a Firebase project.

---

## Step 2 — Enable Authentication

This lets you create user accounts and generate login tokens.

1. In the left sidebar click **"Build" → "Authentication"**
2. Click **"Get started"**
3. Under **"Sign-in method"** tab, click **"Email/Password"**
4. Toggle **"Email/Password"** to **Enabled**
5. Leave "Email link (passwordless)" OFF for now
6. Click **"Save"**

That's it. The project uses `firebase_admin` on the backend — it creates users, verifies tokens, and generates invite links. You don't need to configure anything else here.

---

## Step 3 — Create Firestore Database

Firestore is the main database. Think of it like a JSON document store — no fixed schema, very flexible.

1. In the left sidebar click **"Build" → "Firestore Database"**
2. Click **"Create database"**
3. Choose **"Start in production mode"** (we'll set up proper security rules in Step 6)
4. Choose a region — pick the one closest to your users:
   - India → `asia-south1 (Mumbai)`
   - US → `us-central1`
5. Click **"Enable"**

Firestore is now live. It starts empty — the app creates documents automatically as it runs.

---

## Step 4 — Enable Firebase Storage

Storage holds files — in this project specifically the bulk import result CSVs.

1. In the left sidebar click **"Build" → "Storage"**
2. Click **"Get started"**
3. Choose **"Start in production mode"**
4. Pick the same region as your Firestore (important — keep them in the same region)
5. Click **"Done"**

Note the bucket name shown at the top — it looks like:
```
mindtest-94298.firebasestorage.app
```
Copy this — you'll need it for the `FIREBASE_STORAGE_BUCKET` env var.

---

## Step 5 — Get the Admin SDK Credentials (Service Account Key)

The backend uses **Firebase Admin SDK** to talk to Firebase with full admin privileges
(create users, write to Firestore without auth checks, etc.). For this it needs a
**service account key** — a JSON file that acts like a password for the server.

> ⚠️ This file is secret. Never commit it to git. It's already in `.gitignore` as `firebaseadmn.json`.

1. In Firebase Console, click the **gear icon ⚙️** next to "Project Overview" → **"Project settings"**
2. Click the **"Service accounts"** tab
3. Make sure **"Firebase Admin SDK"** is selected
4. Click **"Generate new private key"**
5. Click **"Generate key"** in the confirmation dialog
6. A JSON file downloads automatically — rename it to `firebaseadmn.json`
7. Move it into the project root: `C:\Users\anime\OneDrive\Desktop\humanlikeAI\firebaseadmn.json`

The file looks like this (example — your values will be different):
```json
{
  "type": "service_account",
  "project_id": "mindtest-94298",
  "private_key_id": "abc123...",
  "private_key": "-----BEGIN RSA PRIVATE KEY-----\n...\n-----END RSA PRIVATE KEY-----\n",
  "client_email": "firebase-adminsdk-xyz@mindtest-94298.iam.gserviceaccount.com",
  "client_id": "...",
  "auth_uri": "https://accounts.google.com/o/oauth2/auth",
  "token_uri": "https://oauth2.googleapis.com/token"
}
```

---

## Step 6 — Set Environment Variables

Open (or create) the `.env` file in the project root and add:

```env
# Firebase
FIREBASE_CREDENTIALS_PATH=firebaseadmn.json
FIREBASE_PROJECT_ID=mindtest-94298
FIREBASE_STORAGE_BUCKET=mindtest-94298.firebasestorage.app
```

Replace `mindtest-94298` with your actual project ID if it's different.
You can find your project ID in Firebase Console → Project settings → General tab.

---

## Step 7 — Create Firestore Indexes

Firestore requires **composite indexes** for any query that filters on more than one field
OR that combines a filter with an `order_by`. Without these indexes the queries will fail
with an error like: *"The query requires an index"*.

Go to **Firebase Console → Firestore → Indexes → Composite** and create the following.

> Tip: When a query fails in the logs it prints a direct link to create the required index.
> You can also create them manually using the table below.

### Indexes to create

Click **"Add index"** for each row:

| Collection | Fields | Query scope |
|------------|--------|-------------|
| `users` | `company_id` ASC, `role` ASC | Collection |
| `users` | `company_id` ASC, `is_active` ASC | Collection |
| `users` | `company_id` ASC, `department` ASC | Collection |
| `check_ins` | `company_id` ASC, `created_at` ASC | Collection |
| `check_ins` | `user_id` ASC, `created_at` ASC | Collection |
| `sessions` | `company_id` ASC, `created_at` ASC | Collection |
| `sessions` | `user_id` ASC, `created_at` ASC | Collection |
| `wellness_events` | `company_id` ASC, `created_at` ASC | Collection |
| `wellness_events` | `company_id` ASC, `event_type` ASC | Collection |
| `mental_health_reports` | `company_id` ASC, `created_at` ASC | Collection |
| `mental_health_reports` | `employee_id` ASC, `created_at` ASC | Collection |
| `escalation_tickets` | `company_id` ASC, `created_at` ASC | Collection |
| `import_jobs` | `company_id` ASC, `created_at` ASC | Collection |
| `community_posts` | `company_id` ASC, `is_approved` ASC, `created_at` ASC | Collection |
| `community_replies` | `post_id` ASC, `is_approved` ASC | Collection |

**How to add each one:**
1. Click **"Add index"**
2. Enter the **Collection ID** (e.g. `users`)
3. Click **"Add field"** for each field, set the direction (ASC = Ascending)
4. Set Query scope to **"Collection"**
5. Click **"Create index"**
6. Wait — indexes take 1-5 minutes to build (status shows "Building...")

---

## Step 8 — Set Firestore Security Rules

Security rules control who can read/write from the frontend (mobile app, web app).
The backend bypasses these entirely because it uses the Admin SDK.
These rules protect direct database access from clients.

1. Go to **Firebase Console → Firestore → Rules**
2. Replace the content with the following:

```javascript
rules_version = '2';
service cloud.firestore {
  match /databases/{database}/documents {

    // ── Helpers ──────────────────────────────────────────────────────────────

    function isSignedIn() {
      return request.auth != null;
    }

    function uid() {
      return request.auth.uid;
    }

    function getUserData() {
      return get(/databases/$(database)/documents/users/$(uid())).data;
    }

    function isEmployerOrHR() {
      return getUserData().role in ['employer', 'hr'];
    }

    function sameCompany(company_id) {
      return getUserData().company_id == company_id;
    }

    // ── Users ─────────────────────────────────────────────────────────────────
    // Users can read their own profile.
    // Employers/HR can read all profiles in their company.
    // Only the backend (Admin SDK) can write.
    match /users/{userId} {
      allow read: if isSignedIn() && (
        uid() == userId ||
        (isEmployerOrHR() && sameCompany(resource.data.company_id))
      );
      allow write: if false; // Backend only
    }

    // ── Companies ─────────────────────────────────────────────────────────────
    match /companies/{companyId} {
      allow read: if isSignedIn() && sameCompany(companyId);
      allow write: if false; // Backend only
    }

    // ── Check-ins ─────────────────────────────────────────────────────────────
    // Users write their own check-ins. Employers/HR read all in company.
    match /check_ins/{docId} {
      allow read: if isSignedIn() && (
        resource.data.user_id == uid() ||
        (isEmployerOrHR() && sameCompany(resource.data.company_id))
      );
      allow create: if isSignedIn() && request.resource.data.user_id == uid();
      allow update, delete: if false; // Backend only
    }

    // ── Sessions (Uma chat sessions) ──────────────────────────────────────────
    match /sessions/{sessionId} {
      allow read, write: if isSignedIn() && resource.data.user_id == uid();
    }

    // ── Mental health reports ─────────────────────────────────────────────────
    // Users read their own. Employers/HR read all in company.
    match /mental_health_reports/{reportId} {
      allow read: if isSignedIn() && (
        resource.data.employee_id == uid() ||
        (isEmployerOrHR() && sameCompany(resource.data.company_id))
      );
      allow write: if false; // Backend only
    }

    // ── Import jobs ───────────────────────────────────────────────────────────
    match /import_jobs/{jobId} {
      allow read: if isSignedIn() && isEmployerOrHR() &&
        sameCompany(resource.data.company_id);
      allow write: if false; // Backend only
    }

    // ── Uma sessions (persistent chat memory) ────────────────────────────────
    match /uma_sessions/{sessionId} {
      allow read, write: if isSignedIn() && resource.data.user_id == uid();
    }

    // ── User memories (Uma long-term memory) ─────────────────────────────────
    match /user_memories/{userId} {
      allow read: if isSignedIn() && uid() == userId;
      allow write: if false; // Backend only
    }

    // ── Community posts ───────────────────────────────────────────────────────
    match /community_posts/{postId} {
      allow read: if isSignedIn();
      allow create: if isSignedIn();
      allow update, delete: if false; // Backend only
    }

    match /community_replies/{replyId} {
      allow read: if isSignedIn();
      allow create: if isSignedIn();
      allow update, delete: if false; // Backend only
    }

    // ── Gamification ─────────────────────────────────────────────────────────
    match /user_gamification/{userId} {
      allow read: if isSignedIn();
      allow write: if false; // Backend only
    }

    match /wellness_challenges/{challengeId} {
      allow read: if isSignedIn();
      allow write: if false; // Backend only
    }

    // ── Escalation tickets ────────────────────────────────────────────────────
    match /escalation_tickets/{ticketId} {
      allow read: if isSignedIn() && isEmployerOrHR() &&
        sameCompany(resource.data.company_id);
      allow write: if false; // Backend only
    }

    // ── Invites (employee invite tokens) ─────────────────────────────────────
    match /invites/{token} {
      allow read: if true;   // needed for accept-invite flow (unauthenticated)
      allow write: if false; // Backend only
    }

    // ── Voice calls ───────────────────────────────────────────────────────────
    match /calls/{callId} {
      allow read: if isSignedIn() && (
        resource.data.caller_id == uid() || resource.data.receiver_id == uid()
      );
      allow write: if false; // Backend only
    }

    match /callSessions/{sessionId} {
      allow read: if isSignedIn();
      allow write: if false; // Backend only
    }

    // ── Catch-all: deny everything else ──────────────────────────────────────
    match /{document=**} {
      allow read, write: if false;
    }
  }
}
```

3. Click **"Publish"**

---

## Step 9 — Set Firebase Storage Rules

1. Go to **Firebase Console → Storage → Rules**
2. Replace the content with:

```javascript
rules_version = '2';
service firebase.storage {
  match /b/{bucket}/o {

    // Import results — only employer/hr can download their company's results
    // (actual enforcement is done via signed URLs generated by Admin SDK)
    match /import_results/{companyId}/{fileName} {
      allow read: if request.auth != null;
      allow write: if false; // Backend only via Admin SDK
    }

    // Deny everything else
    match /{allPaths=**} {
      allow read, write: if false;
    }
  }
}
```

3. Click **"Publish"**

---

## Step 10 — Complete Collections Reference

These are all the Firestore collections this project reads or writes.
You don't need to create them manually — Firestore creates a collection
the first time you write a document to it. This is just for your reference.

| Collection | Created by | Purpose |
|------------|-----------|---------|
| `users` | Auth register / employee create | All user profiles (employers, managers, HR, employees) |
| `companies` | Auth register | Company profiles and settings |
| `check_ins` | Employee app | Daily mood/stress check-ins |
| `sessions` | Chat app | Uma chat session records |
| `mental_health_reports` | Session end | Generated wellness reports |
| `escalation_tickets` | HR / reports | Escalated mental health concerns |
| `import_jobs` | Bulk import API | Async import job tracking |
| `uma_sessions` | Sprint 4 (future) | Persistent Uma chat sessions |
| `user_memories` | Sprint 2 (future) | Uma's long-term memory per user |
| `invites` | Sprint 8.5 (future) | Employee invite tokens |
| `community_posts` | Community feature | Anonymous community posts |
| `community_replies` | Community feature | Replies to community posts |
| `user_gamification` | Gamification | Points, streaks, badges |
| `wellness_challenges` | Admin | Company wellness programs |
| `ai_recommendations` | Recommendations | AI-generated wellness recommendations |
| `chat_sessions` | Recommendations | Chat session records for recommendations |
| `calls` | Voice calls | Voice call records |
| `callSessions` | Voice calls | Voice call session details |
| `interventions` | Employer org | Manager interventions |
| `anonymous_profiles` | Community | Anonymous community profiles |

---

## Step 11 — Test the Connection

Once your `.env` file has `FIREBASE_CREDENTIALS_PATH=firebaseadmn.json`, start the server:

```bash
uvicorn main:app --reload
```

Look for these lines in the terminal:
```
Firebase initialized with admin credentials from firebaseadmn.json
Connecting to Pinecone index: 'uma-rag'...
```

If you see:
```
WARNING: FIREBASE_CREDENTIALS_PATH not set or file not found.
```
→ Check that `firebaseadmn.json` is in the project root and the path in `.env` is correct.

Then hit the health endpoint:
```
GET http://127.0.0.1:8000/health
```
It should return:
```json
{
  "status": "ok",
  "api_key_set": true,
  "rag_chunks": 0
}
```

---

## Step 12 — Test Authentication Flow

To verify auth is working end-to-end:

1. **Register an employer account** via:
   ```
   POST http://127.0.0.1:8000/api/auth/register
   {
     "email": "test@company.com",
     "password": "Test1234!",
     "company_name": "Test Company",
     "firstName": "Admin",
     "lastName": "User"
   }
   ```
   This creates a Firebase Auth user + a Firestore `users` document + a `companies` document.

2. **Login** — Firebase Auth login happens on the frontend using the Firebase SDK.
   The frontend gets back a JWT `idToken`. For testing via Swagger you can get a token
   using the Firebase REST API:
   ```
   POST https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key=YOUR_WEB_API_KEY
   {
     "email": "test@company.com",
     "password": "Test1234!",
     "returnSecureToken": true
   }
   ```
   Copy the `idToken` from the response.

3. **Use the token** — in Swagger UI (`http://127.0.0.1:8000/docs`), click
   **"Authorize"** (lock icon top right), paste: `Bearer <your idToken>`.
   Now all endpoints that require auth will work.

> **Where is the Web API Key?**  
> Firebase Console → Project settings → General → "Web API key"

---

## Common Errors and Fixes

| Error | Cause | Fix |
|-------|-------|-----|
| `Failed to initialize Firebase` | `firebaseadmn.json` missing or wrong path | Check the file exists at the path in `FIREBASE_CREDENTIALS_PATH` |
| `The query requires an index` | Missing composite index | Click the link in the error message — it opens the index creation page pre-filled |
| `PERMISSION_DENIED` | Security rules blocking a write | Temporarily use test mode rules, or check which rule is blocking |
| `Invalid or expired authentication token` | Token expired (they last 1 hour) | Get a fresh token via sign-in |
| `UserNotFoundError` | `fb_auth.get_user_by_email()` on nonexistent email | Expected behaviour — the code handles this, it means the email is new |
| `Email already exists` | Creating a Firebase Auth user with a taken email | Check before creating, or catch the error (already done in `users.py`) |

---

## Security Checklist

Before going to production:

- [ ] `firebaseadmn.json` is in `.gitignore` (it already is — check with `git status`)
- [ ] `.env` is in `.gitignore` (it should be — add it if not)
- [ ] Firestore security rules are published (Step 8)
- [ ] Storage security rules are published (Step 9)
- [ ] `K_ANON_THRESHOLD` is set to `5` in `employer_dashboard.py` (Sprint 1, Task 1.1)
- [ ] No hardcoded API keys in any source file (Sprint 1, Task 1.3)
- [ ] Firebase client config values moved to env vars
