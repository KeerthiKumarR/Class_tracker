"""Core pipeline: transcribe -> load curriculum -> match unit -> audit -> human approval -> update sheet."""

import os
import sys
import re
import shutil
from datetime import datetime
from typing import Optional, Dict, Any, List
from groq import Groq

from transcribe import transcribe_audio
from curriculum import load_curriculum
from unit_matcher import find_matching_unit
from match_topic import match_topic
from sheets_client import SheetClient
from notify import send_teacher_summary, send_developer_alert

DATE_PATTERN = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _archive_or_delete_file(audio_path: str, config: dict, processed_folder: str):
    """Safely archives or deletes an audio file so it is NEVER repeatedly processed in incoming/."""
    if not os.path.exists(audio_path):
        return
    delete_after = str(config.get("DELETE_AUDIO_AFTER_PROCESS", "true")).strip().lower() in ("true", "1", "yes")
    filename = os.path.basename(audio_path)
    if delete_after:
        try:
            os.remove(audio_path)
        except Exception:
            pass
    else:
        try:
            os.makedirs(processed_folder, exist_ok=True)
            dest = os.path.join(processed_folder, filename)
            if os.path.exists(dest):
                try:
                    os.remove(dest)
                except Exception:
                    pass
            shutil.move(audio_path, dest)
        except Exception as e:
            print(f"[WARN] Failed to archive {audio_path} to {processed_folder}: {e}")


def extract_date(filename: str) -> str:
    """Extracts YYYY-MM-DD from filename if present, else returns today's date."""
    match = DATE_PATTERN.search(filename)
    if match:
        return match.group(1)
    return datetime.now().strftime("%Y-%m-%d")


