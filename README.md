# 🎓 Class Tracker: Autonomous Lecture Pipeline & Web Onboarding Portal

Class Tracker is an autonomous, privacy-first lecture recorder and syllabus progression tracking system for academic institutions and faculty. It pairs an interactive **Web Management Portal** with a turnkey **macOS Menu Bar Application** that automatically monitors class schedules, records lectures with instructor consent, transcribes audio via Groq Whisper Cloud, audits syllabus coverage against structured curricula, updates Google Sheets in real-time, and dispatches email recaps to instructors.

---

## 🚀 Key Features

### 🌐 1. Self-Serve Web Onboarding Portal
* **4-Step Setup Wizard**:
  * **Step 1 — Course Information**: Register subject name, course code (e.g., `DAA`, `DSA`, `OS`), instructor email, and timetable identifiers.
  * **Step 2 — Google Sheet Linking**: Instant connection and column verification. Intelligently selects subject worksheet tabs while skipping metadata sheets (`README`, `Config`).
  * **Step 3 — Multi-Format Syllabus Parser**: Drag-and-drop syllabus ingestion supporting **PDF**, **DOCX**, **XLSX/Excel**, and **Plain Text**. Extracts units, learning outcomes, and problem lists with deterministic algorithms and Groq AI fallback.
  * **Step 4 — Universal Activation & DMG Download**: Generates a memorable 6-digit pairing code (e.g. `DAA-0183`) and provides a single turnkey macOS DMG download link.

### 💻 2. Turnkey, Generalized macOS Client
* **Zero Hardcoded Endpoints / Bare Distribution**:
  * One universal `ClassTracker_Installer.dmg` for all instructors.
  * The desktop application starts in a completely clean, bare state (`Waiting for Pairing Code...`) with zero pre-loaded subjects or private keys.
* **1-Click Device Pairing**:
  * Instructors activate their courses by entering their 6-digit pairing code or pasting their portal link.
  * Dynamically fetches curriculum units, sheet coordinates, and calendar matching filters.
* **Flexible Hosting Support**:
  * Works out of the box locally (`http://localhost:8080`) or when the Web Portal is deployed to any domain (e.g., `https://tracker.myschool.edu`).
  * Automatically detects URLs, saves portal preferences, and prompts gracefully if servers are unreachable.
* **Menu Bar Controls & Live Stopwatch**:
  * Real-time recording timer and status updates.
  * Dedicated menu item: `📊 Open Google Sheet` (directly opens the course sheet).
  * Dedicated menu item: `🗑️ Disconnect All Paired Courses...` (instantly resets app back to clean bare slate).

### 🤖 3. Autonomous Audio & Syllabus Tracking Pipeline
* **Foreground Instructor Consent**: Displays a native system dialog before recording begins. Never activates microphone silently.
* **Groq Cloud Whisper Transcription**: Converts 60–90+ minute lectures into accurate transcripts using `whisper-large-v3`.
* **Curriculum Alignment**: Matches lecture topics and exercises against parsed course units and updates syllabus progression rows in Google Sheets.
* **Email Recap Delivery**: Dispatches automated class summaries to instructors via secure SMTP with Gmail App Password authentication.

---

## 🛠️ Quick Start Guide

### 1. Launch the Web Onboarding Portal
```bash
# Activate python virtual environment
source venv/bin/activate

# Start the Flask web server
python3 server.py
```
Open **`http://localhost:8080`** in your browser to access the Web Portal.

### 2. Set Up a Course via the Web Portal
1. Navigate to the **Onboarding Wizard** tab.
2. Enter course details, paste your Google Sheet link, and upload your syllabus document (PDF/Word/Excel).
3. Review the extracted topics and click **Save Course & Generate Pairing Code**.
4. Note your 6-digit pairing code (e.g., `DAA-0183`).

### 3. Install & Pair the macOS Client
1. Download **`ClassTracker_Installer.dmg`** directly from the portal (Step 4) or build it locally with `./scripts/build_dmg.sh`.
2. Open the DMG and drag **ClassTracker.app** into `/Applications`.
3. Launch **ClassTracker** from Applications.
4. When prompted, enter your 6-digit code: **`DAA-0183`** (or paste your portal link).
5. The graduation cap (`🎓`) appears in your macOS menu bar, actively monitoring your timetable!

---

## 📁 Repository Structure

```
class_tracker/
├── app_main.py                 # macOS Menu Bar application (rumps / Cocoa)
├── server.py                   # Web Onboarding & Progress Portal (Flask REST API)
├── ClassTracker.spec           # PyInstaller bundle specification for macOS
├── templates/
│   └── index.html              # Modern, glassmorphism web portal UI
├── static/
│   ├── css/style.css           # Premium responsive stylesheet
│   └── js/app.js               # Frontend wizard logic & live dashboard
├── scripts/
│   ├── syllabus_parser.py      # Multi-format document parser (PDF, DOCX, XLSX)
│   ├── profile_manager.py      # Course profiles & pairing code manager
│   ├── calendar_sync.py        # Google Calendar sync engine
│   ├── sheets_client.py        # Google Sheets API client
│   ├── pipeline.py             # Audio transcription & syllabus matcher
│   ├── notify.py               # Email dispatch module (SMTP / Gmail)
│   └── build_dmg.sh            # Production disk image (DMG) packager
├── config/
│   ├── .env.client             # Clean distribution environment template
│   └── service_account.json    # Google Cloud service account credentials
└── docs/
    └── proposals/              # Technical specifications & architectural blueprints
```

---

## 🔒 Security & Privacy Architecture

* **Isolated Client Storage**: User paired profiles and timetable schedules are saved locally to `~/.classtracker/config/` on the instructor's Mac.
* **Zero Secret Leakage**: The client DMG does not bundle private API keys, teacher passwords, or cross-course sheet IDs.
* **Scoped Permissions**: Calendar synchronization strictly queries events matching the instructor's paired course tags (e.g. `DAA`), ignoring personal and unrelated institutional events.
