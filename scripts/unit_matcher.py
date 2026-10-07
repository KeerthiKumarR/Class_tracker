"""Identifies which curriculum unit a lecture transcript matches, with recap detection."""

import re
import json
import time
from typing import List, Dict, Any, Optional
from groq import Groq

SYSTEM_PROMPT = """You are an academic curriculum classifier with recap detection capability.

Given a class lecture transcript and a catalog of curriculum units (each with unit_id, topic, and sub-topics):
1. Identify which single primary unit_id the transcript most closely covers for the main lecture topic.
2. Note on "Revision" units: If the curriculum contains a unit specifically dedicated to revision (e.g. "DSA Basics Quick Revision" for DAA-5), and the instructor is revising those foundational concepts (recursion, call stack, linked lists, factorial), match directly to that Revision unit as the primary unit.
3. Detect if the lecture begins with a brief casual warm-up recap of yesterday's specific topic before pivoting to a completely different lesson.
4. If a separate prior unit recap is detected, list the recap_unit_ids and set recap_detected to true.
5. STRICT DOMAIN ISOLATION (Out-of-Domain / Non-Curriculum Classes):
   If the lecture is on an unrelated subject outside this curriculum (e.g., Web Development, HTML/CSS, React, JavaScript, Node.js, Machine Learning, DBMS, general casual banter, or faculty meetings), you MUST set "primary_unit_id": null and "confidence": "low". NEVER force-fit an unrelated class into this curriculum.
6. If no confident match exists within the catalog, set primary_unit_id to null.

Strict Rules:
- Grounding: Choose ONLY unit_ids that explicitly appear in the provided curriculum catalog.
- Matching by Meaning: Match based on core technical concepts and algorithms taught, accounting for mixed Hindi/English speech.

Respond with ONLY a JSON object, no markdown fences, in this exact shape:
{
  "primary_unit_id": "<EXACT_MATCHED_UNIT_ID>" | null,
  "recap_unit_ids": ["<UNIT_ID>"],
  "confidence": "high" | "medium" | "low",
  "recap_detected": true | false,
  "reason": "<short sentence explaining the classification>"
}
"""

RECAP_PHRASES = [
    r"last class",
    r"yesterday",
    r"previous class",
    r"previous session",
    r"recap",
    r"revise",
    r"revision",
    r"as we saw",
    r"as we discussed",
    r"pichli class",
    r"kal humne",
]

def _check_heuristic_recap(transcript: str) -> bool:
    """Checks if opening 20% of transcript has obvious recap phrases."""
    if not transcript:
        return False
    words = transcript.split()
    opening_text = " ".join(words[:max(30, int(len(words) * 0.20))]).lower()
    for phrase in RECAP_PHRASES:
        if re.search(r"\b" + phrase + r"\b", opening_text):
            return True
    return False


