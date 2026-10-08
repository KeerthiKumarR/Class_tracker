"""Automated Class Scheduler and Zero-Touch Audio Recorder.

Monitors config/schedule.json, automatically records classes through the Mac
microphone at the scheduled start time, and feeds audio into the pipeline upon class end.
"""

import os
import sys
import time
import json
import shutil
import signal
import subprocess
from datetime import datetime, timedelta
from dotenv import dotenv_values

from sheets_client import SheetClient
from pipeline import process_file
from calendar_sync import sync_google_calendar

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = getattr(sys, '_MEIPASS', os.path.dirname(HERE))


def load_config():
    env_path = os.path.join(PROJECT_ROOT, "config", ".env")
    if not os.path.exists(env_path):
        print(f"[ERROR] Missing {env_path}. Copy config/.env.example to config/.env and fill it in.")
        sys.exit(1)
    return dotenv_values(env_path)


def get_user_data_dir() -> str:
    data_dir = os.path.expanduser("~/.classtracker")
    os.makedirs(os.path.join(data_dir, "config"), exist_ok=True)
    return data_dir


def get_active_schedule_path() -> str:
    user_schedule = os.path.join(get_user_data_dir(), "config", "schedule.json")
    if not os.path.exists(user_schedule) or os.path.getsize(user_schedule) <= 2:
        # Seed from bundled schedule if available
        bundle_schedule = os.path.join(PROJECT_ROOT, "config", "schedule.json")
        if os.path.exists(bundle_schedule):
            try:
                shutil.copy2(bundle_schedule, user_schedule)
            except Exception:
                pass
    return user_schedule


def load_schedule(purge_expired: bool = True):
    schedule_path = get_active_schedule_path()
    if not os.path.exists(schedule_path):
        return []
    try:
        with open(schedule_path, "r", encoding="utf-8") as f:
            items = json.load(f)
    except Exception:
        return []

    if not purge_expired:
        return items

    now = datetime.now()
    valid_items = []
    changed = False

    for item in items:
        target_date_str = item.get("date")
        end_time_str = item.get("end_time")
        # If it has a specific calendar date and end time, check if it already ended
        if target_date_str and end_time_str:
            try:
                class_date = datetime.strptime(target_date_str, "%Y-%m-%d").date()
                end_time = datetime.strptime(end_time_str, "%H:%M").time()
                end_dt = datetime.combine(class_date, end_time)
                if end_dt < now:
                    changed = True
                    continue  # Purge expired past class
            except Exception:
                pass
        valid_items.append(item)

    if changed:
        try:
            with open(schedule_path, "w", encoding="utf-8") as f:
                json.dump(valid_items, f, indent=2)
        except Exception:
            pass

    return valid_items


def get_ffmpeg_bin() -> str:
    """Finds FFmpeg binary, prioritizing system authorized binaries and falling back to bundled binary."""
    # 1. System authorized Homebrew / system paths
    for p in ["/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", shutil.which("ffmpeg"), "/usr/bin/ffmpeg"]:
        if p and os.path.exists(p):
            return p
    # 2. PyInstaller sys._MEIPASS bundle path
    bundle_bin = getattr(sys, '_MEIPASS', None)
    if bundle_bin:
        p = os.path.join(bundle_bin, "bin", "ffmpeg")
        if os.path.exists(p):
            return p
    # 3. macOS App Bundle Frameworks/bin or MacOS/bin
    if hasattr(sys, "executable") and sys.executable:
        exe_dir = os.path.dirname(sys.executable)
        for p in [
            os.path.join(exe_dir, "..", "Frameworks", "bin", "ffmpeg"),
            os.path.join(exe_dir, "bin", "ffmpeg"),
            os.path.join(exe_dir, "_internal", "bin", "ffmpeg"),
        ]:
            if os.path.exists(p):
                return os.path.abspath(p)
    # 4. Project Root embedded
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    p_embedded = os.path.join(project_root, "embedded_bin", "ffmpeg")
    if os.path.exists(p_embedded):
        return p_embedded
    return "ffmpeg"


