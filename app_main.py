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
import signal
import webbrowser
import threading
import subprocess
from datetime import datetime, timedelta

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
            title="🎓 DAA Tracker",
            quit_button=None
        )

        self.config = load_config()
        self.is_recording = False
        self.is_syncing = False
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
        self.item_open_sheet = rumps.MenuItem("📊 Open DAA Google Sheet", callback=self.on_open_sheet)
        self.item_open_audio_dir = rumps.MenuItem("📁 Open Recordings Folder", callback=self.on_open_audio_folder)
        self.item_separator_1 = rumps.separator
        self.item_quit = rumps.MenuItem("❌ Quit Class Tracker", callback=self.on_quit)

        self.menu = [
            self.item_status,
            self.item_next_class,
            self.item_record_toggle,
            self.item_sync_cal,
            self.item_open_sheet,
            self.item_open_audio_dir,
            self.item_separator_1,
            self.item_quit
        ]

        # Initial background calendar sync and recurring auto-sync loop
        threading.Thread(target=self._background_calendar_sync_loop, daemon=True).start()

    def _background_calendar_sync_loop(self):
        """Runs initial recovery sweep and continuously syncs Google Calendar every 60 seconds."""
        time.sleep(1)
        watch_folder = os.path.expanduser(self.config.get("WATCH_FOLDER", "~/ClassAudio/incoming"))
        processed_folder = os.path.abspath(os.path.join(watch_folder, "..", "processed"))
        os.makedirs(watch_folder, exist_ok=True)
        os.makedirs(processed_folder, exist_ok=True)

        sa_path = os.path.join(HERE, self.config.get("GOOGLE_SERVICE_ACCOUNT_JSON", "config/service_account.json")) \
            if not os.path.isabs(self.config.get("GOOGLE_SERVICE_ACCOUNT_JSON", "config/service_account.json")) \
            else self.config.get("GOOGLE_SERVICE_ACCOUNT_JSON", "config/service_account.json")

        try:
            sheet = SheetClient(
                service_account_json=sa_path,
                sheet_id=self.config["GOOGLE_SHEET_ID"],
                tab_name=self.config["SHEET_TAB_NAME"],
            )
            recover_pending_audio(watch_folder, self.config, sheet, processed_folder)
        except Exception as e:
            print(f"[WARN] Initial recovery sweep note: {e}")

        # Continuous background sync & self-healing recovery every 60 seconds
        while True:
            try:
                sync_google_calendar(days_ahead=7)
            except Exception as e:
                print(f"[WARN] Background calendar auto-sync: {e}")

            try:
                # Automatically sweep and recover any pending/interrupted audio only when not actively recording
                if not self.is_recording:
                    recover_pending_audio(watch_folder, self.config, sheet, processed_folder)
            except Exception as e:
                print(f"[WARN] Periodic recovery note: {e}")

            time.sleep(60)

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
                
                class_title = "DAA"
                if active_event:
                    class_title = active_event.get('title') or active_event.get('name') or "DAA"
                
                self.title = f"🔴 Recording {class_title} ({mins:02d}:{secs:02d})"
                self.item_record_toggle.title = f"⏹️ Stop Recording ({mins:02d}:{secs:02d}) & Process"
                self.current_status = f"🔴 Live Recording Active ({mins:02d}:{secs:02d})"
            else:
                self.recording_start_time = None
                self.item_record_toggle.title = "🎙️ Start Recording Now..."
                if self.is_syncing:
                    self.title = "🔄 Syncing..."
                else:
                    self.title = "🎓 DAA Tracker"

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
                end_time = datetime.strptime(active_event.get("end_time"), "%H:%M").time()
                class_date = datetime.strptime(active_event.get("date"), "%Y-%m-%d").date()
                end_dt = datetime.combine(class_date, end_time)
                duration_secs = int(max(60, (end_dt - now).total_seconds()))

                rumps.notification("Class Tracker", "Recording Started", f"Now recording {class_title} until {active_event.get('end_time')}")
                threading.Thread(
                    target=self._run_manual_session,
                    args=(class_title, duration_secs),
                    daemon=True
                ).start()
            else:
                if schedule and len(schedule) > 0:
                    default_title = schedule[0].get("title") or schedule[0].get("name") or "DAA Lecture"

                response = rumps.Window(
                    message="Enter lecture topic or name to start recording immediately:",
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
                        args=(manual_title, 5400),  # Default 90 minutes max duration
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

            try:
                sheet = SheetClient(
                    service_account_json=sa_path,
                    sheet_id=self.config["GOOGLE_SHEET_ID"],
                    tab_name=self.config["SHEET_TAB_NAME"],
                )
                process_file(audio_path, self.config, sheet, processed_folder, auto_confirm=True)
            except Exception as pe:
                print(f"[ERROR in pipeline processing]: {pe}")

        load_schedule(purge_expired=True)
        self.current_status = "Active & Monitoring"

    def on_sync_calendar(self, _):
        """User clicked 'Sync Google Calendar Now'."""
        if self.is_syncing:
            return
        self.is_syncing = True
        self.title = "🔄 Syncing..."
        self.current_status = "Syncing Google Calendar..."
        self.item_status.title = "Status: Syncing Google Calendar..."
        threading.Thread(target=self._worker_sync_calendar, daemon=True).start()

    def _worker_sync_calendar(self):
        try:
            bot_email, events = sync_google_calendar(days_ahead=7)
            if events and len(events) > 0:
                rumps.notification(
                    title="Class Tracker",
                    subtitle="Calendar Synced",
                    message=f"Successfully synced {len(events)} upcoming class(es)."
                )
            else:
                rumps.notification(
                    title="Class Tracker",
                    subtitle="Google Calendar Connected",
                    message=f"0 upcoming classes found.\nShare your Google Calendar with:\n{bot_email}"
                )
        except Exception as e:
            print(f"[WARN calendar sync]: {e}")
            rumps.notification(
                title="Class Tracker",
                subtitle="Sync Notice",
                message=f"Could not connect to calendar: {e}"
            )
        finally:
            self.is_syncing = False
            self.current_status = "Active & Monitoring"

    def on_open_sheet(self, _):
        sheet_id = self.config.get("GOOGLE_SHEET_ID")
        if sheet_id:
            url = f"https://docs.google.com/spreadsheets/d/{sheet_id}"
            webbrowser.open(url)
        else:
            rumps.alert("Google Sheet ID not found in configuration.")

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
