"""Wrapper around gspread for the CourseProgression.xlsx-style tracking sheet."""

import os
import json
import time
import gspread
from google.oauth2.service_account import Credentials
from typing import Optional, Tuple, Dict, Any

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
]


class SheetClient:
    def __init__(
        self,
        service_account_json: Optional[str] = None,
        sheet_id: str = "",
        tab_name: str = "DAA",
    ):
        json_content_env = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON_CONTENT")
        creds = None

        if json_content_env and json_content_env.strip():
            try:
                data = json.loads(json_content_env.strip())
                creds = Credentials.from_service_account_info(data, scopes=SCOPES)
            except Exception as e:
                print(f"[WARN] Failed to parse GOOGLE_SERVICE_ACCOUNT_JSON_CONTENT: {e}")

        if creds is None and service_account_json:
            stripped = service_account_json.strip()
            if stripped.startswith("{") and stripped.endswith("}"):
                try:
                    data = json.loads(stripped)
                    creds = Credentials.from_service_account_info(data, scopes=SCOPES)
                except Exception:
                    pass

        if creds is None and service_account_json:
            if os.path.exists(service_account_json):
                creds = Credentials.from_service_account_file(service_account_json, scopes=SCOPES)
            else:
                raise FileNotFoundError(f"Service account file not found at: {service_account_json}")

        if creds is None:
            raise ValueError("No valid service account credentials provided (file or GOOGLE_SERVICE_ACCOUNT_JSON_CONTENT).")

        self.creds = creds
        self.sheet_id = sheet_id
        self.tab_name = tab_name
        self.ws = None
        self._init_worksheet()

    def _init_worksheet(self):
        """Initializes gspread client and opens the target worksheet with retry for network/DNS readiness."""
        backoff_delays = [2, 4, 8]
        last_error = None
        for attempt, delay in enumerate(backoff_delays, start=1):
            try:
                gc = gspread.authorize(self.creds)
                sh = gc.open_by_key(self.sheet_id)
                self.ws = sh.worksheet(self.tab_name)
                return
            except Exception as e:
                last_error = e
                print(f"[WARN] Connecting to Google Sheets (attempt {attempt}/3): {e}. Retrying in {delay}s...")
                time.sleep(delay)

        raise RuntimeError(f"Could not connect to Google Sheets after 3 attempts (check internet/DNS connection). Error: {last_error}")


    def _execute_with_retry(self, func, *args, **kwargs):
        """Executes a gspread operation with exponential backoff (3 attempts: 2s, 4s, 8s)."""
        backoff_delays = [2, 4, 8]
        last_error = None
        for attempt, delay in enumerate(backoff_delays, start=1):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                last_error = e
                print(f"[WARN] Google Sheets API call failed (attempt {attempt}/3): {e}. Retrying in {delay}s...")
                time.sleep(delay)
                try:
                    self._init_worksheet()
                except Exception:
                    pass
        raise RuntimeError(f"Google Sheets operation failed after 3 attempts. Error: {last_error}")

    def find_row_by_unit_id(self, unit_id: str, topic: Optional[str] = None) -> Tuple[Optional[int], Optional[Dict[str, Any]]]:
        """Finds the row number (1-indexed) in the sheet matching unit_id or topic."""
        import re

        def _op():
            all_values = self.ws.get_all_values()
            if not all_values:
                return None, None

            # Detect header row dynamically (supports header on Row 1, Row 3, etc.)
            header_idx = 0
            for i, r in enumerate(all_values[:5]):
                if any(str(cell).strip().lower() in ("topic", "unit id") for cell in r):
                    header_idx = i
                    break

            headers = all_values[header_idx]
            target = unit_id.strip().lower()
            target_topic = topic.strip().lower() if topic else ""

            # 1. Exact match in Column A (e.g. 'DAA-18' or 'UNIT-2')
            for idx, row in enumerate(all_values[header_idx + 1:], start=header_idx + 2):
                if not row:
                    continue
                col_a_val = str(row[0]).strip().lower()
                if col_a_val == target:
                    row_dict = {headers[i]: row[i] if i < len(row) else "" for i in range(len(headers))}
                    return idx, row_dict

            # 2. Match by Topic in Column C (3rd column) if topic is provided
            if target_topic:
                for idx, row in enumerate(all_values[header_idx + 1:], start=header_idx + 2):
                    if len(row) > 2 and row[2].strip():
                        sheet_topic = row[2].strip().lower()
                        if sheet_topic == target_topic or target_topic in sheet_topic or sheet_topic in target_topic:
                            row_dict = {headers[i]: row[i] if i < len(row) else "" for i in range(len(headers))}
                            return idx, row_dict

            # 3. Numeric index offset fallback (e.g. DAA-18 -> index 18 -> Row 21)
            num_match = re.search(r"\d+", unit_id)
            if num_match:
                cand_row = int(num_match.group()) + header_idx + 1
                if cand_row <= len(all_values):
                    row = all_values[cand_row - 1]
                    row_dict = {headers[i]: row[i] if i < len(row) else "" for i in range(len(headers))}
                    return cand_row, row_dict

            return None, None

        return self._execute_with_retry(_op)

    def is_already_taught_today(self, row_number: int, today_str: str) -> bool:
        """Checks if Column I (Date Taught) already contains today's date for this unit."""
        try:
            def _op():
                val = self.ws.cell(row_number, 9).value  # Col I is 9th column
                return str(val).strip() if val is not None else ""
            
            existing_date = self._execute_with_retry(_op)
            return existing_date == today_str.strip()
        except Exception:
            return False

    def get_prior_cumulative(self, row_number: int) -> int:
        """Reads existing overall % Complete from Column K (1-indexed col 11) if available."""
        try:
            def _op():
                return self.ws.cell(row_number, 11).value

            val = self._execute_with_retry(_op)
            if val is not None and str(val).strip():
                clean_str = str(val).replace("%", "").strip()
                parsed = float(clean_str)
                # If Google Sheets returned decimal (e.g. 1.0 or 0.6)
                if parsed <= 1.0 and "%" not in str(val):
                    return int(round(parsed * 100))
                elif parsed > 100:
                    # Fix any previous accidental 10000%
                    return min(100, int(round(parsed / 100)))
                return int(round(parsed))
        except Exception:
            pass
        return 0

    def get_prior_today_covered(self, row_number: int) -> int:
        """Reads existing session Covered % from Column L (1-indexed col 12) if available."""
        try:
            def _op():
                return self.ws.cell(row_number, 12).value

            val = self._execute_with_retry(_op)
            if val is not None and str(val).strip():
                clean_str = str(val).replace("%", "").strip()
                parsed = float(clean_str)
                if parsed <= 1.0 and "%" not in str(val):
                    return int(round(parsed * 100))
                elif parsed > 100:
                    return min(100, int(round(parsed / 100)))
                return int(round(parsed))
        except Exception:
            pass
        return 0

    def get_existing_details(self, row_number: int) -> Dict[str, str]:
        """Reads existing Left Over (Col M / 13), Classwork (Col O / 15) and Notes (Col Q / 17) for multi-session appending."""
        try:
            def _op():
                left_over = self.ws.cell(row_number, 13).value or ""
                cw = self.ws.cell(row_number, 15).value or ""
                notes = self.ws.cell(row_number, 17).value or ""
                return {
                    "left_over": str(left_over).strip(),
                    "classwork": str(cw).strip(),
                    "notes": str(notes).strip()
                }
            return self._execute_with_retry(_op)
        except Exception:
            return {"left_over": "", "classwork": "", "notes": ""}

    def update_progression(
        self,
        row_number: int,
        date_taught: str,
        status: str,
        percent_complete_cumulative: int,
        percent_covered_today: int,
        left_over: str,
        classwork_done: Optional[str],
        homework_assigned: Optional[str],
        notes: str,
    ) -> None:
        """Writes evaluation values to Columns I–M and O–Q, leaving Column N (Carry To) untouched.

        Exact CourseProgression.xlsx Column mappings:
        - Col I: Date Taught (Date recording made/processed)
        - Col J: Status (Completed, In Progress, Skipped)
        - Col K: % Complete (The overall completion percentage of the syllabus topic)
        - Col L: Covered (% Covered in that specific daily session)
        - Col M: Left Over (Uncovered sub-topics, empty string if none, never 0)
        - Col N: (Carry To - UNTOUCHED)
        - Col O: Classwork (CW executed: Y / Partial / Other: ... / "")
        - Col P: Homework (HW assigned: Y / Partial / Other: ... / "")
        - Col Q: Notes (Audit note)
        """
        clean_left_over = "" if not left_over or str(left_over).strip() in ("0", "None", "null") else str(left_over).strip()
        clean_classwork = "" if classwork_done is None or str(classwork_done).lower() in ("null", "none") else str(classwork_done).strip()
        clean_homework = "" if homework_assigned is None or str(homework_assigned).lower() in ("null", "none") else str(homework_assigned).strip()

        # Pass formatted percentage string (e.g. '45%', '90%', '100%') to avoid Google Sheets 9000% formatting bugs
        val_k_overall_complete = f"{int(round(percent_complete_cumulative))}%"
        val_l_today_covered = f"{int(round(percent_covered_today))}%"

        # Batch update I:M and O:Q in a single atomic call to preserve Column N
        range_i_m = f"I{row_number}:M{row_number}"
        values_i_m = [[
            str(date_taught),
            str(status),
            val_k_overall_complete,
            val_l_today_covered,
            clean_left_over,
        ]]

        range_o_q = f"O{row_number}:Q{row_number}"
        values_o_q = [[
            clean_classwork,
            clean_homework,
            str(notes),
        ]]

        def _op():
            self.ws.batch_update([
                {"range": range_i_m, "values": values_i_m},
                {"range": range_o_q, "values": values_o_q},
            ], value_input_option="USER_ENTERED")

        self._execute_with_retry(_op)