def record_class(output_path: str, duration_seconds: int) -> bool:
    """Records audio from Mac microphone using FFmpeg."""
    ffmpeg_bin = get_ffmpeg_bin()
    
    # FFmpeg AVFoundation audio capture command (device index :0 is MacBook Air Microphone)
    cmd = [
        ffmpeg_bin,
        "-y",
        "-f", "avfoundation",
        "-i", ":0",
        "-t", str(duration_seconds),
        "-c:a", "libmp3lame",
        "-b:a", "32k",
        "-ar", "16000",
        "-ac", "1",
        "-f", "mp3",
        output_path,
    ]
    
    print(f"\n[RECORDING] 🎙️  Capturing class audio for {duration_seconds // 60}m {duration_seconds % 60}s...")
    print(f"[RECORDING] Saving to: {output_path}")
    
    process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    # Show progress bar / countdown
    start_time = time.time()
    try:
        while process.poll() is None:
            elapsed = int(time.time() - start_time)
            remaining = max(0, duration_seconds - elapsed)
            mins_rem, secs_rem = divmod(remaining, 60)
            mins_el, secs_el = divmod(elapsed, 60)
            sys.stdout.write(f"\r[RECORDING] 🔴 Live | Elapsed: {mins_el:02d}:{secs_el:02d} | Remaining: {mins_rem:02d}:{secs_rem:02d} ")
            sys.stdout.flush()
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[INFO] Stop signal received. Finishing recording gracefully...")
        process.send_signal(signal.SIGINT)
        process.wait(timeout=10)
    
    print(f"\n[DONE] 🎙️ Recording completed: {output_path} ({os.path.getsize(output_path) / (1024*1024):.2f} MB)")
    return os.path.exists(output_path) and os.path.getsize(output_path) > 1000


