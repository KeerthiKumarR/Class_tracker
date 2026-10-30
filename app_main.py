"""macOS Native Menu Bar Application & Background Engine for Class Tracker.

Features:
- Live Menu Bar Status & Countdown
- 1-Click Manual Start/Stop Recording (in case a popup was accidentally dismissed or class ended early)
- Real-time Google Calendar Sync with instant deletion/cancellation sync
- 100% thread-safe Main NSRunLoop architecture
"""

import os
import sys
import time
import json
import re
import shutil
import signal
import webbrowser
import threading
import subprocess
from datetime import datetime, timedelta

# Redirect stdout and stderr to debug.log for bundled app troubleshooting
try:
    _log_dir = os.path.expanduser("~/.classtracker")
    os.makedirs(_log_dir, exist_ok=True)
    _log_file = open(os.path.join(_log_dir, "debug.log"), "a", buffering=1, encoding="utf-8")
    sys.stdout = _log_file
    sys.stderr = _log_file
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ClassTracker process started (PID: {os.getpid()})")
except Exception:
    pass

import rumps
from dotenv import dotenv_values

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.join(HERE, "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from sheets_client import SheetClient
from pipeline import process_file
from calendar_sync import sync_google_calendar
from scheduler import (
    load_config,
    load_schedule,
    get_ffmpeg_bin,
    prompt_start_dialog,
    prompt_retry_dialog,
    send_macos_notification,
    recover_pending_audio,
)


class ClassTrackerMenuBarApp(rumps.App):
    def __init__(self):
        super(ClassTrackerMenuBarApp, self).__init__(
            name="Class Tracker",
            title="🎓 Class Tracker",
            quit_button=None
        )

        self.config = load_config()
        self.is_recording = False
        self.is_syncing = False
        self.sync_started_at = 0
        self.is_session_active = False
        self.prompted_events = set()
        self.current_recording_proc = None
        self.recording_start_time = None
        self.tick_count = 0
        self.last_cal_sync = time.time()
        self.current_status = "Active & Monitoring"
        self.next_class_label = "Checking schedule..."
        self.active_event_to_record = None

        # Build Menu
        self.item_status = rumps.MenuItem("Status: Active & Monitoring", callback=None)
        self.item_next_class = rumps.MenuItem("📅 Next Class: Checking...", callback=None)
        self.item_record_toggle = rumps.MenuItem("🎙️ Start Recording Now...", callback=self.on_toggle_recording)
        self.item_sync_cal = rumps.MenuItem("🔄 Sync Google Calendar Now", callback=self.on_sync_calendar)
        self.item_paired_courses = rumps.MenuItem("📚 Paired Subjects", callback=self.on_show_paired_subjects)
        self.item_pair_new = rumps.MenuItem("➕ Pair New Subject via Code...", callback=self.on_pair_subject_code)
        self.item_reset_courses = rumps.MenuItem("🗑️ Disconnect All Paired Courses...", callback=self.on_reset_courses)
        self.item_open_sheet = rumps.MenuItem("📊 Open Google Sheet", callback=self.on_open_sheet)
        self.item_open_audio_dir = rumps.MenuItem("📁 Open Recordings Folder", callback=self.on_open_audio_folder)
        self.item_separator_1 = rumps.separator
        self.item_quit = rumps.MenuItem("❌ Quit Class Tracker", callback=self.on_quit)

        self.menu = [
            self.item_status,
            self.item_next_class,
            self.item_record_toggle,
            self.item_sync_cal,
            self.item_paired_courses,
            self.item_pair_new,
            self.item_reset_courses,
            self.item_open_sheet,
            self.item_open_audio_dir,
            self.item_separator_1,
            self.item_quit
        ]

        self._refresh_paired_courses_menu()

        # Initial background calendar sync and recurring auto-sync loop
        threading.Thread(target=self._background_calendar_sync_loop, daemon=True).start()

        # Menu is refreshed initially; first-launch pairing prompt runs safely on main thread
        self.first_launch_prompted = False

    def _background_calendar_sync_loop(self):
        """Runs initial recovery sweep and continuously syncs Google Calendar only for paired courses."""
        time.sleep(1)
        watch_folder = os.path.expanduser(self.config.get("WATCH_FOLDER", "~/ClassAudio/incoming"))
        processed_folder = os.path.abspath(os.path.join(watch_folder, "..", "processed"))
        os.makedirs(watch_folder, exist_ok=True)
        os.makedirs(processed_folder, exist_ok=True)

        sa_path = os.path.join(HERE, self.config.get("GOOGLE_SERVICE_ACCOUNT_JSON", "config/service_account.json")) \
            if not os.path.isabs(self.config.get("GOOGLE_SERVICE_ACCOUNT_JSON", "config/service_account.json")) \
            else self.config.get("GOOGLE_SERVICE_ACCOUNT_JSON", "config/service_account.json")

        try:
            from profile_manager import get_all_courses
            courses = get_all_courses(prefer_user_dir=True)
            if courses and courses[0].get("sheet_id"):
                sheet = SheetClient(
                    service_account_json=sa_path,
                    sheet_id=courses[0]["sheet_id"],
                    tab_name=courses[0].get("tab_name", "Sheet1"),
                )
                recover_pending_audio(watch_folder, self.config, sheet, processed_folder)
        except Exception as e:
            print(f"[WARN] Initial recovery sweep note: {e}")

        # Continuous background sync only when courses are paired
        while True:
            try:
                from profile_manager import get_all_courses
                courses = get_all_courses(prefer_user_dir=True)
                if courses:
                    sync_google_calendar(days_ahead=7)
                    if not self.is_recording and courses[0].get("sheet_id"):
                        pending_audio = [f for f in os.listdir(watch_folder) if f.lower().endswith(('.wav', '.mp3', '.m4a'))]
                        if pending_audio:
                            sheet = SheetClient(
                                service_account_json=sa_path,
                                sheet_id=courses[0]["sheet_id"],
                                tab_name=courses[0].get("tab_name", "Sheet1"),
                            )
                            recover_pending_audio(watch_folder, self.config, sheet, processed_folder)
            except Exception as e:
                print(f"[WARN] Background calendar sync loop: {e}")

            time.sleep(20)

    @rumps.timer(1)
    def main_thread_ui_and_schedule_tick(self, _):
        """CRITICAL: Runs strictly on macOS Main NSRunLoop every 1 second.

        Safely updates UI menu items and provides real-time pulsing recording animation.
        """
        try:
            self.tick_count += 1
            now = datetime.now()
            schedule = load_schedule(purge_expired=True)
            next_event = None
            active_event = None

            for item in schedule:
                item_date_str = item.get("date")
                if not item_date_str:
                    continue
                try:
                    class_date = datetime.strptime(item_date_str, "%Y-%m-%d").date()
                    start_time = datetime.strptime(item.get("start_time"), "%H:%M").time()
                    end_time = datetime.strptime(item.get("end_time"), "%H:%M").time()
                    start_dt = datetime.combine(class_date, start_time)
                    end_dt = datetime.combine(class_date, end_time)

                    # Window: Within 2 minutes before start up to end time
                    if start_dt - timedelta(minutes=2) <= now < end_dt:
                        active_event = item
                    elif start_dt > now:
                        if next_event is None or start_dt < next_event[0]:
                            next_event = (start_dt, item)
                except Exception:
                    continue

            # Check if any FFmpeg audio recording process is actively running
            is_ffmpeg_active = False
            try:
                res = subprocess.run(["pgrep", "-f", "ffmpeg.*avfoundation"], capture_output=True, text=True)
                if res.returncode == 0 and res.stdout.strip():
                    is_ffmpeg_active = True
            except Exception:
                pass

            # Update Menu Labels & Live Stopwatch
            if self.is_recording or is_ffmpeg_active:
                if not self.recording_start_time:
                    self.recording_start_time = time.time()
                elapsed = int(max(0, time.time() - self.recording_start_time))
                mins = elapsed // 60
                secs = elapsed % 60
                
                class_title = "Class"
                if active_event:
                    class_title = active_event.get('title') or active_event.get('name') or "Class"
                
                self.title = f"🔴 Recording {class_title} ({mins:02d}:{secs:02d})"
                self.item_record_toggle.title = f"⏹️ Stop Recording ({mins:02d}:{secs:02d}) & Process"
                self.current_status = f"🔴 Live Recording Active ({mins:02d}:{secs:02d})"
            else:
                self.recording_start_time = None
                self.item_record_toggle.title = "🎙️ Start Recording Now..."
                
                # Auto-clear stuck syncing if more than 10 seconds elapsed
                if self.is_syncing and (time.time() - getattr(self, "sync_started_at", 0) > 10):
                    self.is_syncing = False

                if not self.is_session_active and not self.is_syncing:
                    self.current_status = "Active & Monitoring"
                if self.is_syncing:
                    self.title = "🔄 Syncing..."
                    self.item_status.title = "Status: 🔄 Syncing Google Calendar..."
                else:
                    self.title = "🎓 Class Tracker"

            # Check if any courses are paired yet
            from profile_manager import get_all_courses
            paired = get_all_courses(prefer_user_dir=True)
            if not paired:
                if not self.is_syncing:
                    self.title = "🎓 Class Tracker"
                    self.current_status = "Waiting for Pairing Code..."
                    self.next_class_label = "No subjects paired"
                    self.item_next_class.title = "📅 Next Class: No subjects paired"
                    self.item_paired_courses.title = "📚 Paired Subjects: None"
                    self.item_status.title = f"Status: {self.current_status}"

                if not self.first_launch_prompted and self.tick_count == 2:
                    self.first_launch_prompted = True
                    self.on_pair_subject_code(None)
                return

            if not self.is_syncing and not self.is_recording and not is_ffmpeg_active:
                self.current_status = "Active & Monitoring"
                self.item_status.title = f"Status: {self.current_status}"

            if len(paired) == 1:
                self.item_paired_courses.title = f"📚 Paired Subject: {paired[0].get('code')} (Tab: {paired[0].get('tab_name')})"
            else:
                codes_str = ", ".join(c.get("code", "CRS") for c in paired)
                self.item_paired_courses.title = f"📚 Paired Subjects: {codes_str} ({len(paired)})"

            if active_event:
                class_title = active_event.get('title') or active_event.get('name') or 'Class'
                self.next_class_label = f"{class_title} (Active Now)"
                self.item_next_class.title = f"📅 Active Class: {self.next_class_label}"
            elif next_event:
                dt, item = next_event
                diff_mins = int((dt - now).total_seconds() // 60)
                time_label = f"in {diff_mins}m" if diff_mins < 120 else dt.strftime("%a %I:%M %p")
                class_title = item.get('title') or item.get('name') or 'Class'
                self.next_class_label = f"{class_title} ({time_label})"
                self.item_next_class.title = f"📅 Next Class: {self.next_class_label}"
            else:
                self.next_class_label = "No upcoming classes"
                self.item_next_class.title = "📅 Next Class: No upcoming classes"

            self.item_status.title = f"Status: {self.current_status}"

            # Auto-trigger scheduled recording if not already prompted or active
            if active_event and not self.is_session_active and not self.is_recording and not is_ffmpeg_active:
                event_key = f"{active_event.get('date')}_{active_event.get('start_time')}_{active_event.get('title')}"
                if event_key not in self.prompted_events:
                    self.is_session_active = True
                    self.prompted_events.add(event_key)
                    self.active_event_to_record = active_event
                    threading.Thread(target=self._run_scheduled_session, args=(active_event,), daemon=True).start()

        except Exception as e:
            print(f"[WARN in main_thread_tick]: {e}")

    def on_toggle_recording(self, _):
        """Allows manual Start or Stop recording at any time with 1-click simplicity."""
        is_ffmpeg_active = False
        try:
            res = subprocess.run(["pgrep", "-f", "ffmpeg.*avfoundation"], capture_output=True, text=True)
            if res.returncode == 0 and res.stdout.strip():
                is_ffmpeg_active = True
        except Exception:
            pass

        if self.is_recording or is_ffmpeg_active:
            # Manual Stop
            if self.current_recording_proc:
                try:
                    self.current_recording_proc.send_signal(signal.SIGINT)
                except Exception:
                    pass
            try:
                subprocess.run(["pkill", "-2", "-f", "ffmpeg.*avfoundation"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                pass
            rumps.notification("Class Tracker", "Stopping Recording", "Finishing audio capture and processing class...")
        else:
            # Manual Start
            now = datetime.now()
            schedule = load_schedule(purge_expired=False)
            active_event = None
            default_title = "DAA Lecture"

            for item in schedule:
                item_date_str = item.get("date")
                if not item_date_str:
                    continue
                try:
                    class_date = datetime.strptime(item_date_str, "%Y-%m-%d").date()
                    start_time = datetime.strptime(item.get("start_time"), "%H:%M").time()
                    end_time = datetime.strptime(item.get("end_time"), "%H:%M").time()
                    start_dt = datetime.combine(class_date, start_time)
                    end_dt = datetime.combine(class_date, end_time)
                    if start_dt - timedelta(minutes=5) <= now < end_dt:
                        active_event = item
                        break
                except Exception:
                    continue

            if active_event:
                # Active class is happening right now - start immediately without prompting
                class_title = active_event.get("title") or active_event.get("name") or "DAA Lecture"
                end_time_str = active_event.get("end_time", "end")
                # Set 3-hour safety cap so the teacher can teach overtime (e.g. 66m, 75m, 90m) without cutoff
                duration_secs = 10800

                rumps.notification("Class Tracker", "Recording Started", f"Now recording {class_title}. Click 'Stop Recording' when class finishes.")
                threading.Thread(
                    target=self._run_manual_session,
                    args=(class_title, duration_secs),
                    daemon=True
                ).start()
            else:
                if schedule and len(schedule) > 0:
                    default_title = schedule[0].get("title") or schedule[0].get("name") or "DAA Lecture"

                response = rumps.Window(
                    message="Enter lecture topic or name to start recording immediately:\n(Recording will continue until you click 'Stop Recording')",
                    title="🎙️ Manual Class Recording",
                    default_text=default_title,
                    ok="Start Recording",
                    cancel="Cancel",
                    dimensions=(320, 24)
                ).run()

                if response.clicked:
                    manual_title = response.text.strip() or "DAA_Lecture"
                    threading.Thread(
                        target=self._run_manual_session,
                        args=(manual_title, 10800),  # 3 hours safety ceiling
                        daemon=True
                    ).start()

    def _run_scheduled_session(self, event):
        """Worker thread for scheduled prompt, recording, and processing."""
        try:
            self.current_status = "Waiting for consent..."
            class_date = datetime.strptime(event.get("date"), "%Y-%m-%d").date()
            start_time = datetime.strptime(event.get("start_time"), "%H:%M").time()
            end_time = datetime.strptime(event.get("end_time"), "%H:%M").time()
            end_dt = datetime.combine(class_date, end_time)
            # Calculate exact remaining duration so recording automatically stops at the scheduled calendar end time
            duration_secs = int(max(60, (end_dt - datetime.now()).total_seconds()))

            class_title = event.get("title") or event.get("name") or "DAA Lecture"

            consent = prompt_start_dialog(
                class_name=class_title,
                start_time=event.get("start_time"),
                end_time=event.get("end_time")
            )

            if not consent:
                self.current_status = "Skipped (waiting 1m before retry)..."
                time.sleep(60)  # Wait 1 minute

                now = datetime.now()
                if now < end_dt:
                    rem_secs = int((end_dt - now).total_seconds())
                    if rem_secs >= 60:
                        retry_consent = prompt_retry_dialog(
                            class_name=class_title,
                            end_time=event.get("end_time")
                        )
                        if retry_consent:
                            self._execute_audio_capture_and_pipeline(class_title, rem_secs)
                            return

                self.current_status = "Dismissed for today"
                time.sleep(60)
                return

            self._execute_audio_capture_and_pipeline(class_title, duration_secs)

        except Exception as e:
            print(f"[ERROR in scheduled session]: {e}")
        finally:
            self.is_recording = False
            self.is_session_active = False
            self.current_status = "Active & Monitoring"

    def _run_manual_session(self, class_title: str, duration_secs: int):
        """Worker thread for ad-hoc or recovery manual recording."""
        try:
            self.is_session_active = True
            self._execute_audio_capture_and_pipeline(class_title, duration_secs)
        except Exception as e:
            print(f"[ERROR in manual session]: {e}")
        finally:
            self.is_recording = False
            self.is_session_active = False
            self.current_status = "Active & Monitoring"

    def _execute_audio_capture_and_pipeline(self, class_title: str, duration_secs: int):
        """Core audio recording and pipeline execution with resilient supervisor loop."""
        title_clean = class_title.replace(" ", "_")
        self.is_recording = True
        self.recording_start_time = time.time()
        self.current_status = f"Recording {title_clean}"

        watch_folder = os.path.expanduser(self.config.get("WATCH_FOLDER", "~/ClassAudio/incoming"))
        processed_folder = os.path.abspath(os.path.join(watch_folder, "..", "processed"))
        os.makedirs(watch_folder, exist_ok=True)
        os.makedirs(processed_folder, exist_ok=True)

        today_str = datetime.now().strftime("%Y-%m-%d")
        base_name = f"{title_clean}_{today_str}"
        audio_filename = f"{base_name}.mp3"
        audio_path = os.path.join(watch_folder, audio_filename)
        temp_recording_path = os.path.join(watch_folder, f"{base_name}_{int(time.time())}.recording")

        ffmpeg_bin = get_ffmpeg_bin()
        cmd = [
            ffmpeg_bin,
            "-nostdin",
            "-y",
            "-f", "avfoundation",
            "-i", ":0",
            "-t", str(duration_secs),
            "-c:a", "libmp3lame",
            "-b:a", "32k",
            "-ar", "16000",
            "-ac", "1",
            "-f", "mp3",
            temp_recording_path,
        ]

        # Resilient Supervisor Loop: Handles macOS TCC permission delay gracefully
        started_cleanly = False
        max_init_attempts = 20  # Up to 40 seconds to allow user to click macOS permission dialog

        for attempt in range(max_init_attempts):
            self.current_recording_proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=os.environ.copy(),
            )
            
            # Check if process stays alive for 2 seconds (proof that microphone is open and capturing)
            time.sleep(2.0)
            if self.current_recording_proc.poll() is None:
                started_cleanly = True
                self.is_recording = True
                self.current_status = f"Recording {title_clean}"
                print(f"[INFO] Audio capture started cleanly on attempt {attempt+1}.")
                break
            else:
                # Process exited (e.g. while user is reviewing macOS mic permission prompt)
                print(f"[INFO] Awaiting microphone access confirmation (attempt {attempt+1}/{max_init_attempts})...")
                time.sleep(1.0)

        if started_cleanly and self.current_recording_proc:
            try:
                self.current_recording_proc.wait()
            except Exception:
                pass
            finally:
                self.current_recording_proc = None

        self.is_recording = False
        self.current_status = "Transcribing & Auditing..."

        # Atomically promote finished recording to watch folder audio_path
        if os.path.exists(temp_recording_path) and os.path.getsize(temp_recording_path) > 1000:
            try:
                if os.path.exists(audio_path):
                    try:
                        os.remove(audio_path)
                    except Exception:
                        pass
                shutil.move(temp_recording_path, audio_path)
            except Exception as me:
                print(f"[WARN] Move error: {me}")
                audio_path = temp_recording_path

        if os.path.exists(audio_path) and os.path.getsize(audio_path) > 1000:
            sa_path = os.path.join(HERE, self.config.get("GOOGLE_SERVICE_ACCOUNT_JSON", "config/service_account.json")) \
                if not os.path.isabs(self.config.get("GOOGLE_SERVICE_ACCOUNT_JSON", "config/service_account.json")) \
                else self.config.get("GOOGLE_SERVICE_ACCOUNT_JSON", "config/service_account.json")

            # Dynamically route to the specific subject tab, sheet, curriculum, and teacher email
            from profile_manager import get_all_courses
            paired_courses = get_all_courses(prefer_user_dir=True)
            default_c = paired_courses[0] if paired_courses else {}

            active_ev = self.active_event_to_record or {}
            target_sheet_id = active_ev.get("sheet_id") or default_c.get("sheet_id", "")
            target_tab_name = active_ev.get("tab_name") or default_c.get("tab_name", "Sheet1")
            target_cid = (active_ev.get("course_id") or default_c.get("id") or default_c.get("code") or "course").lower()

            course_cfg = dict(self.config)
            course_cfg["GOOGLE_SHEET_ID"] = target_sheet_id
            course_cfg["SHEET_TAB_NAME"] = target_tab_name
            target_teacher_email = (
                active_ev.get("teacher_email")
                or next((c.get("teacher_email") for c in paired_courses if (c.get("id") or "").lower() == target_cid or (c.get("code") or "").lower() == target_cid), None)
                or default_c.get("teacher_email", "")
            )
            if target_teacher_email:
                course_cfg["TEACHER_EMAIL"] = target_teacher_email

            cand_curr = os.path.join(HERE, "config", f"curriculum_{target_cid}.json")
            user_curr = os.path.expanduser(f"~/.classtracker/config/curriculum_{target_cid}.json")
            if os.path.exists(cand_curr):
                course_cfg["CURRICULUM_PATH"] = cand_curr
            elif os.path.exists(user_curr):
                course_cfg["CURRICULUM_PATH"] = user_curr

            try:
                sheet = SheetClient(
                    service_account_json=sa_path,
                    sheet_id=target_sheet_id,
                    tab_name=target_tab_name,
                )
                print(f"[PIPELINE] Routing recording to Course: {target_cid.upper()} | Tab: {target_tab_name} | Sheet: {target_sheet_id}")
                process_file(audio_path, course_cfg, sheet, processed_folder, auto_confirm=True)
            except Exception as pe:
                print(f"[ERROR in pipeline processing]: {pe}")

        load_schedule(purge_expired=True)
        self.current_status = "Active & Monitoring"

    def on_sync_calendar(self, _):
        """User clicked 'Sync Google Calendar Now'."""
        if self.is_syncing:
            return
        self.is_syncing = True
        self.sync_started_at = time.time()
        try:
            self.item_sync_cal.title = "⏳ Contacting Google Calendar..."
            self.title = "🔄 Syncing..."
            self.current_status = "Syncing Google Calendar..."
            self.item_status.title = "Status: 🔄 Syncing with Google Calendar..."
            threading.Thread(target=self._worker_sync_calendar, daemon=True).start()
        except Exception as e:
            print(f"[ERROR in on_sync_calendar]: {e}")
            self.is_syncing = False
            self.title = "🎓 Class Tracker"
            self.item_sync_cal.title = "🔄 Sync Google Calendar Now"

    def _worker_sync_calendar(self):
        try:
            from profile_manager import get_all_courses
            paired = get_all_courses(prefer_user_dir=True)
            if not paired:
                applescript = 'display dialog "No Subjects Paired Yet 🎓\\n\\nPlease connect a course using your Pairing Code first so Class Tracker knows which classes to sync from your Google Calendar." with title "Class Tracker · Calendar Sync" buttons {"OK"} default button "OK" with icon note giving up after 6'
                try:
                    subprocess.run(["osascript", "-e", applescript], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=7)
                except Exception:
                    pass
                return

            bot_email, events = sync_google_calendar(days_ahead=7)
            now_str = datetime.now().strftime("%-I:%M %p")
            self.item_sync_cal.title = f"🔄 Sync Google Calendar Now (Last: {now_str})"

            paired_codes = ", ".join(c.get("code", "Course") for c in paired)

            if events and len(events) > 0:
                summary_lines = []
                for ev in events[:4]:
                    summary_lines.append(f"• {ev.get('title')} ({ev.get('date')} {ev.get('start_time')} - {ev.get('end_time')})")
                details = "\\n".join(summary_lines)
                applescript = f'display dialog "Google Calendar Synced! ✅\\n\\nFound {len(events)} upcoming class(es) for {paired_codes}:\\n\\n{details}" with title "Class Tracker · Calendar Sync" buttons {{"OK"}} default button "OK" with icon note giving up after 5'
                try:
                    subprocess.run(["osascript", "-e", applescript], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=6)
                except Exception:
                    pass
            else:
                applescript = f'display dialog "Google Calendar Synced! 📅\\n\\n0 upcoming classes found for paired subject(s): {paired_codes}.\\n\\nTips:\\n1. Ensure event title in Google Calendar contains \'{paired_codes}\'.\\n2. Ensure your Google Calendar is shared with:\\n{bot_email}" with title "Class Tracker · Calendar Sync" buttons {{"OK"}} default button "OK" with icon note giving up after 6'
                try:
                    subprocess.run(["osascript", "-e", applescript], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=7)
                except Exception:
                    pass

        except Exception as e:
            print(f"[WARN calendar sync]: {e}")
            applescript_err = f'display dialog "Calendar Sync Error:\\n\\n{e}" with title "Class Tracker · Sync Notice" buttons {{"OK"}} default button "OK" with icon caution giving up after 6'
            try:
                subprocess.run(["osascript", "-e", applescript_err], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=7)
            except Exception:
                pass
        finally:
            self.is_syncing = False
            self.current_status = "Active & Monitoring"
            self.title = "🎓 Class Tracker"
            if not self.item_sync_cal.title.startswith("🔄 Sync Google Calendar Now (Last:"):
                self.item_sync_cal.title = "🔄 Sync Google Calendar Now"


    def _refresh_paired_courses_menu(self):
        """Updates the 'Paired Subjects' menu title and state."""
        try:
            from profile_manager import get_all_courses
            courses = get_all_courses(prefer_user_dir=True)
            if not courses:
                self.item_paired_courses.title = "📚 Paired Subjects: None"
                self.current_status = "Waiting for Pairing Code..."
                self.next_class_label = "No subjects paired yet"
                self.item_status.title = "Status: Waiting for Pairing Code..."
                self.item_next_class.title = "📅 Next Class: No subjects paired yet"
                return
            if len(courses) == 1:
                c = courses[0]
                code = c.get("code", "CRS")
                tab = c.get("tab_name", code)
                self.item_paired_courses.title = f"📚 Paired Subject: {code} (Tab: {tab})"
            else:
                codes_str = ", ".join(c.get("code", "CRS") for c in courses)
                self.item_paired_courses.title = f"📚 Paired Subjects: {codes_str} ({len(courses)})"
            self.current_status = "Active & Monitoring"
            self.item_status.title = f"Status: {self.current_status}"
        except Exception as e:
            print(f"[WARN] Error refreshing paired courses: {e}")

    def on_show_paired_subjects(self, _):
        """Displays details of all currently paired subjects in a clean dialog."""
        from profile_manager import get_all_courses
        courses = get_all_courses(prefer_user_dir=True)
        if not courses:
            applescript_none = 'display dialog "No Subjects Paired Yet 🎓\\n\\nClick \'Pair New Subject via Code...\' to connect your first course." with title "Class Tracker · Paired Subjects" buttons {"OK"} default button "OK" with icon note giving up after 15'
            try:
                subprocess.run(["osascript", "-e", applescript_none], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=16)
            except Exception:
                pass
            return

        lines = []
        for i, c in enumerate(courses, 1):
            name = c.get("name", "Course")
            code = c.get("code", "CRS")
            tab = c.get("tab_name", code)
            count = c.get("curriculum_count") or len(c.get("units", [])) or 0
            code_str = c.get("pairing_code", "N/A")
            lines.append(f"{i}. 🎓 {code}: {name}\\n   • Sheet Tab: {tab}\\n   • Curriculum Scope: {count} Units\\n   • Pairing Code: {code_str}")

        msg = f"Connected Subjects ({len(courses)} Active):\\n\\n" + "\\n\\n".join(lines) + "\\n\\nClass Tracker is actively scanning your Google Calendar for upcoming classes."

        applescript = f'display dialog "{msg}" with title "Class Tracker · Paired Subjects" buttons {{"Add Another Subject", "OK"}} default button "OK" with icon note'
        try:
            res = subprocess.run(["osascript", "-e", applescript], capture_output=True, text=True, timeout=30)
            if "Add Another Subject" in res.stdout:
                self.on_pair_subject_code(None)
        except Exception as e:
            print(f"[WARN] Paired subjects dialog: {e}")

    def on_pair_subject_code(self, _):
        """Prompts instructor for pairing code using native macOS Cocoa window and activates the new subject."""
        try:
            print("[INFO] on_pair_subject_code invoked.")
            portal_file = os.path.expanduser("~/.classtracker/config/portal_url.txt")
            saved_portal = self.config.get("PORTAL_URL") or ""
            if not saved_portal and os.path.exists(portal_file):
                try:
                    with open(portal_file, "r") as pf:
                        p = pf.read().strip()
                        if p:
                            saved_portal = p
                except Exception:
                    pass
            if not saved_portal:
                saved_portal = "https://class-tracker-pgo5.onrender.com"

            # Check clipboard for a VALID pairing code (e.g. DAA-0183) or pairing URL.
            # Avoid picking up arbitrary text, git diffs, multi-line snippets, or sentences.
            clip_code = ""
            clip_portal = None
            try:
                res_clip = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=2)
                raw_clip = res_clip.stdout.strip()
                # Must be single-line and concise
                if raw_clip and "\n" not in raw_clip and len(raw_clip) <= 120:
                    # Check 1: Full pairing URL (e.g. https://portal.com/pair/DAA-0183 or /api/pair/DAA-0183)
                    url_m = re.search(r'^(https?://[^/\s]+(?::\d+)?)(?:/api)?/pair/([A-Za-z0-9_-]+)/?$', raw_clip, re.IGNORECASE)
                    if url_m:
                        clip_portal = url_m.group(1).rstrip("/")
                        clip_code = url_m.group(2).upper()
                    # Check 2: Standard pairing code like DAA-0183, OS-7319, CS101-1234 (must have exactly 1 hyphen)
                    elif re.fullmatch(r'[A-Za-z0-9]{2,8}-[A-Za-z0-9]{3,6}', raw_clip):
                        clip_code = raw_clip.upper()
                    # Check 3: Short clean course code like DAA, OS, CS101 (2-8 uppercase letters + optional numbers)
                    elif re.fullmatch(r'[A-Za-z]{2,8}\d{0,4}', raw_clip) and not raw_clip.isdigit():
                        clip_code = raw_clip.upper()
            except Exception:
                pass

            # If clipboard contained a valid pairing code, use it. Otherwise, default to empty string
            # so user isn't forced to delete placeholder text or accidental clipboard snippets.
            default_input = clip_code or ""

            # Use native macOS rumps.Window dialog (guaranteed native Cocoa event loop support)
            window = rumps.Window(
                message="Enter your Course Pairing Code (e.g. DAA-0183) or Course Code (DAA):",
                title="Class Tracker Activation 🎓",
                default_text=default_input,
                ok="Connect Course",
                cancel="Cancel",
                dimensions=(320, 24)
            )
            response = window.run()
            if not response.clicked:
                print("[INFO] User cancelled pairing window.")
                return

            raw_input = response.text.strip()
            print(f"[INFO] User entered pairing text: {raw_input}")
            if not raw_input:
                rumps.alert(
                    title="Class Tracker Notice",
                    message="Pairing code cannot be blank.\n\nPlease enter your course code (e.g. DAA-0183) to connect."
                )
                return

            extracted_portal = None
            code = raw_input.strip()

            # Handle case where user pasted a full URL into the input dialog
            if "://" in raw_input:
                url_match = re.search(r'(https?://[^\s/]+(?::\d+)?)', raw_input)
                if url_match:
                    extracted_portal = url_match.group(1).rstrip("/")
                code_match = re.search(r'pair/([A-Za-z0-9_-]+)', raw_input, re.IGNORECASE)
                if code_match:
                    code = code_match.group(1)
                else:
                    path_part = raw_input.rstrip("/").split("?")[0].split("/")[-1].strip()
                    if path_part and not path_part.startswith("http"):
                        code = path_part

            # Sanitize code: remove inner whitespace and uppercase
            code = code.replace(" ", "").upper()

            # Strict validation: prevent random sentences, code diffs, or invalid symbols
            if not re.fullmatch(r'[A-Za-z0-9_-]{2,20}', code):
                rumps.alert(
                    title="Invalid Pairing Code",
                    message=f"'{raw_input[:35]}' does not match a valid course code format.\n\nPlease enter a course code like 'DAA' or pairing code like 'DAA-0183'."
                )
                return

            candidate_portals = []
            if extracted_portal:
                candidate_portals.append(extracted_portal)
            if clip_portal and clip_portal not in candidate_portals:
                candidate_portals.append(clip_portal)
            if saved_portal and saved_portal not in candidate_portals:
                candidate_portals.append(saved_portal)
            if "https://class-tracker-pgo5.onrender.com" not in candidate_portals:
                candidate_portals.append("https://class-tracker-pgo5.onrender.com")
            if "http://localhost:8080" not in candidate_portals:
                candidate_portals.append("http://localhost:8080")

            import urllib.request
            import urllib.parse
            import ssl
            try:
                import certifi
                ssl_ctx = ssl.create_default_context(cafile=certifi.where())
            except Exception:
                ssl_ctx = ssl.create_default_context()
                ssl_ctx.check_hostname = False
                ssl_ctx.verify_mode = ssl.CERT_NONE

            safe_code = urllib.parse.quote(code)
            data = None
            successful_portal = None
            last_err = ""
            is_404 = False

            for portal in candidate_portals:
                try:
                    endpoint = f"{portal.rstrip('/')}/api/pair/{safe_code}"
                    print(f"[INFO] Connecting to {endpoint} ...")
                    req = urllib.request.Request(endpoint, headers={"User-Agent": "ClassTracker-Client/1.0"})
                    # 40s timeout for Render.com spin-up from cold sleep
                    with urllib.request.urlopen(req, context=ssl_ctx, timeout=40) as resp:
                        if resp.status == 200:
                            body = resp.read().decode("utf-8")
                            data = json.loads(body)
                            if data.get("success"):
                                successful_portal = portal
                                print(f"[INFO] Connection succeeded from {portal}: {data.get('name')}")
                                break
                except urllib.error.HTTPError as he:
                    if he.code == 404:
                        is_404 = True
                        try:
                            err_body = json.loads(he.read().decode("utf-8"))
                            last_err = err_body.get("error", f"Course '{code}' not found (404).")
                        except Exception:
                            last_err = f"Course '{code}' not found (404)."
                    else:
                        last_err = f"Server returned HTTP {he.code}"
                    print(f"[WARN] Connection to {portal} returned HTTP {he.code}: {last_err}")
                except urllib.error.URLError as ue:
                    if "timed out" in str(ue).lower():
                        last_err = "Server connection timed out (Render cloud service may be waking up)."
                    else:
                        last_err = f"Network connection failed: {ue.reason}"
                    print(f"[WARN] Connection to {portal} failed: {last_err}")
                except Exception as e:
                    last_err = str(e)
                    print(f"[WARN] Connection to {portal} failed: {e}")
                    continue

            if not data or not data.get("success"):
                if is_404:
                    err_msg = f"Course code '{code}' was not found.\n\nPlease check your web dashboard (https://class-tracker-pgo5.onrender.com) for the active pairing code."
                elif "timed out" in last_err.lower():
                    err_msg = f"Connecting to the server timed out.\n\nRender free servers take ~30 seconds to wake up from idle sleep. Please wait 10 seconds and try again!"
                else:
                    err_msg = (data or {}).get("error", f"Could not connect with code '{code}'.\n\nDetail: {last_err}")
                print(f"[WARN] Pairing unsuccessful: {err_msg}")
                rumps.alert(
                    title="Class Tracker Notice",
                    message=err_msg
                )
                return

            # Save successful portal address for future pairing requests
            if successful_portal:
                try:
                    user_cfg_dir = os.path.expanduser("~/.classtracker/config")
                    os.makedirs(user_cfg_dir, exist_ok=True)
                    with open(portal_file, "w") as pf:
                        pf.write(successful_portal)
                except Exception:
                    pass

            from profile_manager import save_user_paired_course
            cid = (data.get("course_id") or code.lower()).lower()
            units = data.get("units", [])
            user_cfg_dir = os.path.expanduser("~/.classtracker/config")
            os.makedirs(user_cfg_dir, exist_ok=True)
            if units:
                curr_path = os.path.join(user_cfg_dir, f"curriculum_{cid}.json")
                with open(curr_path, "w", encoding="utf-8") as cf:
                    json.dump({"units": units}, cf, indent=2)

            course_obj = {
                "id": cid,
                "name": data.get("name", code),
                "code": data.get("code", code),
                "sheet_id": data.get("sheet_id", self.config.get("GOOGLE_SHEET_ID")),
                "tab_name": data.get("tab_name", code),
                "teacher_email": data.get("teacher_email", ""),
                "calendar_id": data.get("calendar_id", ""),
                "pairing_code": code,
                "units": units,
                "curriculum_count": data.get("curriculum_count", len(units)),
            }
            save_user_paired_course(course_obj)
            print(f"[INFO] Successfully saved user course: {course_obj['name']} ({course_obj['code']})")

            self.current_status = "Active & Monitoring"
            self.item_status.title = f"Status: {self.current_status}"
            self._refresh_paired_courses_menu()
            threading.Thread(target=sync_google_calendar, kwargs={"days_ahead": 7}, daemon=True).start()

            course_name = data.get("name", code)
            tab_name = data.get("tab_name", code)
            curr_count = data.get("curriculum_count", 0)
            rumps.alert(
                title="Course Connected! 🎉",
                message=f"Course '{course_name}' ({code}) is now active!\n\nConnected Sheet Tab: {tab_name}\nUnits Loaded: {curr_count}\n\nClass Tracker is now actively monitoring your Google Calendar for upcoming {code} lectures."
            )
        except Exception as e:
            print(f"[ERROR in on_pair_subject_code]: {e}")
            import traceback
            traceback.print_exc()
            rumps.alert(
                title="Class Tracker Notice",
                message=f"Error activating course:\n\n{e}"
            )

    def on_reset_courses(self, _):
        """Allows instructor to wipe all paired courses and return the app to a clean, bare state."""
        applescript_confirm = 'display dialog "Disconnect all paired subjects?\\n\\nThis will reset Class Tracker back to a completely bare installation with 0 paired courses." with title "Class Tracker · Reset" buttons {"Cancel", "Disconnect All"} default button "Cancel" with icon caution'
        try:
            res = subprocess.run(["osascript", "-e", applescript_confirm], capture_output=True, text=True, timeout=15)
            if "Disconnect All" not in res.stdout:
                return
        except Exception:
            return

        from profile_manager import clear_user_courses
        clear_user_courses()
        user_cfg_dir = os.path.expanduser("~/.classtracker/config")
        try:
            sched_file = os.path.join(user_cfg_dir, "schedule.json")
            if os.path.exists(sched_file):
                with open(sched_file, "w", encoding="utf-8") as f:
                    json.dump([], f)
        except Exception:
            pass

        self._refresh_paired_courses_menu()
        rumps.notification("Class Tracker", "Reset Complete", "All courses disconnected. App is in clean bare mode.")

    def on_open_sheet(self, _):
        """Opens the Google Sheet directly for the paired course."""
        from profile_manager import get_all_courses
        courses = get_all_courses(prefer_user_dir=True)
        if not courses:
            applescript_none = 'display dialog "No Subjects Paired Yet 🎓\\n\\nPlease connect a course using your Pairing Code first to link your Google Sheet." with title "Class Tracker" buttons {"OK"} default button "OK" with icon note giving up after 8'
            try:
                subprocess.run(["osascript", "-e", applescript_none], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=9)
            except Exception:
                pass
            return

        target_course = courses[0]
        sheet_id = target_course.get("sheet_id", "")
        if sheet_id:
            url = f"https://docs.google.com/spreadsheets/d/{sheet_id}"
            webbrowser.open(url)
        else:
            applescript_err = 'display dialog "Google Sheet ID not found for this course profile." with title "Class Tracker" buttons {"OK"} default button "OK" with icon caution giving up after 8'
            try:
                subprocess.run(["osascript", "-e", applescript_err], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=9)
            except Exception:
                pass

    def on_open_audio_folder(self, _):
        watch_folder = os.path.expanduser(self.config.get("WATCH_FOLDER", "~/ClassAudio/incoming"))
        os.makedirs(watch_folder, exist_ok=True)
        subprocess.run(["open", watch_folder])

    def on_quit(self, _):
        if self.is_recording:
            res = rumps.alert(
                title="Recording in Progress",
                message="A class is currently being recorded. Quitting will stop the recording. Are you sure?",
                ok="Quit Anyway",
                cancel="Stay"
            )
            if res != 1:
                return
            if self.current_recording_proc:
                try:
                    self.current_recording_proc.send_signal(signal.SIGINT)
                except Exception:
                    pass
        rumps.quit_application()


if __name__ == "__main__":
    app = ClassTrackerMenuBarApp()
    app.run()
