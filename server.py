"""Cloud Deployment Flask API for Class Tracker.

Endpoints:
- GET /health  : Verifies Groq API, Google Sheets connection, and curriculum.json.
- GET /healthz : Lightweight liveness check for Render.
- POST /upload : Multipart audio file upload -> full processing pipeline.
- POST /retry  : Reprocesses a failed file from /tmp/failed/.
- GET /logs    : Returns the last 50 processing events as JSON.
"""

import os
import sys
import glob
import json
import shutil
import tempfile
from collections import deque
from datetime import datetime
from typing import Dict, Any, Optional, List

from flask import Flask, request, jsonify

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

# Load environment variables
load_dotenv(os.path.join(HERE, "config", ".env"))

app = Flask(__name__)

# In-memory circular buffer for last 50 processing events
EVENT_LOGS = deque(maxlen=50)

FAILED_DIR = "/tmp/failed"
os.makedirs(FAILED_DIR, exist_ok=True)


def get_app_config() -> Dict[str, Any]:
    """Combines .env file values with process environment variables."""
    env_file = os.path.join(HERE, "config", ".env")
    cfg = dotenv_values(env_file) if os.path.exists(env_file) else {}
    for k, v in os.environ.items():
        if v is not None:
            cfg[k] = v
    return cfg


def get_sheet_client(config: Dict[str, Any]) -> SheetClient:
    """Instantiates SheetClient with env content or file path."""
    sa_path = config.get("GOOGLE_SERVICE_ACCOUNT_JSON", "config/service_account.json")
    if not os.path.isabs(sa_path):
        sa_path = os.path.join(HERE, sa_path)

    return SheetClient(
        service_account_json=sa_path,
        sheet_id=config.get("GOOGLE_SHEET_ID", ""),
        tab_name=config.get("SHEET_TAB_NAME", "DAA"),
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


# Scan /tmp/failed/ on server startup
def scan_failed_dir():
    failed_files = glob.glob(os.path.join(FAILED_DIR, "*"))
    if failed_files:
        print(f"[STARTUP] ⚠️ Found {len(failed_files)} unprocessed dead-letter file(s) in {FAILED_DIR}:")
        for f in failed_files:
            print(f"   • {os.path.basename(f)}")
    else:
        print(f"[STARTUP] Dead-letter queue ({FAILED_DIR}) is clean (0 files).")


scan_failed_dir()


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
        # Permanent failure handling (all internal retries exhausted)
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        failed_filename = f"{ts}_{orig_filename}"
        failed_path = os.path.join(FAILED_DIR, failed_filename)

        try:
            if os.path.exists(temp_audio_path):
                shutil.copy(temp_audio_path, failed_path)
                print(f"[DEAD-LETTER] Saved failed audio to: {failed_path}")
        except Exception as copy_err:
            print(f"[WARN] Could not copy to dead letter queue: {copy_err}")

        # Send alert only to developer, NEVER to teacher (attaching the failed audio file)
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
        # Always clean up temporary directory
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

    # Search in /tmp/failed/
    exact_path = os.path.join(FAILED_DIR, filename)
    target_path = None

    if os.path.exists(exact_path):
        target_path = exact_path
    else:
        # Check if matched by partial name
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
    app.run(host="0.0.0.0", port=port)