def _prepare_transcript_for_matching(transcript: str, max_words: int = 1600) -> str:
    """Prepares transcript for unit classification, sampling key segments if long to stay under Groq TPM limits."""
    words = transcript.split()
    if len(words) <= max_words:
        return transcript

    # Sample beginning (agenda/intro), middle (core concept), and end (wrap-up/CW/HW)
    head = words[:900]
    mid_start = max(900, (len(words) // 2) - 250)
    mid = words[mid_start:mid_start + 500]
    tail = words[-200:]
    return f"{' '.join(head)}\n\n[... middle class excerpt ...]\n{' '.join(mid)}\n\n[... final class excerpt ...]\n{' '.join(tail)}"


def find_matching_unit(
    transcript: str,
    curriculum: List[Dict[str, Any]],
    client: Groq,
    model: str,
) -> Dict[str, Any]:
    """Matches a transcript against curriculum units with recap detection and exponential backoff.

    Returns a dict:
    {
      "primary_unit_id": "DAA-2" | None,
      "recap_unit_ids": ["DAA-1"],
      "confidence": "high" | "medium" | "low",
      "recap_detected": bool,
      "reason": str,
    }
    """
    default_res = {
        "primary_unit_id": None,
        "recap_unit_ids": [],
        "confidence": "low",
        "recap_detected": False,
        "reason": "Empty transcript or curriculum",
    }

    if not curriculum or not transcript or not transcript.strip():
        return default_res

    valid_unit_ids = {u["unit_id"] for u in curriculum if u.get("unit_id")}
    if not valid_unit_ids:
        return default_res

    heuristic_recap = _check_heuristic_recap(transcript)
    sampled_transcript = _prepare_transcript_for_matching(transcript, max_words=1600)

    # Format curriculum catalog
    catalog_lines = []
    for u in curriculum:
        uid = u.get("unit_id", "")
        topic = u.get("topic", "")
        content = u.get("content", [])
        content_str = ", ".join(content) if isinstance(content, list) else str(content)
        catalog_lines.append(f"- Unit ID: {uid}\n  Topic: {topic}\n  Content/Sub-topics: {content_str}")

    curriculum_text = "\n\n".join(catalog_lines)
    user_content = (
        f"Curriculum Catalog:\n{curriculum_text}\n\n"
        f"Class Transcript:\n{sampled_transcript}"
    )

    candidate_models = [
        model,
        "qwen/qwen3.8-27b",
        "openai/gpt-oss-120b",
        "groq/compound",
        "openai/gpt-oss-20b",
    ]
    seen_models = set()
    unique_candidates = []
    for m in candidate_models:
        if m and m not in seen_models:
            seen_models.add(m)
            unique_candidates.append(m)


    last_error = None
    for candidate in unique_candidates:

        # Exponential backoff: 3 attempts with 2s, 4s, 8s
        for attempt, delay in enumerate([2, 4, 8], start=1):
            try:
                response = client.chat.completions.create(
                    model=candidate,
                    max_tokens=300,
                    temperature=0.0,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_content},
                    ],
                )
                raw_text = response.choices[0].message.content.strip()
                cleaned = raw_text.replace("```json", "").replace("```", "").strip()
                if "<think>" in cleaned and "</think>" in cleaned:
                    cleaned = cleaned.split("</think>")[-1].strip()

                parsed = json.loads(cleaned)
                raw_primary = parsed.get("primary_unit_id") or parsed.get("unit_id")
                confidence = parsed.get("confidence", "medium")
                recap_detected = bool(parsed.get("recap_detected", False) or heuristic_recap)
                recap_units = parsed.get("recap_unit_ids", [])
                reason = parsed.get("reason", "")

                # Validate primary unit ID against catalog
                primary_matched = None
                if raw_primary and str(raw_primary).lower() not in ("null", "none", ""):
                    for valid_id in valid_unit_ids:
                        if str(raw_primary).strip().lower() == valid_id.lower():
                            primary_matched = valid_id
                            break

                # Validate recap unit IDs
                valid_recaps = []
                if isinstance(recap_units, list):
                    for r_id in recap_units:
                        for valid_id in valid_unit_ids:
                            if str(r_id).strip().lower() == valid_id.lower() and valid_id not in valid_recaps:
                                valid_recaps.append(valid_id)

                result_dict = {
                    "primary_unit_id": primary_matched,
                    "recap_unit_ids": valid_recaps,
                    "confidence": confidence if primary_matched else "low",
                    "recap_detected": recap_detected,
                    "reason": reason,
                }

                if primary_matched:
                    print(f"[INFO] Unit Matcher: Matched to '{primary_matched}' (confidence: {confidence}, recap: {recap_detected}) — {reason}")
                else:
                    print(f"[INFO] Unit Matcher: No confident match found ({reason})")

                return result_dict

            except Exception as e:
                last_error = e
                print(f"[WARN] Unit matching on model '{candidate}' attempt {attempt}/3 failed: {e}. Retrying in {delay}s...")
                time.sleep(delay)

    print(f"[ERROR] All candidate models failed for unit matching. Last error: {last_error}")
    return default_res

