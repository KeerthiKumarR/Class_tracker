# 🎓 Class Tracker: Automated Lecture Progression & Syllabus Auditor

Class Tracker is an autonomous, privacy-first lecture recorder and syllabus tracking pipeline for macOS. It monitors scheduled classes, requests consent before recording, transcribes audio via Groq Whisper Cloud, audits syllabus coverage against your curriculum catalog, updates your Google Sheet progression (Columns I–M and O–Q), and sends an email recap to the instructor.

---

## 📚 Documentation Index

- **[System Architecture & Design (ARCHITECTURE.md)](file:///Users/keerthikumar/Downloads/class_tracker/ARCHITECTURE.md)** — Comprehensive technical overview, module responsibilities, guardrails, and reliability patterns for code review.
- **[Teacher Quick Start Guide (TEACHER_INSTRUCTIONS.md)](file:///Users/keerthikumar/Downloads/class_tracker/TEACHER_INSTRUCTIONS.md)** — Zero-terminal 1-click DMG installation and daily workflow guide.

---

## ⚡ Quick Start for Teachers (1-Click DMG Installer)

1. Double-click `ClassTracker_Installer.dmg`.
2. Drag **ClassTracker.app** into the **Applications** shortcut.
3. Open **ClassTracker** from Applications or Spotlight.
4. The **🎓 DAA Tracker** icon will appear in your top macOS Menu Bar!

Class Tracker runs silently in the background (0.0% CPU) and automatically prompts you before each scheduled class.

---

## 📅 Configuring Class Timetable (`config/schedule.json`)

Define your weekly lecture timetable in `config/schedule.json` (or sync dynamically via Google Calendar):

```json
[
  {
    "name": "Design & Analysis of Algorithms (DAA)",
    "subject": "DAA",
    "day": "Wednesday",
    "start_time": "15:05",
    "end_time": "15:55",
    "enabled": true
  }
]
```

- Supports recurring weekdays (`"day": "Monday"`, `"Wednesday"`, `"Friday"`, or `"Daily"`).
- Supports specific calendar dates (`"date": "2026-10-06"`).

---

## ⚙️ Configuration (`config/.env`)

Copy `config/.env.example` to `config/.env` and supply:

| Variable | Description |
| :--- | :--- |
| `GROQ_API_KEY` | Groq Cloud API key for fast Whisper (`whisper-large-v3`) and audit models |
| `GOOGLE_SHEET_ID` | The ID of your CourseProgression Google Sheet |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Path to your Google Cloud Service Account JSON file (`config/service_account.json`) |
| `SHEET_TAB_NAME` | Name of the worksheet tab (e.g. `DAA`) |
| `WATCH_FOLDER` | Incoming folder for class recordings (`~/ClassAudio/incoming`) |
| `DELETE_AUDIO_AFTER_PROCESS` | `false` to permanently archive class recordings in `~/ClassAudio/processed` |
| `GMAIL_SENDER` | Gmail address used to dispatch summaries |
| `GMAIL_APP_PASSWORD` | 16-character Gmail App Password |
| `TEACHER_EMAIL` | Instructor email address receiving daily class recaps |

---

## 🔒 Privacy & Reliability Guardrails

- **Explicit Foreground Consent**: The microphone is never activated until the user clicks `[Start Recording]` on the foreground system dialog.
- **Isolated Atomic Recording**: All live audio writes to isolated `.recording` files to eliminate race conditions.
- **Zero-Instruction Protection**: 0% progress or casual opening banter is blocked from mutating spreadsheet rows.
- **Multi-Chunk Deduplication**: 60–90+ minute lectures are speech-compressed and sliced into 10-minute cached segments.

---

## 🗺️ Product Roadmap & Proposals

- **[Feature Proposal: Web Onboarding Portal & Automated Curriculum Ingestion](docs/proposals/FEATURE_WEB_PORTAL_ONBOARDING.md)**: Architecture proposal for zero-recompile universal client pairing, drag-and-drop syllabus PDF ingestion, and multi-tenant teacher workspaces.

