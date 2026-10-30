"""Cloud Deployment & Web Management Portal for Class Tracker.

Endpoints:
- GET /             : Serves the Web Onboarding Portal and Syllabus Dashboard.
- GET /health       : Verifies Groq API, Google Sheets connection, and curriculum.json.
- GET /healthz      : Lightweight liveness check for Render.
- POST /upload      : Multipart audio file upload -> full processing pipeline.
- POST /retry       : Reprocesses a failed file from /tmp/failed/.
- GET /logs         : Returns the last 50 processing events as JSON.

Web Portal APIs:
- POST /api/verify-sheet       : Validates Google Sheet URL, returns worksheets and column health.
- POST /api/parse-syllabus     : Ingests PDF/DOCX/XLSX/text syllabus via Groq LLM -> structured units.
- POST /api/save-course        : Saves course profile and generates 6-digit pairing code.
- GET  /api/courses            : Lists all configured course profiles.
- GET  /api/course/<cid>       : Retrieves full details of a specific course.
- DELETE /api/course/<cid>     : Removes a course profile.
- GET  /api/pair/<code>        : Universal Mac App pairing endpoint.
- GET  /api/course/<cid>/progress : Live reads Google Sheet syllabus progression.
- GET  /api/download-dmg       : Serves the universal ClassTracker_Installer.dmg.
- GET  /api/download-config/<cid> : Downloads custom .env configuration file.
"""

import os
import sys
import time
import threading
import urllib.request
import glob
import json
import re
import shutil
import tempfile
from collections import deque
from datetime import datetime
from typing import Dict, Any, Optional, List

from flask import Flask, request, jsonify, render_template, send_file, Response
from dotenv import dotenv_values, load_dotenv

