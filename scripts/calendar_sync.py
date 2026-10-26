"""Google Calendar Auto-Sync for Class Tracker.

Connects to Google Calendar via the shared Service Account, fetches all upcoming
class events, and automatically populates config/schedule.json.
"""

import os
import sys
import json
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional

from dotenv import dotenv_values
from google.oauth2.service_account import Credentials
from google.auth.transport.requests import AuthorizedSession

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = getattr(sys, '_MEIPASS', os.path.dirname(HERE))

if HERE not in sys.path:
    sys.path.insert(0, HERE)

SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events.readonly",
]


def load_config() -> dict:
    cfg = {}
    client_env = os.path.join(PROJECT_ROOT, "config", ".env.client")
    if os.path.exists(client_env):
        cfg.update(dotenv_values(client_env))
    base_env = os.path.join(PROJECT_ROOT, "config", ".env")
    if os.path.exists(base_env):
        cfg.update(dotenv_values(base_env))
    user_env = os.path.expanduser("~/.classtracker/config/.env")
    if os.path.exists(user_env):
        cfg.update(dotenv_values(user_env))
    portal_file = os.path.expanduser("~/.classtracker/config/portal_url.txt")
    if os.path.exists(portal_file):
        try:
            with open(portal_file, "r") as pf:
                p = pf.read().strip()
                if p:
                    cfg["PORTAL_URL"] = p
        except Exception:
            pass
    if not cfg.get("PORTAL_URL"):
        cfg["PORTAL_URL"] = "https://class-tracker-pgo5.onrender.com"
    return cfg


def get_service_account_path(config: dict) -> str:
    sa_path = config.get("GOOGLE_SERVICE_ACCOUNT_JSON", "config/service_account.json")
    if not os.path.isabs(sa_path):
        sa_path = os.path.join(PROJECT_ROOT, sa_path)
    return sa_path


import re

def is_target_course_event(summary: str, course_filter: Optional[str] = None) -> bool:
    """Checks if an event summary corresponds to any active paired course."""
    if not summary or not summary.strip():
        return False
    
    s = summary.strip().lower()
    if course_filter:
        keywords = [k.strip().lower() for k in course_filter.split(",") if k.strip()]
        for kw in keywords:
            if kw and re.search(r"\b" + re.escape(kw) + r"\b", s):
                return True
        return False

    return match_course_for_event(summary) is not None


def match_course_for_event(summary: str, courses: Optional[List[Dict[str, Any]]] = None) -> Optional[Dict[str, Any]]:
    """Matches an event summary to one of the configured course profiles."""
    if not summary or not summary.strip():
        return None
    
    if courses is None:
        try:
            from profile_manager import get_all_courses
            courses = get_all_courses(prefer_user_dir=True)
        except Exception:
            courses = []

    if not courses:
        return None

    s = summary.strip().lower()

    # Check all registered courses in user profile
    for c in courses:
        code = (c.get("code") or "").strip().lower()
        name = (c.get("name") or "").strip().lower()
        tab = (c.get("tab_name") or "").strip().lower()

        # Disambiguate DAA vs DSA
        if code == "daa" and "dsa" in s and "daa" not in s:
            continue
        if code == "dsa" and "daa" in s and "dsa" not in s:
            continue

        if code and re.search(r"\b" + re.escape(code) + r"\b", s):
            return c
        if tab and re.search(r"\b" + re.escape(tab) + r"\b", s):
            return c
        if name:
            clean_name = re.sub(r"\(.*?\)", "", name).strip()
            if len(clean_name) >= 3 and clean_name in s:
                return c

    return None


