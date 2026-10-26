"""Manages multi-subject course profiles and pairing codes for Class Tracker."""

import os
import sys
import json
import random
import string
from datetime import datetime
from typing import List, Dict, Any, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HERE)
PROFILES_FILE = os.path.join(PROJECT_ROOT, "config", "courses.json")


def _generate_pairing_code(course_code: str) -> str:
    """Generates a memorable 6-digit code like DAA-8942 or OS-7319."""
    clean_prefix = "".join(c for c in course_code.upper() if c.isalnum())[:4] or "CRS"
    digits = "".join(random.choices(string.digits, k=4))
    return f"{clean_prefix}-{digits}"


def _ensure_seed_profiles():
    """Initializes default course profiles if profiles file does not exist or is empty."""
    if os.path.exists(PROFILES_FILE) and os.path.getsize(PROFILES_FILE) > 5:
        return

    os.makedirs(os.path.dirname(PROFILES_FILE), exist_ok=True)
    units = []
    curr_file = os.path.join(os.path.dirname(PROFILES_FILE), "curriculum_daa.json")
    if os.path.exists(curr_file):
        try:
            with open(curr_file, "r", encoding="utf-8") as f:
                d = json.load(f)
                units = d.get("units", [])
        except Exception:
            pass

    default_course = {
        "id": "daa",
        "name": "Design & Analysis of Algorithm (DAA)",
        "code": "DAA",
        "sheet_id": "1eGBmCNgfjwABNwVQEt_HJfHpT_kQIttR6QGec_IbLJA",
        "tab_name": "DAA",
        "teacher_email": "rvkeerthi99@gmail.com",
        "calendar_id": "rvkeerthi99@gmail.com",
        "pairing_code": "DAA-0183",
        "units": units,
        "curriculum_count": len(units) or 50,
    }
    with open(PROFILES_FILE, "w", encoding="utf-8") as f:
        json.dump([default_course], f, indent=2)


def get_user_courses_file() -> str:
    user_dir = os.path.expanduser("~/.classtracker/config")
    os.makedirs(user_dir, exist_ok=True)
    return os.path.join(user_dir, "courses.json")


def clear_user_courses():
    """Wipes all paired courses from the user's local config, returning app to bare naked state."""
    uf = get_user_courses_file()
    try:
        with open(uf, "w", encoding="utf-8") as f:
            json.dump([], f, indent=2)
    except Exception as e:
        print(f"[WARN] Failed to clear user courses: {e}")


