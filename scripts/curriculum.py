"""Loads structured curriculum data from JSON or CSV files."""

import os
import json
import csv
from typing import List, Dict, Any


def _normalize_list(val: Any) -> List[str]:
    """Convert string/list into a clean list of strings."""
    if isinstance(val, list):
        return [str(x).strip() for x in val if str(x).strip()]
    if isinstance(val, str):
        # Split by newlines or semicolons if present
        if "\n" in val:
            items = val.split("\n")
        elif ";" in val:
            items = val.split(";")
        else:
            items = [val]
        return [item.strip() for item in items if item.strip()]
    if val is None:
        return []
    return [str(val).strip()]


def load_curriculum(path: str) -> List[Dict[str, Any]]:
    """Reads a structured curriculum file (JSON or CSV).

    Expected fields per unit:
    - unit_id (e.g. 'DAA-1')
    - week
    - topic
    - content (list of sub-topics/concepts)
    - classwork (list of classwork items)
    - homework (list of homework items)

    Returns a list of dicts, one per unit.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"Curriculum file not found: {path}")

    ext = os.path.splitext(path)[1].lower()

    if ext == ".json":
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, dict):
            # If wrapped in a parent key like 'units' or 'curriculum'
            for key in ("units", "curriculum", "data", "topics"):
                if key in data and isinstance(data[key], list):
                    data = data[key]
                    break
            else:
                data = [data]

        units = []
        for item in data:
            unit = {
                "unit_id": str(item.get("unit_id") or item.get("Unit ID") or item.get("id") or "").strip(),
                "week": item.get("week") or item.get("Week") or "",
                "topic": str(item.get("topic") or item.get("Topic") or "").strip(),
                "content": _normalize_list(item.get("content") or item.get("Content") or item.get("sub_topics") or []),
                "classwork": _normalize_list(item.get("classwork") or item.get("Classwork") or []),
                "homework": _normalize_list(item.get("homework") or item.get("Homework") or []),
            }
            if unit["unit_id"]:
                units.append(unit)
        return units

    elif ext == ".csv":
        units = []
        with open(path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                unit = {
                    "unit_id": str(row.get("unit_id") or row.get("Unit ID") or row.get("id") or "").strip(),
                    "week": row.get("week") or row.get("Week") or "",
                    "topic": str(row.get("topic") or row.get("Topic") or "").strip(),
                    "content": _normalize_list(row.get("content") or row.get("Content") or ""),
                    "classwork": _normalize_list(row.get("classwork") or row.get("Classwork") or ""),
                    "homework": _normalize_list(row.get("homework") or row.get("Homework") or ""),
                }
                if unit["unit_id"]:
                    units.append(unit)
        return units

    else:
        raise ValueError(f"Unsupported curriculum file format '{ext}'. Expected .json or .csv.")
