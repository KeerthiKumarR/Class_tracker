"""Process every audio file currently sitting in the watch folder, then exit.
Good for testing before setting up the always-on watcher.
"""

import os
import sys
from dotenv import dotenv_values

from sheets_client import SheetClient
from pipeline import process_file

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HERE)


def load_config():
    env_path = os.path.join(PROJECT_ROOT, "config", ".env")
    if not os.path.exists(env_path):
        print(f"[ERROR] Missing {env_path}. Copy config/.env.example to config/.env and fill it in.")
        sys.exit(1)
    return dotenv_values(env_path)


def deduplicate_audio_files(watch_folder: str, audio_files: list) -> list:
    """Cleans up accidental duplicate copies (e.g. 'Name 2.m4a' created by macOS Voice Memos drag & drop)."""
    seen_sizes = {}
    unique_files = []

    for filename in sorted(audio_files):
        filepath = os.path.join(watch_folder, filename)
        if not os.path.exists(filepath):
            continue
        size = os.path.getsize(filepath)

        if size in seen_sizes:
            print(f"[INFO] Detected duplicate paste '{filename}' (identical {size / (1024*1024):.1f}MB to '{seen_sizes[size]}'). Cleaning up duplicate.")
            try:
                os.remove(filepath)
            except Exception:
                pass
            continue

        seen_sizes[size] = filename
        unique_files.append(filename)

    return unique_files


import argparse


def main():
    parser = argparse.ArgumentParser(description="Process audio files in the watch folder and update Google Sheets.")
    parser.add_argument(
        "--unit",
        type=str,
        default=None,
        help="Optional target Unit ID override (e.g. DAA-8). If omitted, AI auto-matches from the 50-unit curriculum."
    )
    parser.add_argument(
        "-y", "--yes",
        action="store_true",
        help="Automatically approve and write proposed changes to Google Sheets without interactive prompt."
    )
    args = parser.parse_args()

    config = load_config()
    watch_folder = os.path.expanduser(config["WATCH_FOLDER"])
    processed_folder = os.path.abspath(os.path.join(watch_folder, "..", "processed"))

    os.makedirs(watch_folder, exist_ok=True)
    os.makedirs(processed_folder, exist_ok=True)

    service_account_path = os.path.join(PROJECT_ROOT, config["GOOGLE_SERVICE_ACCOUNT_JSON"]) \
        if not os.path.isabs(config["GOOGLE_SERVICE_ACCOUNT_JSON"]) \
        else config["GOOGLE_SERVICE_ACCOUNT_JSON"]

    try:
        from profile_manager import get_all_courses
        courses = get_all_courses(prefer_user_dir=True)
        first_c = courses[0] if courses else {}
        target_sid = first_c.get("sheet_id") or config.get("GOOGLE_SHEET_ID", "")
        target_tab = first_c.get("tab_name") or config.get("SHEET_TAB_NAME", "Sheet1")
        sheet = SheetClient(
            service_account_json=service_account_path,
            sheet_id=target_sid,
            tab_name=target_tab,
        )
    except Exception as e:
        print(f"\n[ERROR] Unable to connect to Google Sheets: {e}")
        print("[TIP] If you just switched Wi-Fi networks, wait a few seconds for DNS to finish connecting, then run again.\n")
        sys.exit(1)


    raw_audio_files = [
        f for f in os.listdir(watch_folder)
        if f.lower().endswith((".wav", ".m4a", ".mp3"))
    ]

    if not raw_audio_files:
        print(f"[INFO] No audio files found in {watch_folder}")
        return

    # Automatically clean up duplicate pastes from macOS Voice Memos
    audio_files = deduplicate_audio_files(watch_folder, raw_audio_files)

    for filename in audio_files:
        filepath = os.path.join(watch_folder, filename)
        if not os.path.exists(filepath):
            continue
        try:
            process_file(filepath, config, sheet, processed_folder, auto_confirm=args.yes, target_unit_id=args.unit)
        except Exception as e:
            print(f"[ERROR] Failed to process {filename}: {e}")


if __name__ == "__main__":
    main()

