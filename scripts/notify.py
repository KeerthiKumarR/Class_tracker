"""Email notifications via Gmail SMTP (STARTTLS) using built-in smtplib."""

import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from typing import Dict, Any, Optional, List


def _send_email(
    sender: str,
    password: str,
    recipient: str,
    subject: str,
    body: str,
    attachment_path: Optional[str] = None,
) -> bool:
    """Sends a plain-text email via Gmail SMTP using STARTTLS with optional attachment. Fails silently with logging."""
    if not sender or not password or not recipient:
        print("[INFO] Email skipped: GMAIL_SENDER, GMAIL_APP_PASSWORD, or recipient not configured.")
        return False

    try:
        msg = MIMEMultipart()
        msg["From"] = sender
        msg["To"] = recipient
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain", "utf-8"))

        if attachment_path and os.path.exists(attachment_path):
            try:
                with open(attachment_path, "rb") as af:
                    part = MIMEBase("application", "octet-stream")
                    part.set_payload(af.read())
                encoders.encode_base64(part)
                att_filename = os.path.basename(attachment_path)
                part.add_header("Content-Disposition", f'attachment; filename="{att_filename}"')
                msg.attach(part)
            except Exception as att_err:
                print(f"[WARN] Failed to attach file {attachment_path}: {att_err}")

        with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as server:
            server.starttls()
            server.login(sender, password.replace(" ", ""))
            server.send_message(msg)

        print(f"[INFO] Email notification sent successfully to {recipient} (Subject: '{subject}')")
        return True
    except Exception as e:
        print(f"[WARN] Failed to send email to {recipient}: {e}")
        return False



def send_teacher_summary(
    result: Dict[str, Any],
    unit: Dict[str, Any],
    date_str: str,
    config: Optional[Dict[str, Any]] = None,
    recap_detected: bool = False,
    review_reasons: Optional[List[str]] = None,
) -> bool:
    """Sends lecture summary email to the teacher.

    Subject: 📚 DAA Summary — {unit_id} {topic} — {date}
    """
    try:
        cfg = config or {}
        sender = cfg.get("GMAIL_SENDER") or os.environ.get("GMAIL_SENDER", "")
        password = cfg.get("GMAIL_APP_PASSWORD") or os.environ.get("GMAIL_APP_PASSWORD", "")
        teacher_email = cfg.get("TEACHER_EMAIL") or os.environ.get("TEACHER_EMAIL", "")
        sheet_id = cfg.get("GOOGLE_SHEET_ID") or os.environ.get("GOOGLE_SHEET_ID", "")

        unit_id = unit.get("unit_id", "N/A")
        topic = unit.get("topic", "N/A")
        status = result.get("status", "In Progress")
        covered_today = result.get("percent_covered_today", 0)
        cumulative = result.get("percent_complete_cumulative", 0)
        left_over = result.get("left_over", "")
        clean_left_over = left_over if (left_over and left_over.strip()) else "Nothing — fully covered ✅"

        cw = result.get("classwork_done")
        hw = result.get("homework_assigned")
        cw_str = cw if cw else "None / Not noted"
        hw_str = hw if hw else "None / Not assigned"
        notes = result.get("notes", "")

        course_label = cfg.get("COURSE_CODE") or cfg.get("SHEET_TAB_NAME") or "Lecture"
        subject = f"📚 {course_label} Summary — {unit_id} {topic} — {date_str}"
        body = (
            f"Unit: {unit_id} · {topic}\n"
            f"Date: {date_str}\n"
            f"Status: {status}\n"
            f"Covered today: {covered_today}% | Overall: {cumulative}%\n"
            f"Left over: {clean_left_over}\n"
            f"Classwork: {cw_str} | Homework: {hw_str}\n\n"
            f"📖 What Was Taught in Today's Class:\n"
            f"{notes}\n\n"
            f"📊 View full sheet: https://docs.google.com/spreadsheets/d/{sheet_id}\n"
        )

        notes_for_teacher = []
        if recap_detected:
            notes_for_teacher.append(
                "Note: this session included review of earlier material, which is why "
                "today's new-content percentage is lower than the class length."
            )

        if review_reasons and any("classwork and homework are null" in r.lower() for r in review_reasons):
            notes_for_teacher.append(
                "Classwork/homework shown as 'None' because none was mentioned in the recording — "
                "let us know if this is wrong."
            )

        if notes_for_teacher:
            body += "\n" + "\n".join(notes_for_teacher) + "\n"

        recipients = [teacher_email]
        dev_email = cfg.get("DEVELOPER_EMAIL") or os.environ.get("DEVELOPER_EMAIL", "")
        if dev_email and dev_email.strip() and dev_email.strip().lower() != teacher_email.strip().lower():
            recipients.append(dev_email.strip())

        success = True
        for rec in recipients:
            if rec:
                ok = _send_email(sender, password, rec, subject, body)
                if not ok:
                    success = False
        return success
    except Exception as e:
        print(f"[WARN] send_teacher_summary failed: {e}")
        return False


def send_developer_alert(
    message: str,
    filename: str,
    config: Optional[Dict[str, Any]] = None,
    error: Optional[Any] = None,
    warnings: Optional[List[str]] = None,
    retries: int = 0,
    fallback_model: Optional[str] = None,
    attachment_path: Optional[str] = None,
) -> bool:
    """Sends alert email to developer only with optional audio attachment. Never sent to teacher."""
    try:
        cfg = config or {}
        sender = cfg.get("GMAIL_SENDER") or os.environ.get("GMAIL_SENDER", "")
        password = cfg.get("GMAIL_APP_PASSWORD") or os.environ.get("GMAIL_APP_PASSWORD", "")
        dev_email = cfg.get("DEVELOPER_EMAIL") or os.environ.get("DEVELOPER_EMAIL", "")

        subject = f"⚠️ Class Tracker Alert — {filename}"

        body_lines = [
            f"Class Tracker Alert Event",
            f"========================",
            f"File: {filename}",
            f"Message: {message}",
        ]

        if error:
            body_lines.append(f"Error Details: {error}")
        if retries > 0:
            body_lines.append(f"Retries attempted: {retries}")
        if fallback_model:
            body_lines.append(f"Fallback Model Used: {fallback_model}")
        if warnings:
            body_lines.append("\nWarnings:")
            for w in warnings:
                body_lines.append(f"  - {w}")

        body = "\n".join(body_lines) + "\n"
        return _send_email(sender, password, dev_email, subject, body, attachment_path=attachment_path)
    except Exception as e:
        print(f"[WARN] send_developer_alert failed: {e}")
        return False

