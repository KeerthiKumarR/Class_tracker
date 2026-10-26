# 🚀 Feature Specification: Web Onboarding Portal & Automated Curriculum Ingestion

**Status:** Implemented & Production-Ready  
**Author:** Keerthi Kumar R  
**Reviewer:** Aditya Prasad  
**Target Milestone:** v2.0-cloud  

---

## 1. Executive Summary & Problem Statement

Previously, deploying **Class Tracker** required a manual, high-touch workflow:
1. **Manual Curriculum Definition:** Engineering teams had to transcribe course outlines by hand into static JSON catalogs.
2. **Hardcoded Environment Builds:** Credentials, course names, and Google Sheet IDs were compiled directly into individual macOS app bundles (`.dmg`), requiring bespoke builds for each instructor.
3. **Cross-Talk Risk:** Sharing service account credentials between development environments and production machines risked test instances querying production calendars or sheets without strict isolation.

### The Delivered Solution
Class Tracker has been transformed into a **self-serve, zero-friction academic platform** featuring:
- An intuitive **Web Management Portal** allowing instructors to upload course syllabi in multiple formats (PDF, DOCX, XLSX, TXT).
- An automated document parser that extracts structured units, learning outcomes, and problem lists.
- A **universal, bare macOS client** packaged into a single turnkey DMG that activates dynamically via a 6-digit pairing code or direct portal URL.
- Zero hardcoded spreadsheet IDs or endpoints: complete isolation across courses and instructors.

---

## 2. High-Level System Architecture

```mermaid
graph TD
    subgraph Web Portal (Cloud / Host)
        A[Instructor Browser] -->|1. Upload Syllabus PDF/DOCX/XLSX| B[Web API / Dashboard]
        B -->|2. Ingest Document| C[Syllabus Parser Engine]
        C -->|3. Extract Topics & Units| D[(Course Profile Store)]
        B -->|4. Verify & Link Sheet| E[Google Sheets API]
        B -->|5. Generate 6-Digit Pairing Code| F[Pairing Token Service]
    end

    subgraph Desktop Application (macOS Client)
        G[Universal ClassTracker.app] -->|6. Enter Pairing Code or URL| F
        F -->|7. Return Course Config & Units| G
        G -->|8. Monitor Local Audio & Calendar| H[Microphone & Audio Capture]
        G -->|9. Audit & Sync Progression| E
    end
```

---

## 3. Implemented Core Pillars

### Pillar A: Automated Multi-Format Syllabus Ingestion
* **Multi-Format Support:** Ingests **PDF**, **DOCX**, **XLSX / Excel**, and **Plain Text** documents.
* **Deterministic + AI Extraction:** Employs high-speed deterministic pattern matching for tabular syllabi with Groq Qwen/Whisper LLM fallback for unstructured narrative documents.
* **Smart Sheet Tab Discovery:** Auto-selects the subject's relevant worksheet tab while systematically filtering out metadata tabs (`README`, `Config`, `Dashboard`).

### Pillar B: Universal Desktop Client (Single DMG Distribution)
* **Zero Recompilation:** One single universal `ClassTracker_Installer.dmg` serves all instructors.
* **Clean Bare Slate:** Fresh installations start with 0 paired subjects (`Waiting for Pairing Code...`) and zero hardcoded spreadsheet IDs.
* **1-Click Device Pairing:**
  1. Instructor opens `ClassTracker.app` on their Mac.
  2. Prompts: *"Enter your 6-digit Course Pairing Code (e.g. DAA-0183) or paste your Web Portal activation link"*.
  3. Supports local development (`http://localhost:8080`) and remote production domains (`https://tracker.myschool.edu`).
* **Disconnect / Reset:** Menu item `🗑️ Disconnect All Paired Courses...` allows instant reset to a clean state.

### Pillar C: Data Isolation & Calendar Precision
* **Tenant Isolation:** User configuration and paired schedules are persisted strictly in `~/.classtracker/config/`.
* **Dynamic Calendar Filtering:** Synchronization queries only events matching the instructor’s registered course tags (e.g. `DAA` vs. `DSA`), filtering out personal and unrelated entries.

### Pillar D: Live Course Progression Dashboard
* **Google Sheets Real-Time Sync:** Reads live progress metrics directly from linked Google Sheets.
* **Visual Metric Cards:** Real-time visibility into units covered, completion percentages, hours taught, and remaining topics.

### Pillar E: Reliable Email Notifications
* **Gmail SMTP Integration:** Sends structured lecture recaps to instructors using 16-character Google App Passwords.
* **Connection Tester:** Dedicated web settings panel with live test email verification.

---

## 4. API Endpoints Reference

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `POST` | `/api/verify-sheet` | Verifies Google Sheet access and suggests relevant subject tab |
| `POST` | `/api/parse-syllabus` | Extracts structured curriculum units from uploaded document |
| `POST` | `/api/save-course` | Stores course profile, creates curriculum catalog, generates 6-digit code |
| `GET` | `/api/pair/<code>` | Returns configuration and units payload for desktop app pairing |
| `GET` | `/api/course/<id>/progress` | Live-reads syllabus completion metrics from Google Sheets |
| `GET` | `/api/download-dmg` | Serves universal `ClassTracker_Installer.dmg` |
| `POST` | `/api/settings/email` | Configures and tests instructor email notifications |

---

## 5. Reviewer Alignment & Feedback Addressed (Aditya Prasad)

1. **Elimination of Per-User Builds:** Fully resolved by moving from hardcoded compile-time configuration to dynamic token-based pairing.
2. **Document Variety:** Faculty syllabi provided as Word (`.docx`), Excel (`.xlsx`), or PDF documents are all parsed out-of-the-box.
3. **Menu Bar Reliability:** Cocoa Main NSRunLoop timer integration prevents status item freezes during background calendar synchronization.
4. **Generalization for Cloud Deployment:** All hardcoded localhost endpoints replaced with dynamic URL detection and fallback prompts.