def send_macos_notification(title: str, message: str):
    """Posts a native macOS banner notification in the top-right corner."""
    clean_msg = message.replace('"', '\\"')
    clean_title = title.replace('"', '\\"')
    script = f'display notification "{clean_msg}" with title "{clean_title}"'
    try:
        subprocess.run(["osascript", "-e", script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def prompt_start_dialog(class_name: str, start_time: str, end_time: str) -> bool:
    """Displays a native macOS dialog box in the foreground asking for user consent to start recording.
    
    The microphone is NEVER activated until the user explicitly clicks 'Start Recording'.
    Times out safely after 60 seconds if unattended.
    """
    clean_name = class_name.replace('"', '')
    applescript = f'''
    tell application "System Events"
        activate
        display dialog "🔔 {clean_name} is scheduled now ({start_time} - {end_time}).\\n\\nStart recording class lecture?" with title "Class Tracker 🎙️" buttons {{"Cancel / Skip", "Start Recording"}} default button "Start Recording" giving up after 60 with icon note
    end tell
    '''
    try:
        res = subprocess.run(["osascript", "-e", applescript], capture_output=True, text=True)
        if "button returned:Start Recording" in res.stdout:
            return True
        else:
            print(f"\n[SKIPPED] 🛑 Recording cancelled or timed out by user for '{class_name}'.")
            return False
    except Exception as e:
        print(f"[ERROR] Dialog prompt error: {e}")
        return False


def prompt_retry_dialog(class_name: str, end_time: str) -> bool:
    """Displays a gentle follow-up confirmation dialog 1 minute after an initial skip if the lecture is still in session."""
    clean_name = class_name.replace('"', '')
    applescript = f'''
    tell application "System Events"
        activate
        display dialog "🔔 {clean_name} is still in session (until {end_time}).\\n\\nDid you mean to start recording?" with title "Class Tracker 🎙️" buttons {{"Dismiss for Today", "Start Recording"}} default button "Start Recording" giving up after 60 with icon note
    end tell
    '''
    try:
        res = subprocess.run(["osascript", "-e", applescript], capture_output=True, text=True)
        return "button returned:Start Recording" in res.stdout
    except Exception as e:
        print(f"[ERROR] Retry dialog error: {e}")
        return False


def is_file_locked_or_recording(fpath: str) -> bool:
    """Checks whether a file is actively being written to by FFmpeg or any other process."""
    if not os.path.exists(fpath):
        return True
    try:
        res = subprocess.run(["lsof", fpath], capture_output=True, text=True)
        if res.returncode == 0 and res.stdout.strip():
            return True
    except Exception:
        pass

    try:
        s1 = os.path.getsize(fpath)
        time.sleep(1.5)
        s2 = os.path.getsize(fpath)
        if s1 != s2:
            return True
    except Exception:
        pass
    return False


def recover_pending_audio(watch_folder: str, config: dict, sheet: SheetClient, processed_folder: str):
    """Self-Healing: Automatically sweeps incoming folder for unprocessed audio files
    and processes them into the sheet ONLY when recording is completely finished.
    """
    if not os.path.exists(watch_folder):
        return
    valid_exts = (".mp3", ".m4a", ".wav")
    for fname in os.listdir(watch_folder):
        if fname.endswith((".recording", ".tmp", ".part", ".crdownload")):
            continue
        fpath = os.path.join(watch_folder, fname)
        if os.path.isfile(fpath) and fname.lower().endswith(valid_exts):
            # Strict safety check: Never touch a file that is actively open or being recorded
            if is_file_locked_or_recording(fpath):
                continue

            # Check file is at least 15 seconds old and not 0 bytes
            if (time.time() - os.path.getmtime(fpath) > 15) and os.path.getsize(fpath) > 1000:
                print(f"\n[SELF-HEALING] 🔄 Auto-recovering pending audio: {fname}...")
                try:
                    process_file(fpath, config, sheet, processed_folder, auto_confirm=True)
                except Exception as e:
                    print(f"[WARN] Pending audio recovery deferred: {e}")


def run_scheduler(test_mode: bool = False):
    config = load_config()
    watch_folder = os.path.expanduser(config["WATCH_FOLDER"])
    processed_folder = os.path.abspath(os.path.join(watch_folder, "..", "processed"))
    os.makedirs(watch_folder, exist_ok=True)
    os.makedirs(processed_folder, exist_ok=True)

    service_account_path = (
        os.path.join(PROJECT_ROOT, config["GOOGLE_SERVICE_ACCOUNT_JSON"])
        if not os.path.isabs(config["GOOGLE_SERVICE_ACCOUNT_JSON"])
        else config["GOOGLE_SERVICE_ACCOUNT_JSON"]
    )

    sheet = SheetClient(
        service_account_json=service_account_path,
        sheet_id=config["GOOGLE_SHEET_ID"],
        tab_name=config["SHEET_TAB_NAME"],
    )

    print("\n" + "=" * 65)
    print("📅 CLASS TRACKER AUTOMATED SCHEDULER ACTIVE")
    print("=" * 65)

    # 1. Sync with Google Calendar if shared with the bot
    try:
        sync_google_calendar()
    except Exception:
        pass

    schedule = load_schedule()
    active_classes = [c for c in schedule if c.get("enabled", True)]
    
    print(f"[INFO] Monitoring {len(active_classes)} scheduled class(es):")
    for c in active_classes:
        print(f"  • {c['name']} ({c['subject']}) on {c.get('date', c.get('day'))} from {c['start_time']} to {c['end_time']}")
    print("=" * 65 + "\n")

    executed_events = set()
    last_calendar_sync = time.time()

    while True:
        now = datetime.now()
        
        # Periodic Google Calendar sync every 30 minutes
        if time.time() - last_calendar_sync > 1800:
            try:
                sync_google_calendar()
            except Exception:
                pass
            last_calendar_sync = time.time()

        schedule = load_schedule()
        active_classes = [c for c in schedule if c.get("enabled", True)]

        for item in active_classes:
            target_date_str = item.get("date")
            start_time_str = item["start_time"]
            end_time_str = item["end_time"]
            class_name = item["name"]

            # Match date or recurring weekday
            if target_date_str:
                class_date = datetime.strptime(target_date_str, "%Y-%m-%d").date()
                if class_date != now.date():
                    continue
            elif item.get("day"):
                target_day = item["day"].strip().lower()
                today_day = now.strftime("%A").lower()
                if target_day != today_day and target_day != "daily":
                    continue
                class_date = now.date()
            else:
                class_date = now.date()

            start_dt = datetime.combine(class_date, datetime.strptime(start_time_str, "%H:%M").time())
            end_dt = datetime.combine(class_date, datetime.strptime(end_time_str, "%H:%M").time())

            event_key = f"{class_name}_{start_dt.strftime('%Y-%m-%d_%H:%M')}"

            # Check if class is about to start or currently ongoing in its time window
            seconds_until_start = (start_dt - now).total_seconds()
            is_ongoing = (start_dt <= now < end_dt)
            is_about_to_start = (0 <= seconds_until_start <= 45)

            if (is_about_to_start or is_ongoing) and event_key not in executed_events:
                # 1. Require explicit user consent before activating microphone
                user_approved = prompt_start_dialog(class_name, start_time_str, end_time_str)
                
                # Mark event as handled so it doesn't loop
                executed_events.add(event_key)

                if not user_approved:
                    print(f"[INFO] User skipped recording for '{class_name}'.")
                    continue

                # Compute remaining recording duration with a 60-minute overtime buffer so extended lectures are never cut off
                duration = int(max(10, (end_dt - datetime.now()).total_seconds())) + 3600
                timestamp_str = start_dt.strftime("%Y-%m-%d")
                safe_name = class_name.replace(" ", "_").replace("(", "").replace(")", "")
                audio_filename = f"{safe_name}_{timestamp_str}.mp3"
                audio_filepath = os.path.join(watch_folder, audio_filename)

                print(f"\n[RECORDING] 🎙️ Starting Class Recording: '{class_name}' ({start_time_str} to {end_time_str})")
                send_macos_notification(
                    "Class Tracker 🎙️",
                    f"🔴 Recording Started: '{class_name}' (Ends at {end_time_str})"
                )
                
                success = record_class(audio_filepath, duration_seconds=duration)

                if success:
                    print("\n[INFO] Class concluded! Auto-stopping and running pipeline...")
                    send_macos_notification(
                        "Class Tracker ✅",
                        f"Recording complete for '{class_name}'. Transcribing & updating Google Sheet..."
                    )
                    try:
                        process_file(audio_filepath, config, sheet, processed_folder, auto_confirm=True)
                    except Exception as e:
                        print(f"[ERROR] Pipeline execution failed: {e}")

            elif seconds_until_start > 0:
                mins_left = int(seconds_until_start // 60)
                hours_left = int(mins_left // 60)
                rem_mins = mins_left % 60
                rem_secs = int(seconds_until_start % 60)
                sys.stdout.write(f"\r⏳ Next Class: '{class_name}' at {start_time_str} (in {mins_left}m {rem_secs:02d}s) | Time: {now.strftime('%H:%M:%S')}  ")
                sys.stdout.flush()

        # Dynamic ultra-low power sleep
        active_classes = [c for c in schedule if c.get("enabled", True)]
        min_seconds_left = min(
            [(datetime.combine(datetime.strptime(c.get("date", now.strftime("%Y-%m-%d")), "%Y-%m-%d").date(), datetime.strptime(c["start_time"], "%H:%M").time()) - now).total_seconds() for c in active_classes] or [60]
        )
        if min_seconds_left > 300:
            time.sleep(30)
        elif min_seconds_left > 60:
            time.sleep(10)
        else:
            time.sleep(3)


if __name__ == "__main__":
    test = "--test" in sys.argv or "-t" in sys.argv
    run_scheduler(test_mode=test)