# Ensure scripts directory is importable
HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.join(HERE, "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from curriculum import load_curriculum
from sheets_client import SheetClient
from pipeline import process_file
from notify import send_developer_alert
from groq import Groq
import gspread
from google.oauth2.service_account import Credentials

from syllabus_parser import extract_text_from_file, parse_syllabus_to_curriculum
from profile_manager import (
    get_all_courses,
    get_course_by_id,
    get_course_by_pairing_code,
    save_course,
    delete_course,
)

# Load environment variables
load_dotenv(os.path.join(HERE, "config", ".env"))

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = 150 * 1024 * 1024  # 150MB upload limit

# In-memory circular buffer for last 50 processing events
EVENT_LOGS = deque(maxlen=50)

FAILED_DIR = "/tmp/failed"
os.makedirs(FAILED_DIR, exist_ok=True)


def _start_keepalive_worker():
    """Background worker that pings the public endpoint every 9 minutes to keep Render awake."""
    def _ping_loop():
        time.sleep(30)  # Brief warm-up delay after server boot
        portal_url = os.environ.get("PORTAL_URL", "https://class-tracker-pgo5.onrender.com")
        health_url = f"{portal_url.rstrip('/')}/healthz"
        while True:
            try:
                req = urllib.request.Request(health_url, headers={"User-Agent": "ClassTracker-KeepAlive/1.0"})
                with urllib.request.urlopen(req, timeout=15) as resp:
                    if resp.status == 200:
                        print(f"[KEEPALIVE] Periodic ping succeeded: {health_url} (status {resp.status})")
            except Exception as e:
                print(f"[KEEPALIVE] Periodic ping note: {e}")
            time.sleep(540)  # Ping every 9 minutes (Render free idle limit is 15 minutes)

    t = threading.Thread(target=_ping_loop, daemon=True)
    t.start()


# Launch keepalive worker on cloud startup
_start_keepalive_worker()


def get_app_config() -> Dict[str, Any]:
    """Combines .env file values, user ~/.classtracker/config/.env, and process environment variables."""
    cfg = {}
    for cand in [os.path.join(HERE, "config", ".env"), os.path.join(HERE, "config", ".env.client")]:
        if os.path.exists(cand):
            cfg.update(dotenv_values(cand))
            break
    user_env = os.path.expanduser("~/.classtracker/config/.env")
    if os.path.exists(user_env):
        cfg.update(dotenv_values(user_env))
    for k, v in os.environ.items():
        if v is not None:
            cfg[k] = v
    return cfg


def resolve_service_account_credentials(scopes: Optional[List[str]] = None) -> Tuple[Optional[Credentials], Optional[str]]:
    """Resolves service account credentials from raw JSON env var, JSON file, or default paths."""
    scopes = scopes or ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/calendar.readonly"]
    config = get_app_config()
    sa_val = (config.get("GOOGLE_SERVICE_ACCOUNT_JSON_CONTENT") or config.get("GOOGLE_SERVICE_ACCOUNT_JSON") or "").strip()

    # 1. If environment variable contains raw JSON string:
    if sa_val.startswith("{") and sa_val.endswith("}"):
        try:
            info = json.loads(sa_val)
            creds = Credentials.from_service_account_info(info, scopes=scopes)
            # Write to disk so any other process/script expecting a path can read it
            target_file = os.path.join(HERE, "config", "service_account.json")
            os.makedirs(os.path.dirname(target_file), exist_ok=True)
            with open(target_file, "w", encoding="utf-8") as f:
                f.write(sa_val)
            return creds, target_file
        except Exception as e:
            print(f"[WARN] Failed to parse JSON from environment variable: {e}")

    # 2. Candidate file paths:
    candidates = [
        sa_val if (sa_val and os.path.isabs(sa_val)) else (os.path.join(HERE, sa_val) if sa_val else None),
        os.path.join(HERE, "config", "service_account.json"),
        os.path.expanduser("~/.classtracker/config/service_account.json"),
        "/etc/secrets/service_account.json",  # Standard Render Secret File path
    ]
    for p in candidates:
        if p and os.path.exists(p) and os.path.isfile(p):
            try:
                creds = Credentials.from_service_account_file(p, scopes=scopes)
                return creds, p
            except Exception as e:
                print(f"[WARN] Error reading service account file at {p}: {e}")

    return None, None


def get_service_account_email(config: Optional[Dict[str, Any]] = None) -> str:
    """Extracts client email dynamically from service account credentials or config."""
    cfg = config or get_app_config()
    bot = cfg.get("BOT_EMAIL")
    if bot:
        return bot
    creds, sa_path = resolve_service_account_credentials()
    if creds and hasattr(creds, "service_account_email"):
        return creds.service_account_email
    if sa_path and os.path.exists(sa_path):
        try:
            with open(sa_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("client_email", "")
        except Exception:
            pass
    return "class-tracker-bot@class-tracker-507704.iam.gserviceaccount.com"


def get_sheet_client(config: Dict[str, Any], sheet_id: Optional[str] = None, tab_name: Optional[str] = None) -> SheetClient:
    """Instantiates SheetClient with service account and targeted sheet/tab."""
    creds, sa_path = resolve_service_account_credentials()
    sid = sheet_id or config.get("GOOGLE_SHEET_ID", "")
    tab = tab_name or config.get("SHEET_TAB_NAME", "Sheet1")

    return SheetClient(
        service_account_json=sa_path or config.get("GOOGLE_SERVICE_ACCOUNT_JSON", "config/service_account.json"),
        sheet_id=sid,
        tab_name=tab,
    )


def record_event(
    filename: str,
    success: bool,
    unit_matched: Optional[str] = None,
    status: Optional[str] = None,
    percent_covered: Optional[int] = None,
    warnings: Optional[list] = None,
    error: Optional[str] = None,
):
    """Appends an event to the circular in-memory event log."""
    event = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "filename": filename,
        "unit_matched": unit_matched,
        "status": status,
        "percent_covered": percent_covered,
        "success": success,
        "warnings": warnings or [],
        "error": error,
    }
    EVENT_LOGS.appendleft(event)


def scan_failed_dir():
    failed_files = glob.glob(os.path.join(FAILED_DIR, "*"))
    if failed_files:
        print(f"[STARTUP] ⚠️ Found {len(failed_files)} unprocessed dead-letter file(s) in {FAILED_DIR}:")
        for f in failed_files:
            print(f"   • {os.path.basename(f)}")
    else:
        print(f"[STARTUP] Dead-letter queue ({FAILED_DIR}) is clean (0 files).")


scan_failed_dir()


# ==============================================================================
# WEB PORTAL FRONTEND ROUTES
# ==============================================================================

@app.route("/", methods=["GET"])
def index():
    """Serves the main Web Onboarding Portal and Dashboard."""
    return render_template("index.html")


# ==============================================================================
# WEB ONBOARDING PORTAL APIS
# ==============================================================================

@app.route("/api/verify-sheet", methods=["POST"])
def verify_sheet():
    """Validates Google Sheet URL/ID, returns spreadsheet title, tab names, and column health."""
    data = request.get_json(silent=True) or {}
    sheet_input = data.get("sheet_url") or data.get("sheet_id") or ""

    if not sheet_input.strip():
        return jsonify({"error": "Please provide a Google Sheet URL or ID"}), 400

    # Extract Sheet ID from URL if full link was provided
    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", sheet_input)
    sheet_id = match.group(1) if match else sheet_input.strip()

    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds, sa_path = resolve_service_account_credentials(scopes=scopes)
    if not creds:
        return jsonify({"error": "Service account credentials not configured. Please paste your service account JSON into the GOOGLE_SERVICE_ACCOUNT_JSON environment variable."}), 500

    try:
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(sheet_id)

        tabs_info = []
        for ws in sh.worksheets():
            tabs_info.append({
                "id": str(ws.id),
                "title": ws.title,
                "row_count": ws.row_count,
                "col_count": ws.col_count,
            })

        # Smart tab suggestion: ignore metadata tabs (README, instructions, config, dashboard)
        IGNORE_NAMES = {"readme", "instructions", "info", "changelog", "notes", "config", "dashboard"}
        suggested_tab = None
        has_topic_col = False
        detected_headers = []

        # Check worksheets for valid syllabus columns first
        for t in tabs_info:
            t_name = t["title"].strip()
            if t_name.lower() in IGNORE_NAMES:
                continue
            try:
                target_ws = sh.worksheet(t_name)
                header_vals = target_ws.get_all_values()
                for r in header_vals[:5]:
                    if any(str(c).strip().lower() in ("topic", "unit id", "date taught", "unit_id") for c in r):
                        has_topic_col = True
                        detected_headers = [str(c).strip() for c in r if str(c).strip()]
                        suggested_tab = t_name
                        break
                if suggested_tab:
                    break
            except Exception:
                continue

        # Fallback to first non-ignored tab, or first tab
        if not suggested_tab:
            for t in tabs_info:
                if t["title"].strip().lower() not in IGNORE_NAMES:
                    suggested_tab = t["title"]
                    break
        if not suggested_tab:
            suggested_tab = tabs_info[0]["title"] if tabs_info else "Sheet1"

        return jsonify({
            "success": True,
            "sheet_id": sheet_id,
            "title": sh.title,
            "tabs": tabs_info,
            "suggested_tab": suggested_tab,
            "has_valid_columns": has_topic_col,
            "detected_headers": detected_headers,
            "bot_email": creds.service_account_email,
        }), 200

    except Exception as e:
        err_msg = str(e)
        bot_mail = get_service_account_email(config) or "your service account email"
        if "404" in err_msg:
            return jsonify({
                "error": "Spreadsheet not found or bot does not have access. Please share the Google Sheet with: "
                         f"{bot_mail} as Editor."
            }), 404
        return jsonify({"error": f"Could not connect to Google Sheet: {err_msg}"}), 400


@app.route("/api/parse-syllabus", methods=["POST"])
def parse_syllabus():
    """Accepts uploaded syllabus file(s) (PDF, DOCX, XLSX, TXT) or raw text and extracts structured units.
    Supports detecting multiple courses/subjects simultaneously.
    """
    config = get_app_config()
    course_name = request.form.get("course_name") or "Course"
    course_code = request.form.get("course_code") or "CRS"
    
    # Optional list of known sheet tabs to aid automatic matching
    raw_tabs = request.form.get("available_tabs") or request.args.get("available_tabs")
    available_tabs = None
    if raw_tabs:
        try:
            available_tabs = json.loads(raw_tabs) if raw_tabs.startswith("[") else [t.strip() for t in raw_tabs.split(",")]
        except Exception:
            available_tabs = [t.strip() for t in raw_tabs.split(",")]

    all_raw_text = []

    # Check for multiple or single file uploads
    uploaded_files = request.files.getlist("files") or request.files.getlist("file")
    if uploaded_files and any(f.filename for f in uploaded_files):
        for f in uploaded_files:
            if not f or not f.filename:
                continue
            ext = os.path.splitext(f.filename)[1].lower()
            tmp_path = None
            try:
                # Read file content into memory first, then write to temp file
                file_data = f.read()
                with tempfile.NamedTemporaryFile(suffix=ext, delete=False, mode='wb') as tmp:
                    tmp_path = tmp.name
                    tmp.write(file_data)
                    tmp.flush()
                # File handle is now closed — safe to read from disk
                txt = extract_text_from_file(tmp_path)
                all_raw_text.append(f"=== Document: {f.filename} ===\n{txt}")
            except Exception as file_err:
                print(f"[WARN] Failed to extract text from {f.filename}: {file_err}")
                all_raw_text.append(f"=== Document: {f.filename} ===\n[Extraction failed: {file_err}]")
            finally:
                if tmp_path and os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except Exception:
                        pass

    elif request.is_json:
        data = request.get_json(silent=True) or {}
        raw_text_input = data.get("text", "")
        if raw_text_input:
            all_raw_text.append(raw_text_input)
        course_name = data.get("course_name") or course_name
        course_code = data.get("course_code") or course_code
        if data.get("available_tabs"):
            available_tabs = data["available_tabs"]

    elif request.form.get("text"):
        all_raw_text.append(request.form.get("text", ""))

    combined_text = "\n\n".join(all_raw_text).strip()
    if not combined_text:
        return jsonify({"error": "No syllabus file or text provided."}), 400

    groq_api_key = config.get("GROQ_API_KEY")
    if not groq_api_key:
        return jsonify({"error": "GROQ_API_KEY is not configured on the server."}), 500

    from syllabus_parser import parse_multi_subject_syllabus

    try:
        courses = parse_multi_subject_syllabus(
            raw_text=combined_text,
            available_tabs=available_tabs,
            groq_api_key=groq_api_key,
            groq_model=config.get("GROQ_MODEL", "qwen/qwen3.8-27b"),
        )

        total_units = sum(len(c.get("units", [])) for c in courses)

        return jsonify({
            "success": True,
            "multi_course": len(courses) > 1,
            "courses_count": len(courses),
            "courses": courses,
            # Backwards compatibility for single-course flow
            "units": courses[0]["units"] if courses else [],
            "count": total_units,
            "detected_name": courses[0]["course_name"] if courses else course_name,
            "detected_code": courses[0]["course_code"] if courses else course_code,
            "matched_tab": courses[0].get("matched_tab", "") if courses else "",
        }), 200

    except Exception as e:
        return jsonify({"error": f"AI Curriculum Extraction failed: {str(e)}"}), 500


@app.route("/api/save-course", methods=["POST"])
def api_save_course():
    """Saves or updates a course profile with pairing code and curriculum units."""
    data = request.get_json(silent=True) or {}
    name = data.get("name")
    code = data.get("code")
    sheet_id = data.get("sheet_id")
    tab_name = data.get("tab_name") or data.get("code") or "Sheet1"
    teacher_email = data.get("teacher_email", "")
    calendar_id = data.get("calendar_id", "")
    units = data.get("units", [])
    course_id = data.get("course_id")

    if not name or not code or not sheet_id:
        return jsonify({"error": "Missing required fields: name, code, sheet_id"}), 400

    # Auto-extract sheet ID if full URL was passed
    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", str(sheet_id))
    clean_sheet = match.group(1) if match else str(sheet_id).strip()

    try:
        saved = save_course(
            name=name,
            code=code,
            sheet_id=clean_sheet,
            tab_name=tab_name,
            teacher_email=teacher_email,
            calendar_id=calendar_id,
            units=units,
            course_id=course_id,
        )
        return jsonify({
            "success": True,
            "course": saved,
            "pairing_code": saved.get("pairing_code"),
        }), 200
    except Exception as e:
        return jsonify({"error": f"Failed to save course profile: {str(e)}"}), 500


@app.route("/api/courses", methods=["GET"])
def api_get_courses():
    """Returns all configured course profiles."""
    courses = get_all_courses()
    return jsonify({"courses": courses}), 200


@app.route("/api/course/<course_id>", methods=["GET", "DELETE"])
def api_course_detail(course_id: str):
    """Retrieves or removes a course profile."""
    if request.method == "DELETE":
        deleted = delete_course(course_id)
        return jsonify({"success": deleted}), (200 if deleted else 404)

    course = get_course_by_id(course_id)
    if not course:
        return jsonify({"error": f"Course '{course_id}' not found"}), 404
    return jsonify({"course": course}), 200


@app.route("/api/pair/<code>", methods=["GET"])
def api_pair_client(code: str):
    """Universal macOS App pairing endpoint. Returns payload required to configure the client."""
    clean_code = (code or "").strip()
    if not clean_code:
        return jsonify({
            "success": False,
            "error": "Pairing code cannot be blank."
        }), 400

    course = get_course_by_pairing_code(clean_code) or get_course_by_id(clean_code)
    if not course:
        prefix = clean_code.split("-")[0].strip().lower()
        if len(prefix) >= 2:
            course = get_course_by_id(prefix)

    if not course:
        return jsonify({
            "success": False,
            "error": f"Invalid or expired pairing code '{clean_code}'. Check the web dashboard for active codes."
        }), 404

    config = get_app_config()

    # Payload contains curriculum units, sheet coordinates, and calendar info
    return jsonify({
        "success": True,
        "course_id": course["id"],
        "name": course["name"],
        "code": course["code"],
        "sheet_id": course["sheet_id"],
        "tab_name": course["tab_name"],
        "teacher_email": course.get("teacher_email", ""),
        "calendar_id": course.get("calendar_id", ""),
        "units": course.get("units", []),
        "curriculum_count": len(course.get("units", [])),
        "bot_email": get_service_account_email(config) or config.get("BOT_EMAIL", ""),
    }), 200


@app.route("/api/course/<course_id>/progress", methods=["GET"])
def api_course_progress(course_id: str):
    """Live reads syllabus completion and progress directly from Google Sheets."""
    course = get_course_by_id(course_id)
    if not course:
        return jsonify({"error": "Course not found"}), 404

    config = get_app_config()
    try:
        sheet = get_sheet_client(
            config=config,
            sheet_id=course["sheet_id"],
            tab_name=course["tab_name"]
        )

        all_values = sheet.ws.get_all_values()
        if not all_values:
            return jsonify({"error": "Worksheet is empty"}), 400

        # Detect header row
        header_idx = 0
        for i, r in enumerate(all_values[:5]):
            if any(str(c).strip().lower() in ("topic", "unit id", "date taught") for c in r):
                header_idx = i
                break

        data_rows = []
        completed_count = 0
        started_count = 0
        not_started_count = 0

        for row_idx, r in enumerate(all_values[header_idx + 1:], start=header_idx + 2):
            if not r or len(r) < 3 or not any(str(c).strip() for c in r):
                continue
            unit_id = r[0] if len(r) > 0 else ""
            topic = r[2] if len(r) > 2 else ""
            date_taught = r[8] if len(r) > 8 else ""
            status = r[9] if len(r) > 9 else "Not Started"
            pct_complete = r[10] if len(r) > 10 else "0%"
            leftover = r[12] if len(r) > 12 else ""
            classwork = r[14] if len(r) > 14 else ""
            homework = r[15] if len(r) > 15 else ""
            notes = r[16] if len(r) > 16 else ""

            # Skip totals/footer
            if "total" in str(topic).lower() or "total" in str(unit_id).lower():
                continue

            stat_clean = status.strip().lower()
            if stat_clean == "completed":
                completed_count += 1
            elif stat_clean in ("started", "in progress"):
                started_count += 1
            else:
                not_started_count += 1

            data_rows.append({
                "row_number": row_idx,
                "unit_id": unit_id,
                "topic": topic,
                "date_taught": date_taught,
                "status": status,
                "percent_complete": pct_complete,
                "leftover": leftover,
                "classwork": classwork,
                "homework": homework,
                "notes": notes,
            })

        total_units = len(data_rows)
        pct_overall = int(round((completed_count / total_units) * 100)) if total_units > 0 else 0

        # Find next unit to teach
        next_unit = next((row for row in data_rows if row["status"].strip().lower() not in ("completed",)), None)

        return jsonify({
            "success": True,
            "course_id": course["id"],
            "course_name": course["name"],
            "sheet_title": sheet.ws.spreadsheet.title,
            "tab_name": course["tab_name"],
            "total_units": total_units,
            "completed_count": completed_count,
            "started_count": started_count,
            "not_started_count": not_started_count,
            "percent_overall": pct_overall,
            "next_unit": next_unit,
            "rows": data_rows,
        }), 200

    except Exception as e:
        return jsonify({"error": f"Failed to read sheet progress: {str(e)}"}), 500


@app.route("/api/settings/email", methods=["GET", "POST"])
def api_settings_email():
    """Universal Email Service settings: saves Gmail SMTP credentials to user config ~/.classtracker/config/.env."""
    user_env_path = os.path.expanduser("~/.classtracker/config/.env")
    os.makedirs(os.path.dirname(user_env_path), exist_ok=True)

    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        sender = data.get("gmail_sender", "").strip()
        app_password = data.get("gmail_app_password", "").strip().replace(" ", "")
        test_send = data.get("test_send", False)

        if not sender or not app_password:
            return jsonify({"error": "Please provide both Gmail Address and App Password."}), 400

        # Validate SMTP connection and send confirmation test email if requested
        if test_send:
            try:
                import smtplib
                from email.mime.text import MIMEText
                msg = MIMEText(
                    "Hello from Class Tracker! 🎓\n\n"
                    "Your Gmail SMTP credentials have been verified successfully.\n"
                    "Lecture summaries and syllabus updates will now be sent automatically after each class.",
                    "plain",
                    "utf-8"
                )
                msg["From"] = sender
                msg["To"] = sender
                msg["Subject"] = "🎓 Class Tracker — Email Dispatch Verification"
                with smtplib.SMTP("smtp.gmail.com", 587, timeout=15) as s:
                    s.starttls()
                    s.login(sender, app_password)
                    s.send_message(msg)
            except Exception as e:
                return jsonify({"error": f"Gmail SMTP authentication failed: {str(e)}"}), 400

        # Persist to ~/.classtracker/config/.env
        current = dotenv_values(user_env_path) if os.path.exists(user_env_path) else {}
        current["GMAIL_SENDER"] = sender
        current["GMAIL_APP_PASSWORD"] = app_password

        with open(user_env_path, "w", encoding="utf-8") as f:
            for k, v in current.items():
                f.write(f"{k}={v}\n")

        return jsonify({
            "success": True,
            "message": f"Email credentials verified and saved! Test email sent to {sender}.",
            "gmail_sender": sender
        }), 200

    # GET request: return active configuration status
    current = dotenv_values(user_env_path) if os.path.exists(user_env_path) else {}
    sender = current.get("GMAIL_SENDER", "")
    has_pwd = bool(current.get("GMAIL_APP_PASSWORD"))
    return jsonify({
        "gmail_sender": sender,
        "has_password": has_pwd,
        "is_configured": bool(sender and has_pwd),
    }), 200


@app.route("/api/download-dmg", methods=["GET"])
def download_dmg():
    """Serves the universal ClassTracker_Installer.dmg for direct browser download."""
    candidates = [
        "/Users/keerthikumar/Downloads/ClassTracker_Installer.dmg",
        os.path.join(HERE, "ClassTracker_Installer.dmg"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return send_file(c, as_attachment=True, download_name="ClassTracker_Installer.dmg")
    return jsonify({"error": "Installer DMG file not found on server"}), 404


@app.route("/api/download-config/<course_id_or_code>", methods=["GET"])
def download_config(course_id_or_code: str):
    """Generates and downloads a custom .env configuration file tailored to the selected course."""
    course = get_course_by_pairing_code(course_id_or_code) or get_course_by_id(course_id_or_code)
    if not course:
        return jsonify({"error": "Course not found"}), 404

    base_cfg = get_app_config()
    content = f"""# Class Tracker Configuration for {course['name']} ({course['code']})
GROQ_API_KEY={base_cfg.get('GROQ_API_KEY', '')}
GROQ_MODEL={base_cfg.get('GROQ_MODEL', 'qwen/qwen3.8-27b')}
WHISPER_GROQ_MODEL={base_cfg.get('WHISPER_GROQ_MODEL', 'whisper-large-v3')}

GOOGLE_SHEET_ID={course['sheet_id']}
SHEET_TAB_NAME={course['tab_name']}
GOOGLE_SERVICE_ACCOUNT_JSON=config/service_account.json
COURSE_FILTER={course['code']}

TEACHER_EMAIL={course.get('teacher_email', '')}
DEVELOPER_EMAIL={base_cfg.get('DEVELOPER_EMAIL', '')}
GMAIL_SENDER={base_cfg.get('GMAIL_SENDER', '')}
GMAIL_APP_PASSWORD={base_cfg.get('GMAIL_APP_PASSWORD', '')}

WATCH_FOLDER=~/ClassAudio/incoming
DELETE_AUDIO_AFTER_PROCESS=false
PAIRING_CODE={course.get('pairing_code', '')}
"""
    return Response(
        content,
        mimetype="text/plain",
        headers={"Content-Disposition": f"attachment;filename=config_{course['code'].lower()}.env"}
    )


# ==============================================================================
# EXISTING PIPELINE & HEALTH ROUTES (PRESERVED)
# ==============================================================================

@app.route("/healthz", methods=["GET"])
def healthz():
    """Lightweight liveness check used by Render every 30 seconds."""
    return jsonify({"alive": True}), 200


@app.route("/health", methods=["GET"])
def health():
    """Verifies Groq API, Google Sheets connection, and curriculum.json loadability."""
    config = get_app_config()
    degraded_reasons = []

    # 1. Check curriculum.json
    curriculum_path = config.get("CURRICULUM_PATH", os.path.join(HERE, "config", "curriculum.json"))
    if not os.path.isabs(curriculum_path):
        curriculum_path = os.path.join(HERE, curriculum_path)

    try:
        curriculum = load_curriculum(curriculum_path)
        if not curriculum:
            degraded_reasons.append("Curriculum catalog is empty.")
    except Exception as e:
        degraded_reasons.append(f"Curriculum load failed: {e}")

    # 2. Check Groq API
    groq_api_key = config.get("GROQ_API_KEY")
    if not groq_api_key:
        degraded_reasons.append("GROQ_API_KEY is not configured.")
    else:
        try:
            client = Groq(api_key=groq_api_key, timeout=10.0)
            model = config.get("GROQ_MODEL", "qwen/qwen3.8-27b")
            client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1,
            )
        except Exception as e:
            degraded_reasons.append(f"Groq API health check failed: {e}")

    # 3. Check Google Sheets connection
    try:
        sheet = get_sheet_client(config)
        # Minimal read check
        sheet.ws.cell(1, 1).value
    except Exception as e:
        degraded_reasons.append(f"Google Sheets connection failed: {e}")

    if degraded_reasons:
        return jsonify({
            "status": "degraded",
            "reason": "; ".join(degraded_reasons),
        }), 503

    return jsonify({"status": "ok"}), 200


@app.route("/upload", methods=["POST"])
def upload():
    """Accepts multipart audio upload, runs full pipeline, handles retries and notifications."""
    if "file" not in request.files:
        return jsonify({"error": "No file field in multipart request"}), 400

    audio_file = request.files["file"]
    if not audio_file or not audio_file.filename:
        return jsonify({"error": "Empty filename"}), 400

    orig_filename = audio_file.filename
    config = get_app_config()

    # Optional course_id override
    course_id = request.form.get("course_id")
    if course_id:
        course = get_course_by_id(course_id)
        if course:
            config["GOOGLE_SHEET_ID"] = course["sheet_id"]
            config["SHEET_TAB_NAME"] = course["tab_name"]

    # Save to temporary directory
    temp_dir = tempfile.mkdtemp(prefix="classtracker_upload_")
    temp_audio_path = os.path.join(temp_dir, orig_filename)

    try:
        audio_file.save(temp_audio_path)
        processed_dir = os.path.join(temp_dir, "processed")
        sheet = get_sheet_client(config)

        # Run pipeline with auto_confirm=True for cloud execution
        result = process_file(
            audio_path=temp_audio_path,
            config=config,
            sheet=sheet,
            processed_folder=processed_dir,
            auto_confirm=True,
        )

        success = bool(result and result.get("success", False))
        record_event(
            filename=orig_filename,
            success=success,
            unit_matched=result.get("unit_id") if result else None,
            status=result.get("status") if result else None,
            percent_covered=result.get("percent_covered_today") if result else None,
            warnings=result.get("warnings") if result else None,
            error=result.get("message") if (result and not success) else None,
        )

        return jsonify({
            "status": "success" if success else "failed",
            "result": result,
        }), (200 if success else 422)

    except Exception as e:
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        failed_filename = f"{ts}_{orig_filename}"
        failed_path = os.path.join(FAILED_DIR, failed_filename)

        try:
            if os.path.exists(temp_audio_path):
                shutil.copy(temp_audio_path, failed_path)
                print(f"[DEAD-LETTER] Saved failed audio to: {failed_path}")
        except Exception as copy_err:
            print(f"[WARN] Could not copy to dead letter queue: {copy_err}")

        send_developer_alert(
            message=f"Permanent pipeline failure on file: {orig_filename}. Saved to dead letter {failed_path}.",
            filename=orig_filename,
            config=config,
            error=str(e),
            attachment_path=failed_path if os.path.exists(failed_path) else (temp_audio_path if os.path.exists(temp_audio_path) else None),
        )

        record_event(
            filename=orig_filename,
            success=False,
            error=str(e),
        )

        return jsonify({
            "status": "error",
            "message": f"Pipeline execution failed: {str(e)}",
            "dead_letter": failed_path,
        }), 500

    finally:
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass


@app.route("/retry", methods=["POST"])
def retry():
    """Accepts filename in /tmp/failed/ and reprocesses through the full pipeline."""
    data = request.get_json(silent=True) or {}
    filename = data.get("filename") or request.form.get("filename")

    if not filename:
        return jsonify({"error": "Missing 'filename' parameter in request"}), 400

    exact_path = os.path.join(FAILED_DIR, filename)
    target_path = None

    if os.path.exists(exact_path):
        target_path = exact_path
    else:
        matches = glob.glob(os.path.join(FAILED_DIR, f"*{filename}*"))
        if matches:
            target_path = matches[0]

    if not target_path or not os.path.exists(target_path):
        return jsonify({
            "error": f"File '{filename}' not found in dead-letter folder {FAILED_DIR}",
            "available_failed_files": [os.path.basename(f) for f in glob.glob(os.path.join(FAILED_DIR, "*"))],
        }), 404

    config = get_app_config()
    try:
        sheet = get_sheet_client(config)
        processed_dir = os.path.join(FAILED_DIR, "processed")

        result = process_file(
            audio_path=target_path,
            config=config,
            sheet=sheet,
            processed_folder=processed_dir,
            auto_confirm=True,
        )

        success = bool(result and result.get("success", False))
        record_event(
            filename=os.path.basename(target_path),
            success=success,
            unit_matched=result.get("unit_id") if result else None,
            status=result.get("status") if result else None,
            percent_covered=result.get("percent_covered_today") if result else None,
            warnings=result.get("warnings") if result else None,
            error=result.get("message") if (result and not success) else None,
        )

        if success and os.path.exists(target_path):
            try:
                os.remove(target_path)
            except Exception:
                pass

        return jsonify({
            "status": "success" if success else "failed",
            "result": result,
        }), (200 if success else 422)

    except Exception as e:
        send_developer_alert(
            message=f"Retry failed for {os.path.basename(target_path)}: {e}",
            filename=os.path.basename(target_path),
            config=config,
            error=str(e),
        )
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/logs", methods=["GET"])
def logs():
    """Returns the last 50 processing events as JSON."""
    return jsonify({
        "total_logged": len(EVENT_LOGS),
        "events": list(EVENT_LOGS),
    }), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print(f"\n[SERVER] 🚀 Class Tracker Web Portal starting on http://localhost:{port} (PORT={port})...")
    app.run(host="0.0.0.0", port=port, debug=False)
