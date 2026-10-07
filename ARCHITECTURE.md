# 🏛️ Class Tracker: Technical Architecture & System Design

This document provides a comprehensive technical overview of the **Class Tracker** codebase for engineering and academic review.

---

## 1. System Overview

Class Tracker is an autonomous, privacy-first macOS application designed to track, audit, and document university lecture progression against a structured syllabus.

### High-Level Architecture Flow

```
[ Google Calendar / Timetable ]
               │
               ▼
[ Native Menu Bar App (app_main.py) ] ── (Foreground Consent Dialog)
               │
               ▼
   [ Audio Capture (FFmpeg) ] ── (Atomic .recording File)
               │
               ▼
[ Audio Preprocessor & Slicer ] ── (16kHz Mono MP3, 10-min Chunks)
               │
               ▼
[ Cloud Speech-to-Text (Groq Whisper) ]
               │
               ▼
   [ Curriculum Classifier (unit_matcher.py) ] ── (Rule 0: Recap Isolation)
               │
               ▼
  [ Syllabus Progression Auditor (match_topic.py) ]
               │
         ┌─────┴────────────────────────┐
         ▼                              ▼
[ Google Sheets Updater ]      [ SMTP Notification Engine ]
 (CourseProgression Tab)         (Daily Email Summary)
```

---

## 2. Core Modules & Responsibilities

| Module | File | Primary Responsibility |
| :--- | :--- | :--- |
| **Menu Bar GUI** | `app_main.py` | macOS native status bar UI via `rumps` and `Main NSRunLoop`. Handles timers, manual triggers, and countdowns. |
| **Scheduler Engine** | `scripts/scheduler.py` | Timetable tracking, microphone consent dialogs (`AppleScript`), and atomic file recovery. |
| **Pipeline Coordinator**| `scripts/pipeline.py` | Central controller orchestrating transcription, matching, auditing, and updates. |
| **Audio Transcription** | `scripts/transcribe.py` | 16kHz mono speech compression, multi-segment chunking, cache management, and Groq Whisper client. |
| **Unit Classifier** | `scripts/unit_matcher.py` | Matches transcript to syllabus units; identifies recap vs. new instruction (Rule 0). |
| **Topic Auditor** | `scripts/match_topic.py` | Analyzes syllabus coverage %, solved classwork (LeetCode/GFG), homework, and generates academic notes. |
| **Sheets Client** | `scripts/sheets_client.py`| Wrapper around `gspread` with automatic exponential backoff, retry loops, and column mapping. |
| **Calendar Sync** | `scripts/calendar_sync.py`| Service Account Google Calendar integration with automatic deletion/cancellation detection. |
| **Notification Engine** | `scripts/notify.py` | Sends formatted HTML summaries to instructors and diagnostic alerts to developers. |
| **Curriculum Loader** | `scripts/curriculum.py` | Validates and loads curriculum JSON schema (`config/curriculum.json`). |

---

## 3. Key Engineering Guardrails & Reliability Patterns

### A. Atomic Recording & Collision Prevention
- **Isolated `.recording` Files**: During active recording, audio is captured to an isolated `.recording` file.
- **Lock Check (`lsof`)**: `is_file_locked_or_recording()` inspects active system file descriptors and file growth over time before any worker can touch an audio file.
- **Race Condition Immunity**: Background sweeps explicitly ignore `.recording` files and pause execution while `is_recording` is active.

### B. Multi-Chunk Transcription with Deduplication Cache
- Long recordings (up to 2+ hours) are automatically sliced into **10-minute speech-optimized chunks** (~1.8MB each).
- Chunks are transcribed with exponential backoff and cached locally in `~/.classtracker_cache` using MD5 checksums. If network interrupts occur, previously transcribed segments are restored instantly without re-billing.

### C. Zero-Instruction & Short-Fragment Protection
- **Word Count Threshold**: Audio files yielding fewer than 150 words (e.g. mic tests, accidental clicks) are safely bypassed.
- **Zero-Coverage Lock**: If a recording yields `0%` syllabus progress and no assigned classwork/homework (e.g. introductory setup or non-lecture chatter), the system **blocks spreadsheet writes completely** to protect existing curriculum progress.

### D. Academic Professionalism Filter
- Non-academic remarks, background noise, or opening chatter are filtered out.
- The auditor adheres to strict university-grade summaries without including casual conversational slang in official records.

### E. Multi-Unit Split Class Support
- When a lecture begins with unfinished topics from a previous unit before moving to a new unit, the system automatically audits both units, marking the prior unit complete while logging the new unit's initial progress.

---

## 4. Security & Privacy Model

1. **Explicit Foreground Consent**: The microphone is never activated passively. An AppleScript dialog explicitly requests permission before recording begins.
2. **Zero Cloud Audio Storage**: Audio files are stored locally in `~/ClassAudio/processed/` and processed in-memory over TLS 1.3 encrypted cloud endpoints.
3. **No Root Privileges**: The standalone application runs entirely within standard user permissions (`0.0%` idle CPU, zero `sudo` requirements).