def get_all_courses(prefer_user_dir: bool = False) -> List[Dict[str, Any]]:
    """Returns list of configured course profiles.
    When prefer_user_dir=True (Mac Client App mode), reads strictly from ~/.classtracker/config/courses.json.
    If no courses have been paired by the user, returns [] (bare naked).
    """
    if prefer_user_dir:
        uf = get_user_courses_file()
        if os.path.exists(uf) and os.path.getsize(uf) > 5:
            try:
                with open(uf, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return []
        return []

    # Server mode: reads from server PROFILES_FILE
    _ensure_seed_profiles()
    if not os.path.exists(PROFILES_FILE):
        return []
    try:
        with open(PROFILES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[WARN] Error loading courses: {e}")
        return []


def save_user_paired_course(course_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Saves a newly paired course profile strictly to ~/.classtracker/config/courses.json."""
    uf = get_user_courses_file()
    courses = []
    if os.path.exists(uf) and os.path.getsize(uf) > 5:
        try:
            with open(uf, "r", encoding="utf-8") as f:
                courses = json.load(f)
        except Exception:
            courses = []

    cid = (course_data.get("id") or course_data.get("code") or "crs").lower()
    courses = [c for c in courses if (c.get("id") or "").lower() != cid]
    courses.append(course_data)

    with open(uf, "w", encoding="utf-8") as f:
        json.dump(courses, f, indent=2)

    return courses


def get_course_by_id(course_id: str) -> Optional[Dict[str, Any]]:
    """Finds a course profile by its ID (e.g. 'daa', 'os')."""
    courses = get_all_courses()
    target = course_id.strip().lower()
    for c in courses:
        if c.get("id", "").strip().lower() == target:
            return c
    return None


def get_course_by_pairing_code(code: str) -> Optional[Dict[str, Any]]:
    """Finds a course profile flexibly by:
    1. Exact pairing code (e.g. 'DAA-1655', 'DAA-0183')
    2. Course code (e.g. 'DAA') or Course ID ('daa')
    3. Prefix of pairing code (e.g. 'DAA-4645' -> matches 'DAA')
    4. If only 1 course exists and query looks like a course code, return it.
    """
    courses = get_all_courses()
    if not courses:
        return None
    target = code.strip().upper().replace(" ", "")

    # 1. Exact match on pairing_code
    for c in courses:
        clean_code = c.get("pairing_code", "").strip().upper().replace(" ", "")
        if clean_code and clean_code == target:
            return c

    # 2. Match on course code or course ID (e.g. 'DAA' or 'daa')
    for c in courses:
        course_code = c.get("code", "").strip().upper().replace(" ", "")
        course_id = c.get("id", "").strip().upper().replace(" ", "")
        if target in (course_code, course_id):
            return c

    # 3. Match on prefix if code is formatted like 'CODE-XXXX'
    prefix = target.split("-")[0].strip()
    if prefix:
        for c in courses:
            course_code = c.get("code", "").strip().upper().replace(" ", "")
            course_id = c.get("id", "").strip().upper().replace(" ", "")
            if prefix in (course_code, course_id):
                return c

    # 4. Fallback: If only 1 course is configured on the portal, return it
    if len(courses) == 1:
        return courses[0]

    return None


def clean_calendar_id(raw: str) -> str:
    """Extracts calendar ID / email even if user pasted full Google Calendar embed or sharing URL."""
    if not raw:
        return ""
    raw = raw.strip()
    if "calendar.google.com" in raw:
        import urllib.parse
        parsed = urllib.parse.urlparse(raw)
        qs = urllib.parse.parse_qs(parsed.query)
        if "src" in qs and qs["src"]:
            return urllib.parse.unquote(qs["src"][0])
        if "cid" in qs and qs["cid"]:
            return urllib.parse.unquote(qs["cid"][0])
    return raw


def save_course(
    name: str,
    code: str,
    sheet_id: str,
    tab_name: str,
    teacher_email: str = "",
    calendar_id: str = "",
    units: Optional[List[Dict[str, Any]]] = None,
    course_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Creates or updates a course profile with a pairing code."""
    courses = get_all_courses()
    clean_code = code.strip().upper()
    cid = course_id or clean_code.lower()
    clean_cal = clean_calendar_id(calendar_id)

    # Check existing
    existing = next((c for c in courses if c.get("id") == cid), None)

    if existing:
        existing["name"] = name.strip()
        existing["code"] = clean_code
        existing["sheet_id"] = sheet_id.strip()
        existing["tab_name"] = tab_name.strip()
        existing["teacher_email"] = teacher_email.strip()
        existing["calendar_id"] = clean_cal
        if units is not None:
            existing["units"] = units
        existing["updated_at"] = datetime.utcnow().isoformat() + "Z"
        saved_course = existing
    else:
        pairing_code = _generate_pairing_code(clean_code)
        # Ensure code uniqueness
        while any(c.get("pairing_code") == pairing_code for c in courses):
            pairing_code = _generate_pairing_code(clean_code)

        saved_course = {
            "id": cid,
            "name": name.strip(),
            "code": clean_code,
            "sheet_id": sheet_id.strip(),
            "tab_name": tab_name.strip(),
            "teacher_email": teacher_email.strip(),
            "calendar_id": calendar_id.strip(),
            "pairing_code": pairing_code,
            "units": units or [],
            "created_at": datetime.utcnow().isoformat() + "Z",
        }
        courses.append(saved_course)

    with open(PROFILES_FILE, "w", encoding="utf-8") as f:
        json.dump(courses, f, indent=2)

    # Persist course-specific curriculum JSON file for pipeline matcher
    if units:
        try:
            curr_data = {"units": units}
            dest1 = os.path.join(PROJECT_ROOT, "config", f"curriculum_{cid}.json")
            with open(dest1, "w", encoding="utf-8") as cf:
                json.dump(curr_data, cf, indent=2)
            
            user_cfg_dir = os.path.expanduser("~/.classtracker/config")
            os.makedirs(user_cfg_dir, exist_ok=True)
            dest2 = os.path.join(user_cfg_dir, f"curriculum_{cid}.json")
            with open(dest2, "w", encoding="utf-8") as cf2:
                json.dump(curr_data, cf2, indent=2)
        except Exception as e:
            print(f"[WARN] Failed to write course curriculum file: {e}")

    return saved_course


def delete_course(course_id: str) -> bool:
    """Removes a course profile by ID across both server and user configs."""
    cid = course_id.strip().lower()
    
    # 1. Remove from server PROFILES_FILE
    courses = get_all_courses()
    initial_len = len(courses)
    courses = [c for c in courses if c.get("id", "").strip().lower() != cid]
    deleted = len(courses) < initial_len
    if deleted:
        with open(PROFILES_FILE, "w", encoding="utf-8") as f:
            json.dump(courses, f, indent=2)

    # 2. Also remove from client/user courses file if present
    uf = get_user_courses_file()
    if os.path.exists(uf):
        try:
            with open(uf, "r", encoding="utf-8") as f:
                user_courses = json.load(f)
            u_init = len(user_courses)
            user_courses = [c for c in user_courses if c.get("id", "").strip().lower() != cid]
            if len(user_courses) < u_init:
                with open(uf, "w", encoding="utf-8") as f:
                    json.dump(user_courses, f, indent=2)
                deleted = True
        except Exception:
            pass

    # 3. Clean up curriculum files
    for base in [os.path.join(PROJECT_ROOT, "config"), os.path.expanduser("~/.classtracker/config")]:
        cpath = os.path.join(base, f"curriculum_{cid}.json")
        if os.path.exists(cpath):
            try:
                os.remove(cpath)
            except Exception:
                pass

    return deleted
