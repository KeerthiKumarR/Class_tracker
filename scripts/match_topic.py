"""Audits lecture transcript against curriculum unit content, classwork, and homework."""

import json
import time
from typing import Dict, Any, Optional
from groq import Groq

SYSTEM_PROMPT = """You are an exacting academic auditor tracking syllabus progression from a class transcript,
grounded strictly in the provided curriculum unit data. Never invent content, classwork,
or homework items that aren't in the provided lists, and never reference a unit other than
the one given.

RULE 0 — RECAP ISOLATION (apply before all other rules):
If the transcript contains a recap or review of content belonging to a different unit, treat that portion as invisible for all scoring purposes. Score ONLY the instructional content for THIS unit (if this unit itself is a Revision unit, revision of this unit's stated topics is scored as valid coverage). A recap of unrelated prior material earns zero coverage credit for the current unit.


You will receive: Unit ID, Topic, Content sub-topics list, Classwork list, Homework list,
Prior Cumulative Completion %, and the Class Transcript.

COVERAGE SCORING (percent_covered_today, percent_complete_cumulative):
1. Credit topics that are recapped, referenced, taught, or demonstrated: if the instructor names, discusses, recaps, or writes code for a sub-topic of THIS unit (e.g. mentions frequency counting, lookups, map syntax, hashing collisions), count it as covered.
2. Weight all N sub-topics roughly equally (100/N each). Provide full/generous credit when the concept is mentioned, recapped, or practiced during the lecture.
3. If the instructor walks through, discusses, or solves problems related to this unit's sub-topics, credit the associated content items.
4. percent_covered_today = this session's coverage score of the unit's content (0-100 integer, e.g. 45, 80, 100).
5. percent_complete_cumulative = running total across all sessions on this unit, building on Prior Cumulative Completion (never exceeding 100, never decreasing below the prior value).

LEFT OVER (left_over):
7. List ONLY the specific sub-topics from the Content list that were completely absent or unmentioned in the session. If a topic was named, recapped, or discussed, it is NOT left over.
   Return exactly "" (empty string) if all sub-topics were addressed or if percent_complete_cumulative >= 90 — never 0, never null.

STATUS (status) — must be exactly one of "Completed", "In Progress", or "Skipped"
(matching this sheet's actual dropdown values — do not use "Ongoing" or "Spilled over"):
8. "Completed": cumulative >= 90 AND left_over is empty.
9. "In Progress": cumulative is 1-89, OR left_over is non-empty even if cumulative >= 90.
10. "Skipped": 0% covered this session with no prior cumulative progress.
11. Never mark "Completed" while left_over lists anything outstanding.

CLASSWORK / HOMEWORK (classwork_done, homework_assigned):
12. For classwork_done: List the specific problem names, LeetCode titles/numbers, or algorithms actually solved, coded, or walked through in class (e.g. "LC102 (Level Order Traversal), Queue BFS implementation"). If problems from the curriculum list were solved, include their names; if custom or conceptually equivalent problems were solved, state their problem names/topics. If no problems or coding exercises were solved in class, return null.
13. For homework_assigned: List the specific problem names, LeetCode titles/numbers, or exercises assigned to students for practice or homework (e.g. "LC199 (Right Side View), LC103 (Zigzag Traversal)"). If no homework or practice problems were given/mentioned, return null.
14. Keep the problem names concise and readable (comma-separated if multiple, e.g. "Two Sum, Frequency Counting").

NOTES (notes):
15. Provide a clear, structured summary of what was ACTUALLY TAUGHT, EXPLAINED, AND DEMONSTRATED in today's class (e.g. core theoretical concepts explained, code patterns/syntax demonstrated, step-by-step algorithms walked through, and specific problems solved). Do NOT focus on missing or left-over items (Column M already handles Left Over topics) — instead, highlight the positive instructional takeaways in 2-3 concise, professional sentences.

PROFESSIONAL ACADEMIC FILTER (CRITICAL):
16. Maintain strict, university-grade academic professionalism. Completely IGNORE and NEVER quote casual background chatter, informal side-conversations, personal phone talk, slang, or non-academic remarks.
17. If the transcript is casual chatter or lacks formal lecture teaching, summarize cleanly and neutrally as: "Informal class discussion and review without structured lecture delivery." Never repeat casual slang or informal conversational snippets in the spreadsheet notes.

Respond with ONLY a valid JSON object, no markdown fences, in this exact shape:
{
  "status": "Completed" | "In Progress" | "Skipped",
  "percent_covered_today": <integer 0-100>,
  "percent_complete_cumulative": <integer 0-100>,
  "left_over": "<string, empty string if nothing left over>",
  "classwork_done": "<comma-separated problem names solved in class>" | null,
  "homework_assigned": "<comma-separated problem names assigned for homework>" | null,
  "notes": "<concise professional summary of what was taught and demonstrated>"
}
"""