def process_file(
    audio_path: str,
    config: dict,
    sheet: SheetClient,
    processed_folder: str,
    auto_confirm: bool = False,
    target_unit_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Runs the full processing pipeline on an audio file.

    Returns a status dict describing the outcome.
    """
    if not os.path.exists(audio_path):
        msg = f"Audio file not found: {audio_path}"
        print(f"[WARN] {msg}")
        return {"success": False, "reason": "file_not_found", "message": msg}

    filename = os.path.basename(audio_path)
    date_str = extract_date(filename)
    
    # Robust curriculum path resolution across dev & standalone .app bundle
    curriculum_path = config.get("CURRICULUM_PATH", "config/curriculum.json")
    if not os.path.isabs(curriculum_path) or not os.path.exists(curriculum_path):
        bundle_root = getattr(sys, '_MEIPASS', os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
        cand = os.path.join(bundle_root, curriculum_path)
        if os.path.exists(cand):
            curriculum_path = cand
        else:
            cand2 = os.path.join(bundle_root, "config", "curriculum.json")
            if os.path.exists(cand2):
                curriculum_path = cand2
            else:
                user_cand = os.path.expanduser(os.path.join("~/.classtracker", curriculum_path))
                if os.path.exists(user_cand):
                    curriculum_path = user_cand

    if not os.path.exists(curriculum_path):
        msg = f"Curriculum reference file not found at: {curriculum_path}"
        print(f"[ERROR] {msg}")
        send_developer_alert(msg, filename, config=config, error=msg)
        _archive_or_delete_file(audio_path, config, processed_folder)
        return {"success": False, "reason": "missing_curriculum", "message": msg}

    # 1. Load curriculum
    try:
        curriculum = load_curriculum(curriculum_path)
    except Exception as e:
        msg = f"Failed to load curriculum from {curriculum_path}: {e}"
        print(f"[ERROR] {msg}")
        send_developer_alert(msg, filename, config=config, error=str(e))
        _archive_or_delete_file(audio_path, config, processed_folder)
        return {"success": False, "reason": "curriculum_load_error", "message": msg}

    if not curriculum:
        msg = f"Curriculum file is empty: {curriculum_path}"
        print(f"[ERROR] {msg}")
        send_developer_alert(msg, filename, config=config, error=msg)
        _archive_or_delete_file(audio_path, config, processed_folder)
        return {"success": False, "reason": "empty_curriculum", "message": msg}

    # 2. Transcribe Audio
    print(f"\n[INFO] 1/4 Transcribing {filename} ...")
    try:
        transcript = transcribe_audio(
            audio_path,
            config,
            language=config.get("WHISPER_LANGUAGE", "auto"),
        )
    except Exception as e:
        msg = f"Transcription failed: {e}"
        print(f"[ERROR] {msg}")
        send_developer_alert(msg, filename, config=config, error=str(e))
        raise

    if not transcript or not transcript.strip():
        msg = f"Empty transcript generated for {filename}. Skipping processing."
        print(f"[WARN] {msg}")
        send_developer_alert(msg, filename, config=config)
        _archive_or_delete_file(audio_path, config, processed_folder)
        return {"success": False, "reason": "empty_transcript", "message": msg}

    # Check for Low Audio Quality / Short Fragment (under 150 words)
    words = transcript.strip().split()
    word_count = len(words)
    if word_count < 150:
        msg = f"Low audio length/quality detected: transcript has only {word_count} words (< 150 words threshold). Skipping matching and sheet updates."
        print(f"[WARN] {msg}")
        send_developer_alert(
            message=msg,
            filename=filename,
            config=config,
            warnings=[f"Transcript word count: {word_count} (minimum required: 150)"],
        )
        # Clean up or archive low quality audio
        _archive_or_delete_file(audio_path, config, processed_folder)
        return {
            "success": False,
            "reason": "low_audio_quality",
            "word_count": word_count,
            "filename": filename,
            "message": msg,
        }

    client = Groq(api_key=config["GROQ_API_KEY"])
    model = config.get("GROQ_MODEL", "qwen/qwen3.8-27b")
    warnings: List[str] = []

    # 3. Match transcript to curriculum unit (or use manual override)
    recap_detected = False
    recap_units = []
    if target_unit_id and target_unit_id.strip():
        matched_unit_id = target_unit_id.strip()
        print(f"[INFO] 2/4 Using specified Unit ID override: '{matched_unit_id}'")
    else:
        print(f"[INFO] 2/4 Identifying matching curriculum unit from catalog ({len(curriculum)} units)...")
        match_info = find_matching_unit(transcript, curriculum, client, model)
        if isinstance(match_info, dict):
            matched_unit_id = match_info.get("primary_unit_id")
            recap_detected = match_info.get("recap_detected", False)
            recap_units = match_info.get("recap_unit_ids", [])
        else:
            matched_unit_id = match_info

    if recap_detected:
        recap_msg = f"Recap detected for prior unit(s) {recap_units}. Applied Rule 0 (Recap Isolation)."
        print(f"[WARN] {recap_msg}")
        warnings.append(recap_msg)

    if not matched_unit_id:
        msg = f"Transcript for {filename} could not be confidently matched to any curriculum unit."
        print(f"[SKIP] {msg}")
        send_developer_alert(msg, filename, config=config, warnings=["Confidence low or no unit match"])
        _archive_or_delete_file(audio_path, config, processed_folder)
        return {"success": False, "reason": "no_unit_matched", "message": msg}

    # Retrieve unit object
    matched_unit = next((u for u in curriculum if u["unit_id"].lower() == matched_unit_id.lower()), None)
    if not matched_unit:
        msg = f"Matched Unit ID '{matched_unit_id}' not found in curriculum catalog."
        print(f"[ERROR] {msg}")
        send_developer_alert(msg, filename, config=config, error=msg)
        _archive_or_delete_file(audio_path, config, processed_folder)
        return {"success": False, "reason": "unit_not_in_curriculum", "message": msg}

    # 4. Find row in Google Sheet
    print(f"[INFO] 3/4 Locating Unit ID '{matched_unit_id}' in Google Sheet...")
    row_number, _ = sheet.find_row_by_unit_id(matched_unit_id, topic=matched_unit.get("topic"))
    if row_number is None:
        msg = f"Unit ID '{matched_unit_id}' does not exist in Column A of the Google Sheet."
        print(f"[SKIP] {msg}")
        send_developer_alert(msg, filename, config=config, error=msg)
        _archive_or_delete_file(audio_path, config, processed_folder)
        return {"success": False, "reason": "unit_not_in_sheet", "message": msg}

    # Multi-session / same-day check: If already recorded today, allow continuing progress updates
    today_str = datetime.now().strftime("%Y-%m-%d")
    already_taught_today = sheet.is_already_taught_today(row_number, today_str)
    if already_taught_today:
        if not auto_confirm and str(config.get("BLOCK_SAME_DAY_SESSIONS", "false")).strip().lower() in ("true", "1", "yes"):
            dup_msg = (
                f"Duplicate detected for unit '{matched_unit_id}' on {today_str} — file '{filename}' was NOT written to the sheet. "
                f"If this is a second legitimate session, reprocess with auto_confirm or clear Date Taught cell."
            )
            print(f"[INFO] {dup_msg}")
            send_developer_alert(
                message=dup_msg,
                filename=filename,
                config=config,
                warnings=["Duplicate session on same unit/date ignored by idempotency check."],
            )
            _archive_or_delete_file(audio_path, config, processed_folder)
            return {
                "success": True,
                "unit_id": matched_unit_id,
                "topic": matched_unit.get("topic", ""),
                "status": "Already Processed Today",
                "percent_covered_today": 0,
                "duplicate": True,
                "warnings": warnings,
                "message": dup_msg,
            }
        else:
            print(f"[INFO] Continuing / multi-session update for unit '{matched_unit_id}' on {today_str}. Calculating cumulative progression...")


    prior_cumulative = sheet.get_prior_cumulative(row_number)
    prior_today_covered = sheet.get_prior_today_covered(row_number) if already_taught_today else 0

    # 5. Audit topic against curriculum content/classwork/homework
    print(f"[INFO] 4/4 Auditing coverage against unit: {matched_unit_id} - {matched_unit.get('topic', '')} ...")
    result = match_topic(client, model, matched_unit, transcript, previous_cumulative_percent=prior_cumulative)

    # Deterministic Progression Guardrail:
    # Enforce cumulative math (Prior + Today) so LLM arithmetic hallucinations never slip through
    today_cov = result.get("percent_covered_today", 0)
    calculated_cumulative = min(100, max(today_cov, prior_cumulative + today_cov))
    result["percent_complete_cumulative"] = calculated_cumulative

    # Accumulate today's coverage if multiple sessions occur on the same day
    if already_taught_today:
        result["percent_covered_today"] = min(100, max(today_cov, prior_today_covered + today_cov))

    if calculated_cumulative >= 90 and not result.get("left_over"):
        result["status"] = "Completed"
    elif calculated_cumulative > 0 or result.get("left_over"):
        result["status"] = "In Progress"

    # Merge newly solved problems & notes with prior session for multi-session updates
    if prior_cumulative > 0:
        existing_details = sheet.get_existing_details(row_number)
        prior_left_over = existing_details.get("left_over", "")

        # If previous session had left over topics, check if today's continuation addressed them
        if prior_left_over:
            prior_items = [item.strip() for item in prior_left_over.split(",") if item.strip()]
            today_cw_text = f"{result.get('classwork_done') or ''} {result.get('notes') or ''}".lower()
            remaining_left_over = []
            for item in prior_items:
                clean_term = item.split("(")[0].strip().lower()
                if clean_term and clean_term in today_cw_text:
                    continue  # Successfully taught in this continuation session
                remaining_left_over.append(item)
            result["left_over"] = ", ".join(remaining_left_over)

        # If all prior leftovers are resolved or cumulative >= 90, mark unit Completed
        if not result.get("left_over") and (prior_cumulative + today_cov >= 75):
            result["percent_complete_cumulative"] = 100
            result["status"] = "Completed"
            if already_taught_today and prior_today_covered > 0:
                result["percent_covered_today"] = 100

        if existing_details.get("classwork"):
            existing_cw = [c.strip() for c in existing_details["classwork"].split(",") if c.strip()]
            new_cw = [c.strip() for c in (result.get("classwork_done") or "").split(",") if c.strip()]
            merged_cw = list(dict.fromkeys(existing_cw + new_cw))
            if merged_cw:
                result["classwork_done"] = ", ".join(merged_cw)
        if existing_details.get("notes") and result.get("notes") and result["notes"] not in existing_details["notes"]:
            result["notes"] = f"{existing_details['notes']} (Part 2: {result['notes']})"

    # 6. Display proposed values
    print("\n" + "=" * 65)
    print("📋 PROPOSED SHEET UPDATES")
    print("=" * 65)
    print(f"Target Row in Sheet       : Row {row_number}")
    print(f"Matched Unit ID (Col A)   : {matched_unit_id} ({matched_unit.get('topic', '')})")
    print("-" * 65)
    print(f"Col I (Date Taught)       : {date_str}")
    print(f"Col J (Status)            : {result['status']}")
    print(f"Col K (% Complete Overall): {result['percent_complete_cumulative']}%")
    print(f"Col L (% Covered Today)   : {result['percent_covered_today']}%")
    print(f"Col M (Left Over)         : \"{result['left_over']}\"")
    print(f"Col N (Carry To)          : [UNTOUCHED]")
    print(f"Col O (CW - Classwork)    : {result['classwork_done']}")
    print(f"Col P (HW - Homework)     : {result['homework_assigned']}")
    print(f"Col Q (Notes)             : {result['notes']}")
    print("=" * 65)

    # 7. Strict Guardrail: Zero Instructional Coverage Protection
    has_instructional_progress = (
        result.get("percent_covered_today", 0) > 0
        or (result.get("classwork_done") and str(result["classwork_done"]).strip().lower() not in ("null", "none", ""))
        or (result.get("homework_assigned") and str(result["homework_assigned"]).strip().lower() not in ("null", "none", ""))
    )

    if not has_instructional_progress:
        msg = (
            f"Zero instructional progress detected for {filename} (0% coverage, no classwork/homework). "
            f"Skipping Sheet update to protect existing curriculum progression data."
        )
        print(f"\n[GUARDRAIL BLOCKED] 🛑 {msg}")
        send_developer_alert(
            message=msg,
            filename=filename,
            config=config,
            warnings=["0% coverage and no problems solved/assigned (informal opening/fragment)."],
        )
        _archive_or_delete_file(audio_path, config, processed_folder)
        return {
            "success": False,
            "reason": "zero_instructional_progress",
            "message": msg,
            "result": result,
        }

    # 8. Uncertainty & Review Checks
    review_reasons = []

    valid_statuses = {"Completed", "In Progress", "Skipped"}
    if not result.get("status") or result["status"] not in valid_statuses:
        review_reasons.append(f"Unexpected status value '{result.get('status')}'")

    cw = result.get("classwork_done")
    hw = result.get("homework_assigned")
    is_cw_null = cw is None or str(cw).lower() in ("null", "none", "")
    is_hw_null = hw is None or str(hw).lower() in ("null", "none", "")
    if is_cw_null and is_hw_null:
        review_reasons.append("Both classwork and homework are null (no spoken CW/HW evidence detected)")

    requires_review = len(review_reasons) > 0

    if requires_review:
        print("\n" + "⚠️ " * 20)
        print("⚠️  HUMAN REVIEW REQUIRED BEFORE WRITING TO SHEET")
        print("⚠️  Triggered because:")
        for r in review_reasons:
            print(f"   • {r}")
        print("⚠️ " * 20)

        if not auto_confirm:
            user_choice = input("\n👉 Type 'yes' to approve and write these updates (or anything else to abort): ").strip().lower()
            if user_choice != "yes":
                print(f"[ABORTED] Write skipped by user for {filename}.")
                return {"success": False, "reason": "aborted_by_user", "message": "Write cancelled by user."}
    else:
        print("\n[INFO] All confidence checks passed (high certainty). Auto-writing updates to sheet...")

    # 9. Write Primary Unit to Sheet
    print(f"[INFO] Writing updates to Row {row_number} (Columns I–M and O–Q)...")
    sheet.update_progression(
        row_number=row_number,
        date_taught=date_str,
        status=result["status"],
        percent_complete_cumulative=result["percent_complete_cumulative"],
        percent_covered_today=result["percent_covered_today"],
        left_over=result["left_over"],
        classwork_done=result["classwork_done"],
        homework_assigned=result["homework_assigned"],
        notes=result["notes"],
    )

    # 8b. Multi-Unit Split Class Handling:
    # If this lecture covered leftover topics for an unfinished previous unit before starting the new unit,
    # automatically close/update the previous unit row as well!
    if recap_detected and recap_units:
        for prev_uid in recap_units:
            try:
                prev_unit_obj = next((u for u in curriculum if u["unit_id"].lower() == prev_uid.lower()), None)
                prev_row, _ = sheet.find_row_by_unit_id(prev_uid, topic=prev_unit_obj.get("topic") if prev_unit_obj else None)
                if prev_row is not None and prev_row != row_number:
                    prev_prior_pct = sheet.get_prior_cumulative(prev_row)
                    # If previous unit was left unfinished (< 100%)
                    if prev_prior_pct < 100:
                        prev_unit_obj = next((u for u in curriculum if u["unit_id"].lower() == prev_uid.lower()), None)
                        if prev_unit_obj:
                            print(f"[INFO] Evaluating leftover completion for unfinished prior Unit '{prev_uid}' (Row {prev_row})...")
                            prev_res = match_topic(client, model, prev_unit_obj, transcript, previous_cumulative_percent=prev_prior_pct)
                            if prev_res.get("percent_covered_today", 0) > 0:
                                print(f"[INFO] ✅ Multi-Unit Split Class: Updated prior Unit '{prev_uid}' on Row {prev_row} to {prev_res['percent_complete_cumulative']}% ({prev_res['status']}).")
                                sheet.update_progression(
                                    row_number=prev_row,
                                    date_taught=date_str,
                                    status=prev_res["status"],
                                    percent_complete_cumulative=prev_res["percent_complete_cumulative"],
                                    percent_covered_today=prev_res["percent_covered_today"],
                                    left_over=prev_res["left_over"],
                                    classwork_done=prev_res["classwork_done"],
                                    homework_assigned=prev_res["homework_assigned"],
                                    notes=f"Completed in split class: {prev_res['notes']}",
                                )
            except Exception as prev_err:
                print(f"[WARN] Multi-unit prior unit update note for '{prev_uid}': {prev_err}")

    # 9. Email Notifications
    # Send teacher summary
    send_teacher_summary(
        result,
        matched_unit,
        date_str,
        config=config,
        recap_detected=recap_detected,
        review_reasons=review_reasons if requires_review else None,
    )


    # If recap or review was triggered, also notify developer
    if recap_detected or requires_review or warnings:
        alert_notes = warnings.copy()
        if requires_review:
            alert_notes.extend(review_reasons)
        send_developer_alert(
            message=f"Session processed for {matched_unit_id} with notes/warnings.",
            filename=filename,
            config=config,
            warnings=alert_notes,
        )

    # 10. Clean up or archive processed file
    delete_after = str(config.get("DELETE_AUDIO_AFTER_PROCESS", "true")).strip().lower() in ("true", "1", "yes")
    if delete_after:
        try:
            os.remove(audio_path)
            print(f"[DONE] Successfully updated Row {row_number} in Google Sheet and deleted {filename} to save disk space.\n")
        except Exception as e:
            print(f"[WARN] Could not delete audio file {audio_path}: {e}\n")
    else:
        os.makedirs(processed_folder, exist_ok=True)
        shutil.move(audio_path, os.path.join(processed_folder, filename))
        print(f"[DONE] Successfully updated Row {row_number} in Google Sheet and archived {filename}.\n")

    return {
        "success": True,
        "unit_id": matched_unit_id,
        "topic": matched_unit.get("topic", ""),
        "status": result["status"],
        "percent_covered_today": result["percent_covered_today"],
        "percent_complete_cumulative": result["percent_complete_cumulative"],
        "left_over": result["left_over"],
        "classwork_done": result["classwork_done"],
        "homework_assigned": result["homework_assigned"],
        "notes": result["notes"],
        "warnings": warnings,
        "filename": filename,
        "date_str": date_str,
    }

