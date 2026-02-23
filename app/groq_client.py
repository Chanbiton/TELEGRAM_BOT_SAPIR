"""Groq LLM client — uses GROQ_API_KEY and GROQ_MODEL from config."""
import json
import re
from typing import List, Optional

from app.config import GROQ_API_KEY, GROQ_MODEL


def ask_groq(
    user_message: str,
    system_prompt: str = "You are a helpful assistant. Reply briefly and clearly.",
    max_tokens: int = 1024,
) -> str:
    """Send a message to Groq and return the assistant reply. Sync for simplicity with groq lib."""
    if not GROQ_API_KEY:
        return "Groq is not configured (set GROQ_API_KEY in my_secrets or environment)."
    try:
        from groq import Groq
        client = Groq(api_key=GROQ_API_KEY)
        resp = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            max_tokens=max_tokens,
        )
        if resp.choices and resp.choices[0].message.content:
            return resp.choices[0].message.content.strip()
        return "No response from the model."
    except Exception as e:
        return f"Error: {str(e)}"


# --- Quiz question generation (rules for Groq) ---------------------------------

QUIZ_SYSTEM_PROMPT = """You are a quiz question generator for a developer quiz (DevDuel). Generate ONLY valid JSON, no other text.

CRITICAL: You MUST always return exactly the requested number of valid questions. Never return an empty array. Every subject + difficulty combination is valid — create appropriate questions.

SUBJECTS (choose the one requested):
- leetcode: Coding problems, data structures, Python/JS syntax, array/string manipulation, hash maps, two pointers, sliding window.
- algorithms: Time/space complexity (Big O), data structures (arrays, trees, graphs), NeetCode-style problems, recursion, sorting, searching.
- code_review: Best practices, readability, bugs, refactoring, clean code, naming, design patterns.
- mixed: Vary across leetcode, algorithms, and code_review evenly.

DIFFICULTY (match exactly):
- easy: Beginner. Simple concepts, one-step reasoning. Example: "What is the time complexity of a linear search?" / "Which loop iterates through all elements?"
- medium: Intermediate. Multi-step logic, common patterns. Example: "What data structure is best for LRU cache?" / "Which refactoring improves this code?"
- hard: Advanced. Complex algorithms, edge cases. Example: "What is the optimal approach for finding median in a stream?" / "Identify the memory leak."
- master: Expert/Legend. Tough problems, optimization, subtle bugs. Example: "Design an O(1) get/put LFU cache" / "What makes this code fail under concurrency?"

OUTPUT FORMAT: A single JSON array of question objects. Each object:
- "id": short unique string (e.g. "q1", "leet_2")
- "text": question text (1–2 sentences; may include code snippet)
- "choices": array of exactly 4 strings (A, B, C, D)
- "correct_index": 0, 1, 2, or 3 (index of correct choice)
- "explain": short educational explanation (1–2 sentences)
- "difficulty": "easy" | "medium" | "hard" | "master"

EXAMPLE (easy, leetcode):
[{"id":"q1","text":"What is the time complexity of iterating through an array of n elements?","choices":["O(1)","O(log n)","O(n)","O(n²)"],"correct_index":2,"explain":"Linear scan visits each element once, so O(n).","difficulty":"easy"}]

Write in English. No markdown, no code blocks — output only the raw JSON array."""


def _parse_questions_json(raw: str) -> List[dict]:
    """Parse LLM response into list of question dicts; normalize and validate."""
    out = []
    # Try to extract a JSON array if there's extra text
    raw = raw.strip()
    match = re.search(r'\[[\s\S]*\]', raw)
    if match:
        raw = match.group(0)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    for item in data:
        if not isinstance(item, dict):
            continue
        text = item.get("text") or item.get("question") or ""
        choices = item.get("choices") or []
        if isinstance(choices, dict):
            choices = list(choices.values())
        if len(choices) != 4 or not text:
            continue
        correct = int(item.get("correct_index", 0))
        if correct not in (0, 1, 2, 3):
            correct = 0
        out.append({
            "id": item.get("id") or f"q{len(out)+1}",
            "text": text[:500],
            "choices": [str(c)[:200] for c in choices[:4]],
            "correct_index": correct,
            "explain": (item.get("explain") or "Keep going!")[:400],
            "difficulty": (item.get("difficulty") or "medium").lower(),
        })
    return out


def generate_quiz_questions_groq(
    subject: str,
    difficulty: str,
    count: int,
    exclude_texts: Optional[List[str]] = None,
) -> List[dict]:
    """
    Generate quiz questions via Groq. Subject: leetcode, algorithms, code_review, or mixed.
    Difficulty: easy, medium, hard, master (or legend).
    Returns list of question dicts: id, text, choices (4), correct_index, explain, difficulty.
    """
    if not GROQ_API_KEY or count <= 0:
        return []
    diff = difficulty.lower() if difficulty else "medium"
    if diff == "legend":
        diff = "master"
    subj = subject.lower() if subject else "mixed"
    exclude_hint = ""
    if exclude_texts:
        exclude_hint = f" Do not duplicate or rephrase these questions: {exclude_texts[:3]!r}."
    user = (
        f"Generate exactly {count} quiz question(s) for subject '{subj}' and difficulty '{diff}'."
        f"{exclude_hint} Output only the JSON array."
    )
    raw = ask_groq(user, system_prompt=QUIZ_SYSTEM_PROMPT, max_tokens=4096)
    if raw.startswith("Error:") or raw.startswith("Groq is not configured"):
        return []
    questions = _parse_questions_json(raw)
    if len(questions) < count:
        user_retry = (
            f"Generate exactly {count} multiple-choice quiz questions. Subject: {subj}. Difficulty: {diff}."
            " Output ONLY a JSON array of objects with: id, text, choices (4 strings), correct_index (0-3), explain, difficulty."
        )
        raw2 = ask_groq(user_retry, system_prompt=QUIZ_SYSTEM_PROMPT, max_tokens=4096)
        if not raw2.startswith("Error:") and not raw2.startswith("Groq is not configured"):
            questions2 = _parse_questions_json(raw2)
            if len(questions2) > len(questions):
                questions = questions2
    for q in questions:
        q["difficulty"] = diff
    return questions[:count]
