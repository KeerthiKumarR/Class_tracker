"""Watches WATCH_FOLDER continuously and processes new audio files as they appear.
Run this in the background (or install via launchd, see com.classtracker.watcher.plist).
"""

import os
import sys
import time

from dotenv import dotenv_values
from sheets_client import SheetClient
from pipeline import process_file

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HERE)
POLL_SECONDS = 60


def load_config():
    env_path = os.path.join(PROJECT_ROOT, "config", ".env")
    if not os.path.exists(env_path):
        print(f"[ERROR] Missing {env_path}. Copy config/.env.example to config/.env and fill it in.")
        sys.exit(1)
    return dotenv_values(env_path)


def main():
    config = load_config()
    watch_folder = os.path.expanduser(config["WATCH_FOLDER"])
    processed_folder = os.path.abspath(os.path.join(watch_folder, "..", "processed"))
    os.makedirs(watch_folder, exist_ok=True)
    os.makedirs(processed_folder, exist_ok=True)

    service_account_path = os.path.join(PROJECT_ROOT, config["GOOGLE_SERVICE_ACCOUNT_JSON"]) \
        if not os.path.isabs(config["GOOGLE_SERVICE_ACCOUNT_JSON"]) \
        else config["GOOGLE_SERVICE_ACCOUNT_JSON"]

    from profile_manager import get_all_courses
    courses = get_all_courses(prefer_user_dir=True)
    first_c = courses[0] if courses else {}
    target_sid = first_c.get("sheet_id") or config.get("GOOGLE_SHEET_ID", "")
    target_tab = first_c.get("tab_name") or config.get("SHEET_TAB_NAME", "Sheet1")
    sheet = None
    if target_sid:
        sheet = SheetClient(
            service_account_json=service_account_path,
            sheet_id=target_sid,
            tab_name=target_tab,
        )

    print(f"[INFO] Watching {watch_folder} every {POLL_SECONDS}s. Ctrl+C to stop.")
    seen = set()

    while True:
        try:
            raw_audio_files = [
                f for f in os.listdir(watch_folder)
                if f.lower().endswith((".wav", ".m4a", ".mp3"))
            ]
            
            seen_sizes = {}
            for filename in sorted(raw_audio_files):
                filepath = os.path.join(watch_folder, filename)
                if not os.path.exists(filepath) or filename in seen:
                    continue
                size = os.path.getsize(filepath)
                if size in seen_sizes:
                    try:
                        os.remove(filepath)
                    except Exception:
                        pass
                    continue
                seen_sizes[size] = filename
                process_file(filepath, config, sheet, processed_folder, auto_confirm=True)
                seen.add(filename)
        except Exception as e:
            print(f"[ERROR] {e}")

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
