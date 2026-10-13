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

SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events.readonly",
]


def load_config() -> dict:
    env_path = os.path.join(PROJECT_ROOT, "config", ".env")
    if not os.path.exists(env_path):
        return {}
    return dotenv_values(env_path)


def get_service_account_path(config: dict) -> str:
    sa_path = config.get("GOOGLE_SERVICE_ACCOUNT_JSON", "config/service_account.json")
    if not os.path.isabs(sa_path):
        sa_path = os.path.join(PROJECT_ROOT, sa_path)
    return sa_path


import re

def is_target_course_event(summary: str, course_filter: Optional[str] = None) -> bool:
    """Checks if a calendar event title/summary corresponds to the target course (DAA).
    
    Specifically avoids matching unrelated courses such as:
    - Data Structure and Algorithms (DSA)
    - GenAI
    - Essentials of Information Technology
    - Weekly Review / Non-lecture meetings
    """
    if not summary or not summary.strip():
        return False
    
    s = summary.strip().lower()

    if course_filter:
        keywords = [k.strip().lower() for k in course_filter.split(",") if k.strip()]
        for kw in keywords:
            if kw and re.search(r"\b" + re.escape(kw) + r"\b", s):
                return True
        return False

    # Exclude other subjects even if they contain the word "Algorithms"
    if "data structure" in s or re.search(r"\bdsa\b", s):
        # Exclude unless DAA is explicitly also in the title
        if not re.search(r"\bdaa\b", s):
            return False

    # Positive match for DAA
    if re.search(r"\bdaa\b", s):
        return True

    # Positive match for full title "Design & Analysis of Algorithm(s)"
    if re.search(r"design\s*(&|and)\s*analysis\s*of\s*algorithms?", s):
        return True

    return False


def sync_google_calendar(days_ahead: int = 7):
    """Fetches upcoming events from all calendars shared with the service account
    and updates config/schedule.json automatically with DAA class events only.
    
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

        cal_ids = set()
        for key in ["TEACHER_EMAIL", "DEVELOPER_EMAIL", "GMAIL_SENDER", "CALENDAR_ID"]:
            val = config.get(key, "")
            if val:
                for email in val.split(","):
                    if email.strip():
                        cal_ids.add(email.strip())

        # 1. Get all accessible calendars from calendarList
        cal_list_resp = session.get("https://www.googleapis.com/calendar/v3/users/me/calendarList")
        if cal_list_resp.status_code == 200:
            calendars = cal_list_resp.json().get("items", [])
            for c in calendars:
                if c.get("id"):
                    cal_ids.add(c["id"])

        if not cal_ids:
            print(f"[INFO] No Google Calendars shared with {bot_email} yet.")
            return (bot_email, [])

        now = datetime.now(timezone.utc)
        time_min = (now - timedelta(hours=2)).isoformat()
        time_max = (now + timedelta(days=days_ahead)).isoformat()

        course_filter = config.get("COURSE_FILTER")
        synced_events = []
        seen_keys = set()

        for cal_id in cal_ids:
            events_url = f"https://www.googleapis.com/calendar/v3/calendars/{cal_id}/events"
            params = {
                "timeMin": time_min,
                "timeMax": time_max,
                "singleEvents": "true",
                "orderBy": "startTime",
            }
            events_resp = session.get(events_url, params=params)
            if events_resp.status_code != 200:
                continue

            items = events_resp.json().get("items", [])
            for item in items:
                if item.get("status") == "cancelled":
                    continue
                summary = item.get("summary", "").strip()
                if not summary:
                    continue

                # Filter strictly for DAA classes
                if not is_target_course_event(summary, course_filter=course_filter):
                    continue

                start_info = item.get("start", {})
                end_info = item.get("end", {})

                start_iso = start_info.get("dateTime") or start_info.get("date")
                end_iso = end_info.get("dateTime") or end_info.get("date")

                if not start_iso or not end_iso:
                    continue

                # Parse datetimes
                if "T" in start_iso:
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

                    event_entry = {
                        "name": summary,
                        "title": summary,
                        "subject": config.get("SHEET_TAB_NAME", "DAA"),
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
        print(f"[SUCCESS] 📅 Synced {len(synced_events)} DAA event(s) from Google Calendar into {schedule_path}!")

        return (bot_email, synced_events)

    except Exception as e:
        print(f"[WARN] Google Calendar sync failed: {e}")
        return ("", [])


if __name__ == "__main__":
    bot_email, events = sync_google_calendar()
    print(f"Bot Email: {bot_email} | Total synced events: {len(events)}")
