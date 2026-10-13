# 🚀 Feature Specification: Web Onboarding Portal & Automated Curriculum Ingestion

**Status:** Proposed / Review  
**Author:** Keerthi Kumar R  
**Reviewer:** Aditya Prasad  
**Target Milestone:** v2.0-cloud  

---

## 1. Executive Summary & Problem Statement

Currently, deploying **Class Tracker** to an instructor requires a high-touch, manual workflow:
1. **Manual Curriculum Definition:** Engineering teams must manually construct `config/curriculum.json` by transcribing course outlines from course spreadsheets or syllabus documents.
2. **Hardcoded Environment Builds:** Credentials, course names, and Google Sheet IDs are compiled directly into individual macOS app bundles (`.dmg`), requiring bespoke builds for each instructor.
3. **Cross-Talk Risk:** Sharing service account credentials between development environments and production machines can cause test instances to query production calendars or sheets without strict isolation.

### The Objective
Transform Class Tracker into a **zero-friction SaaS platform** by introducing a **Web Management Portal** that:
- Allows instructors to upload raw syllabus documents (PDF, DOCX, XLSX).
- Automatically parses topics, learning objectives, and problem lists into structured curricula using LLMs.
- Provisions isolated Google Sheets and calendar filters dynamically.
- Pairs with a **single universal macOS client** via a secure 6-digit pairing code or OAuth login, eliminating bespoke per-instructor builds.

---

## 2. High-Level System Architecture

```mermaid
graph TD
    subgraph Web Portal (Cloud)
        A[Instructor Browser] -->|1. Upload Syllabus PDF/DOCX| B[Web API / Dashboard]
        B -->|2. Ingest Document| C[LLM Document Parser]
        C -->|3. Extract Topics & Units| D[(Curriculum Schema DB)]
        B -->|4. Connect Google Sheet| E[Google Sheets API]
        B -->|5. Generate 6-Digit Pairing Code| F[Pairing Token Service]
    end

    subgraph Desktop Application (macOS)
        G[Universal ClassTracker.app] -->|6. Enter Pairing Code| F
        F -->|7. Return Config & Curriculum Payload| G
        G -->|8. Monitor Local Audio & Calendar| H[Microphone & Audio Capture]
        G -->|9. Audit & Sync Progression| E
    end
```

---

## 3. Core Feature Pillars

### Pillar A: Automated Syllabus Ingestion (PDF / DOCX / Sheets)
* **Drag-and-Drop Ingestion:** Instructors drag their syllabus PDF or Excel sheet directly onto the web portal.
* **Intelligent Document Extraction:**
  - Extracts unit numbers, topic hierarchies, target lecture durations, and core problem lists (LeetCode, Codeforces, HackerRank, textbook exercises).
  - Normalizes sub-topic titles into structured JSON compatible with `curriculum.json` schema.
* **Instructor Verification UI:** An interactive review screen allowing instructors to tweak unit boundaries or add custom problem tags before finalizing.

### Pillar B: Universal Desktop Client (Single Build Distribution)
* **Zero Recompilation:** A single, universal `ClassTracker_Installer.dmg` is distributed to all instructors.
* **1-Click Device Pairing:**
  1. Instructor opens `ClassTracker.app` on their Mac for the first time.
  2. A welcome window prompts: *"Enter your 6-digit setup code from classtracker.io"*.
  3. The app authenticates against the web backend and downloads:
     - Target course name and calendar filter keywords.
     - Assigned Google Sheet ID.
     - Custom syllabus JSON.
  4. Config is stored locally in `~/.classtracker/config/` with encryption.

### Pillar C: Multi-Tenant Data Isolation & Security
* **Tenant Isolation:** Each instructor's workspace is strictly isolated. Instructor A’s macOS client cannot read or write to Instructor B’s sheet or calendar.
* **Dynamic Calendar Filtering:** The desktop client only monitors classes matching the instructor’s registered course tags (e.g. `DAA` vs. `DSA`), preventing accidental cross-course recording.
* **Role-Based Access:** Teaching assistants (TAs) or academic directors can be invited to view cohort progression without accessing teacher Mac credentials.

### Pillar D: 1-Click Google OAuth & Teaching Calendar Selector
* **Elimination of Service Accounts:** Completely eliminates manual Google Cloud Service Account creation, JSON keys, and bot email sharing via standard 1-click **"Sign in with Google"** (OAuth 2.0).
* **Smart Calendar Discovery:**
  - Upon authentication, queries the instructor's accessible Google Calendar List (Primary account, university timetables, shared department calendars).
  - Auto-selects the **Primary Calendar** by default (90% of academic faculty use cases).
  - Provides a single-click dropdown selector for institutional sub-calendars (e.g. *MSOT Department Schedule*).
* **Live Schedule Preview:** Displays an immediate visual preview of matched course lectures (e.g., *Wed 10:50 AM DAA*, *Fri 2:25 PM DAA*) with personal events filtered out before the instructor clicks "Confirm".

### Pillar E: Zero-Secret Desktop Architecture (Server-Side Proxy)
* **No Client-Side Keys:** The macOS desktop app never bundles or stores Google Service Account private keys, eliminating reverse-engineering risks.
* **Secure Web Proxying:** The desktop client performs local audio capture and transcription, then sends structured session results via HTTPS to the Web API. The cloud backend executes the authenticated Google Sheets updates and email dispatches.

---

## 4. API Contract & Data Models

### 1. Ingest Syllabus Document
* **Endpoint:** `POST /api/v1/curriculum/parse`
* **Payload:** `multipart/form-data` with `file: syllabus.pdf`, `course_code: "DAA-2026"`
* **Response:**
```json
{
  "course_id": "crs_982341",
  "subject": "Design & Analysis of Algorithms",
  "units_count": 22,
  "curriculum": [
    {
      "unit_id": "DAA-01",
      "topic": "Introduction & Asymptotic Notations",
      "hours": 1.5,
      "classwork": ["Big-O proofs", "LC1 Two Sum"],
      "homework": ["Order of growth analysis"]
    }
  ]
}
```

### 2. Desktop Client Device Pairing
* **Endpoint:** `POST /api/v1/device/pair`
* **Payload:**
```json
{
  "pairing_code": "481-902",
  "device_name": "Aditya-MacBook-Pro",
  "os_version": "macOS 15.1"
}
```
* **Response:**
```json
{
  "auth_token": "ct_sec_9918231...",
  "course_id": "crs_982341",
  "course_name": "DAA",
  "sheet_id": "1eGBmCNgfjwABNwVQEt_HJfHpT_kQIttR6QGec_IbLJA",
  "calendar_filter": ["DAA", "Design & Analysis of Algorithm"],
  "curriculum_sync_url": "/api/v1/curriculum/crs_982341.json"
}
```

---

## 5. Phased Rollout Plan

| Phase | Scope | Deliverables |
| :--- | :--- | :--- |
| **Phase 1** | Client Decoupling & Config API | Update desktop client to fetch runtime configuration and curriculum from remote endpoint or local JSON override instead of hardcoded `.env`. |
| **Phase 2** | Web Ingestion Prototype | Lightweight web interface (Next.js/FastAPI) supporting syllabus PDF upload and Groq/Qwen-powered curriculum JSON extraction. |
| **Phase 3** | Pairing Service & Cloud Sync | Implement pairing token exchange, user authentication (Google OAuth), and one-click cloud sheet linking. |
| **Phase 4** | Web Dashboard & Cohort Analytics | Instructor dashboard displaying live syllabus completion progress bars, recent class transcripts, and student assignments. |