def _format_list(items: Any) -> str:
    if isinstance(items, list):
        if not items:
            return "None specified"
        return "\n".join(f"- {item}" for item in items)
    return str(items) if items else "None specified"


import re


def _prepare_transcript_for_audit(transcript: str, unit: Dict[str, Any], max_words: int = 3400) -> str:
    """Prepares transcript for topic auditing, guaranteeing that any section solving classwork,
    assigning homework, or explaining syllabus topics is completely preserved.
    """
    words = transcript.split()
    if len(words) <= max_words:
        return transcript

    # 1. Build list of curriculum target terms to prioritize
    target_terms = set()
    for item in unit.get("classwork", []) + unit.get("homework", []) + unit.get("content", []):
        for w in re.findall(r"[A-Za-z0-9_]+", item.lower()):
            if len(w) > 2 and w not in {"the", "and", "for", "with", "from", "using", "use"}:
                target_terms.add(w)

    general_kw = {"problem", "solve", "question", "target", "leetcode", "gfg", "homework", "classwork", "assignment", "lc1", "lc2", "lc3", "lc4"}
    target_terms.update(general_kw)

    # 2. Break transcript into sentence-sized chunks (~50 words each)
    chunks = []
    chunk_size = 50
    for i in range(0, len(words), chunk_size):
        chunk_text = " ".join(words[i:i + chunk_size])
        score = sum(1 for kw in target_terms if kw in chunk_text.lower())
        chunks.append((i, chunk_text, score))

    # 3. Always include head (~800 words), tail (~400 words), and all high-scoring problem/concept chunks
    selected_indices = set(range(0, min(len(chunks), 16)))  # First ~800 words
    selected_indices.update(range(max(0, len(chunks) - 8), len(chunks)))  # Last ~400 words

    # Sort remaining chunks by relevance score and pick until max_words
    middle_chunks = [(idx, score) for idx, _, score in chunks if idx not in selected_indices and score > 0]
    middle_chunks.sort(key=lambda x: x[1], reverse=True)

    max_chunks = max_words // chunk_size
    for idx, _ in middle_chunks:
        if len(selected_indices) >= max_chunks:
            break
        selected_indices.add(idx)

    # Assemble selected chunks in original chronological order
    final_chunks = [chunks[i][1] for i in sorted(selected_indices)]
    return "\n\n".join(final_chunks)


def match_topic(
    client: Groq,
    model: str,
    unit: Dict[str, Any],
    transcript: str,
    previous_cumulative_percent: int = 0,
) -> Dict[str, Any]:
    """Audits the transcript against the curriculum unit with backoff retries and returns the evaluation dictionary."""
    unit_id = unit.get("unit_id", "")
    topic = unit.get("topic", "")
    content = unit.get("content", [])
    classwork = unit.get("classwork", [])
    homework = unit.get("homework", [])

    audit_transcript = _prepare_transcript_for_audit(transcript, unit, max_words=3400)


    user_content = (
        f"Unit ID: {unit_id}\n"
        f"Topic: {topic}\n\n"
        f"Curriculum Content Sub-topics (100% = all covered):\n{_format_list(content)}\n\n"
        f"Curriculum Expected Classwork:\n{_format_list(classwork)}\n\n"
        f"Curriculum Expected Homework:\n{_format_list(homework)}\n\n"
        f"Prior Cumulative Completion: {previous_cumulative_percent}%\n\n"
        f"Class Transcript:\n{audit_transcript}"
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
                    max_tokens=600,
                    temperature=0.1,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_content},
                    ],
                )
                raw_text = response.choices[0].message.content.strip()

                # Clean up fences or reasoning tags
                cleaned = raw_text.replace("```json", "").replace("```", "").strip()
                if "<think>" in cleaned and "</think>" in cleaned:
                    cleaned = cleaned.split("</think>")[-1].strip()

                result = json.loads(cleaned)

                required_fields = [
                    "status",
                    "percent_covered_today",
                    "percent_complete_cumulative",
                    "left_over",
                    "classwork_done",
                    "homework_assigned",
                    "notes",
                ]
                for field in required_fields:
                    if field not in result:
                        raise ValueError(f"Missing field '{field}' in response: {result}")

                # Ensure left_over is string, never None
                if result["left_over"] is None:
                    result["left_over"] = ""
                result["left_over"] = str(result["left_over"]).strip()

                return result

            except Exception as e:
                print(f"[WARN] Topic matching on model '{candidate}' attempt {attempt}/3 failed: {e}. Retrying in {delay}s...")
                last_error = e
                time.sleep(delay)

    raise RuntimeError(f"All LLM candidate models failed for topic matching. Last error: {last_error}")