def sync_google_calendar(days_ahead: int = 7):
    """Fetches upcoming events from all calendars shared with the service account
    and updates config/schedule.json automatically with all matched course events.
    
    Returns: (bot_email, synced_events_list)
    """
    config = load_config()
    sa_path = get_service_account_path(config)

    if not os.path.exists(sa_path):
        print(f"[WARN] Service account not found at {sa_path}. Skipping calendar sync.")
        return ("", [])

    try:
        creds = Credentials.from_service_account_file(sa_path, scopes=SCOPES)
        bot_email = creds.service_account_email
        session = AuthorizedSession(creds)

        registered_courses = []
        try:
            from profile_manager import get_all_courses
            registered_courses = get_all_courses(prefer_user_dir=True)
        except Exception:
            pass

        # If zero courses are paired, app is completely bare-naked: no calendar events synced
        if not registered_courses:
            print("[INFO] No courses paired yet. Clean bare state — skipping calendar sync.")
            data_dir = os.path.expanduser("~/.classtracker/config")
            os.makedirs(data_dir, exist_ok=True)
            schedule_path = os.path.join(data_dir, "schedule.json")
            with open(schedule_path, "w", encoding="utf-8") as f:
                json.dump([], f)
            return (bot_email, [])

        synced_events = []
        seen_keys = set()

        now = datetime.now(timezone.utc)
        time_min = (now - timedelta(hours=2)).isoformat()
        time_max = (now + timedelta(days=days_ahead)).isoformat()

        # Collect only calendars explicitly belonging to the paired courses
        cal_ids = set()
        from profile_manager import clean_calendar_id

        for c in registered_courses:
            raw_cal = (c.get("calendar_id") or "").strip()
            cal_id = clean_calendar_id(raw_cal)
            if cal_id:
                cal_ids.add(cal_id)
            t_email = (c.get("teacher_email") or "").strip()
            if t_email:
                cal_ids.add(t_email)

        # If instructor did not specify a calendar, fallback to developer/sender email
        if not cal_ids:
            for key in ["DEVELOPER_EMAIL", "GMAIL_SENDER", "TEACHER_EMAIL"]:
                val = config.get(key, "")
                if val:
                    for em in val.split(","):
                        clean_em = clean_calendar_id(em.strip())
                        if clean_em:
                            cal_ids.add(clean_em)

        for cal_id in cal_ids:
            events_url = f"https://www.googleapis.com/calendar/v3/calendars/{cal_id}/events"
            params = {
                "timeMin": time_min,
                "timeMax": time_max,
                "singleEvents": "true",
                "orderBy": "startTime",
            }
            try:
                events_resp = session.get(events_url, params=params, timeout=10)
                if events_resp.status_code != 200:
                    continue
                items = events_resp.json().get("items", [])
            except Exception:
                continue

            for item in items:
                if item.get("status") == "cancelled":
                    continue
                summary = item.get("summary", "").strip()
                if not summary:
                    continue

                # Match against paired courses
                matched_course = match_course_for_event(summary, registered_courses)
                if not matched_course:
                    continue

                start_info = item.get("start", {})
                end_info = item.get("end", {})

                start_iso = start_info.get("dateTime") or start_info.get("date")
                end_iso = end_info.get("dateTime") or end_info.get("date")

                if not start_iso or not end_iso or "T" not in start_iso:
                    continue

                start_dt = datetime.fromisoformat(start_iso)
                end_dt = datetime.fromisoformat(end_iso)

                event_key = (
                    start_dt.strftime("%Y-%m-%d"),
                    start_dt.strftime("%H:%M"),
                    end_dt.strftime("%H:%M"),
                    summary.lower(),
                )
                if event_key in seen_keys:
                    continue
                seen_keys.add(event_key)

                course_code = matched_course.get("code", "Course")
                tab_name = matched_course.get("tab_name", course_code)
                sheet_id = matched_course.get("sheet_id", "")

                event_entry = {
                    "name": summary,
                    "title": summary,
                    "course_id": matched_course.get("id", course_code.lower()),
                    "course_code": course_code,
                    "subject": tab_name,
                    "sheet_id": sheet_id,
                    "tab_name": tab_name,
                    "teacher_email": matched_course.get("teacher_email", ""),
                    "date": start_dt.strftime("%Y-%m-%d"),
                    "day": start_dt.strftime("%A"),
                    "start_time": start_dt.strftime("%H:%M"),
                    "end_time": end_dt.strftime("%H:%M"),
                    "enabled": True,
                    "source": "google_calendar",
                }
                synced_events.append(event_entry)

        synced_events.sort(key=lambda x: (x.get("date", ""), x.get("start_time", "")))

        data_dir = os.path.expanduser("~/.classtracker/config")
        os.makedirs(data_dir, exist_ok=True)
        schedule_path = os.path.join(data_dir, "schedule.json")
        with open(schedule_path, "w", encoding="utf-8") as f:
            json.dump(synced_events, f, indent=2)
        print(f"[SUCCESS] 📅 Synced {len(synced_events)} event(s) from Google Calendar into {schedule_path}!")

        return (bot_email, synced_events)

    except Exception as e:
        print(f"[WARN] Google Calendar sync failed: {e}")
        return ("", [])


if __name__ == "__main__":
    bot_email, events = sync_google_calendar()
    print(f"Bot Email: {bot_email} | Total synced events: {len(events)}")
