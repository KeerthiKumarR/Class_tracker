# 🎓 Class Tracker: Quick Start Guide for Teachers

Welcome to **Class Tracker**! This tool automatically records your scheduled class lectures, audits curriculum progression, updates your Google Sheet, and emails you a summary of topics covered.

---

## 🚀 3-Minute Setup (Done Only Once)

### Step 1: Share Your Google Sheet (and Calendar)
1. Open your **Course Progression Google Sheet** in your browser $\rightarrow$ Click **Share**.
2. Add the tracker bot email with **Editor** permissions:
   `class-tracker-bot@class-tracker-507704.iam.gserviceaccount.com`
3. Click **Send** / **Done**.

*(Optional)* **Google Calendar Auto-Sync**: If you keep your class schedule on Google Calendar, open Google Calendar $\rightarrow$ Settings $\rightarrow$ Share your class calendar with the same bot email. Class Tracker will automatically sync all scheduled lectures from your calendar!

---

### Step 2: Install Class Tracker (1-Click DMG Installer)
1. Double-click the installer:
   ```
   💿 ClassTracker_Installer.dmg
   ```
2. Drag **ClassTracker.app** into the **Applications** folder shortcut.
3. Open **ClassTracker** from your Applications or Spotlight search.
4. You will see the **🎓 DAA Tracker** icon appear in your top macOS Menu Bar!

That's it! Setup is 100% complete. You **never need to open Terminal or write any commands**.

---

## 🎙️ Daily Class Workflow

You don't need to do anything to start the app — it runs silently in the background (0% CPU).

1. **Before Class Starts**:
   A prompt dialog will automatically appear on your screen showing your actual scheduled class time:
   > *"🔔 [Class Name] is scheduled now ([Start Time] - [End Time]).*
   > *Start recording class lecture?"*
   >
   > `[Cancel / Skip]`  `[Start Recording]`

2. **Click `[Start Recording]`**:
   - The microphone will begin recording your lecture audio.

3. **After Class Ends**:
   - Recording stops **automatically** at the scheduled end time.
   - The system transcribes the lecture, identifies the syllabus unit, calculates topic completion %, updates your Google Sheet, and sends a summary recap to your email.

---

## 📅 Setting Your Timetable (`config/schedule.json`)

Your weekly schedule is configured in `config/schedule.json`. You can list as many classes and days as you teach:

```json
[
  {
    "name": "Design & Analysis of Algorithms (DAA)",
    "subject": "DAA",
    "day": "Wednesday",
    "start_time": "14:25",
    "end_time": "15:55",
    "enabled": true
  }
]
```
*(Supports days like `"Monday"`, `"Wednesday"`, `"Friday"`, or `"Daily"`)*

---

## 🔒 Privacy Guarantee
- **Zero Background Listening**: The microphone is strictly off until you click `[Start Recording]`.
- **Local Storage**: All recordings are stored privately on your Mac in `~/ClassAudio/processed/`.
