"""Helpers: room code, question loading by level, marathon/bonus. Uses Groq when configured."""
import json
import random
import re
import string
from pathlib import Path
from typing import List, Optional

from app.config import (
    QUESTIONS_DIR,
    CHAPTERS,
    QUESTIONS_PER_CHAPTER,
    QUESTIONS_PER_SUBJECT,
    MARATHON_QUESTIONS_TOTAL,
    ROOM_CODE_LENGTH,
    BONUS_QUESTION_CHANCE,
    GROQ_API_KEY,
)
from app.state import rooms

# Subject (mode) to chapter index: Leetcode=0, Algorithms=1, Code Review=2
SUBJECT_TO_CHAPTER = {"leetcode": 0, "algorithms": 1, "code_review": 2}


def generate_room_code() -> str:
    chars = string.ascii_uppercase + string.digits
    for _ in range(50):
        code = "".join(random.choices(chars, k=ROOM_CODE_LENGTH))
        if code not in rooms:
            return code
    raise RuntimeError("Could not generate unique room code")


def _load_chapter_raw(chapter_index: int) -> List[dict]:
    if chapter_index < 0 or chapter_index >= len(CHAPTERS):
        return []
    path = QUESTIONS_DIR / CHAPTERS[chapter_index]
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    questions = data.get("questions", data) if isinstance(data, dict) else data
    return questions if isinstance(questions, list) else []


def _normalize_difficulty(level: Optional[str]) -> Optional[str]:
    """Use 'master' in question filter when user chose Legend (display name)."""
    if not level:
        return level
    return "master" if level.lower() == "legend" else level.lower()


def load_questions_for_chapter(
    chapter_index: int,
    difficulty: Optional[str] = None,
    limit: Optional[int] = None,
) -> List[dict]:
    """Load questions for a chapter, filter by difficulty (easy/medium/hard/master)."""
    raw = _load_chapter_raw(chapter_index)
    if difficulty:
        diff = _normalize_difficulty(difficulty)
        filtered = [q for q in raw if (q.get("difficulty") or "medium").lower() == diff]
        if not filtered:
            filtered = raw
        raw = filtered
    n = limit or QUESTIONS_PER_CHAPTER
    return random.sample(raw, min(len(raw), n)) if len(raw) > n else raw


def get_questions_for_subject(
    subject: str,
    level: Optional[str] = None,
    limit: int = QUESTIONS_PER_SUBJECT,
) -> List[tuple]:
    """One subject (Leetcode / Algorithms / Code Review): questions at chosen difficulty. Uses Groq if configured."""
    ch_idx = SUBJECT_TO_CHAPTER.get(subject, 0)
    if GROQ_API_KEY:
        from app.groq_client import generate_quiz_questions_groq
        qs = generate_quiz_questions_groq(subject, level or "medium", limit)
        if qs:
            return [(ch_idx, q) for q in qs]
        qs = generate_quiz_questions_groq(subject, "medium", limit)
        if qs:
            return [(ch_idx, q) for q in qs]
        qs = generate_quiz_questions_groq("mixed", "easy", limit)
        return [(ch_idx, q) for q in qs]
    qs = load_questions_for_chapter(ch_idx, difficulty=level, limit=limit)
    if not qs:
        qs = load_questions_for_chapter(ch_idx, difficulty=None, limit=limit)
    return [(ch_idx, q) for q in qs]


def get_marathon_questions(level: Optional[str] = None) -> List[tuple]:
    """Marathon: all 3 subjects, 10 questions each (30 total), at chosen difficulty. Uses Groq if configured."""
    out: List[tuple] = []
    lvl = level or "medium"
    if GROQ_API_KEY:
        from app.groq_client import generate_quiz_questions_groq
        for subject, ch_idx in [("leetcode", 0), ("algorithms", 1), ("code_review", 2)]:
            qs = generate_quiz_questions_groq(subject, lvl, QUESTIONS_PER_SUBJECT)
            if not qs:
                qs = generate_quiz_questions_groq(subject, "medium", QUESTIONS_PER_SUBJECT)
            for q in qs:
                out.append((ch_idx, q))
        if not out:
            qs = generate_quiz_questions_groq("mixed", lvl, MARATHON_QUESTIONS_TOTAL)
            out = [(i % 3, q) for i, q in enumerate(qs)]
        random.shuffle(out)
        return out[:MARATHON_QUESTIONS_TOTAL]
    for ch_idx in range(len(CHAPTERS)):
        qs = load_questions_for_chapter(ch_idx, difficulty=level, limit=QUESTIONS_PER_SUBJECT)
        if not qs:
            qs = load_questions_for_chapter(ch_idx, difficulty=None, limit=QUESTIONS_PER_SUBJECT)
        for q in qs:
            out.append((ch_idx, q))
    random.shuffle(out)
    return out[:MARATHON_QUESTIONS_TOTAL]


def get_practice_questions(
    level: Optional[str] = None,
    limit: int = 10,
    subject: Optional[str] = None,
) -> List[tuple]:
    """Practice: mixed or single subject, random order. Uses Groq if configured.
    subject: leetcode, algorithms, code_review, marathon, or None/mixed for mixed."""
    lvl = level or "medium"
    if subject and subject != "mixed":
        if subject == "marathon":
            return get_marathon_questions(lvl)[:limit]
        ch_idx = SUBJECT_TO_CHAPTER.get(subject, 0)
        return get_questions_for_subject(subject, lvl, limit)
    if GROQ_API_KEY:
        from app.groq_client import generate_quiz_questions_groq
        qs = generate_quiz_questions_groq("mixed", lvl, limit)
        if not qs:
            qs = generate_quiz_questions_groq("mixed", "medium", limit)
        if not qs:
            qs = generate_quiz_questions_groq("leetcode", "easy", limit)
        return [(i % 3, q) for i, q in enumerate(qs)] if qs else []
    out: List[tuple] = []
    for ch_idx in range(len(CHAPTERS)):
        qs = load_questions_for_chapter(ch_idx, difficulty=level)
        if not qs:
            qs = load_questions_for_chapter(ch_idx, difficulty=None)
        for q in qs:
            out.append((ch_idx, q))
    random.shuffle(out)
    return out[:limit]


def should_add_bonus() -> bool:
    return random.random() < BONUS_QUESTION_CHANCE


def _bonus_level(quiz_level: str) -> str:
    """Bonus is one step harder than quiz: easy→medium, medium→hard, hard/master→master."""
    lvl = (quiz_level or "medium").lower()
    harder = {"easy": "medium", "medium": "hard", "hard": "master", "master": "master"}
    return harder.get(lvl, "hard")


def get_one_bonus_question(room) -> Optional[tuple]:
    """Get one bonus question at a HIGHER difficulty than the quiz. For Groq: generate 1; for JSON: pick from harder. Returns (ch_idx, q) or None."""
    quiz_level = getattr(room, "level", "medium") or "medium"
    bonus_level = _bonus_level(quiz_level)
    if GROQ_API_KEY:
        from app.groq_client import generate_quiz_questions_groq
        subj = getattr(room, "quiz_mode", "basic")
        if subj not in ("leetcode", "algorithms", "code_review", "marathon"):
            subj = "mixed"
        qs = generate_quiz_questions_groq(subj, bonus_level, 1)
        if qs:
            ch_idx = SUBJECT_TO_CHAPTER.get(subj, 0) if subj != "mixed" else random.randint(0, 2)
            return (ch_idx, qs[0])
        return None
    return pick_bonus_question(exclude_ids=set(), difficulty=bonus_level)


def pick_bonus_question(exclude_ids: set, difficulty: Optional[str] = None) -> Optional[tuple]:
    """Pick a random question from any chapter not in exclude_ids. If difficulty given, only pick questions at that level."""
    all_q: List[tuple] = []
    diff = _normalize_difficulty(difficulty) if difficulty else None
    for ch_idx in range(len(CHAPTERS)):
        raw = _load_chapter_raw(ch_idx)
        for q in raw:
            if q.get("id") in exclude_ids:
                continue
            if diff:
                q_diff = (q.get("difficulty") or "medium").lower()
                if q_diff != diff:
                    continue
            all_q.append((ch_idx, q))
    return random.choice(all_q) if all_q else None


def extract_room_code(text: str) -> Optional[str]:
    """Extract a 6-char room code from pasted text (e.g. 'Code: 2TPWHJ' or full invite message)."""
    if not text or not text.strip():
        return None
    normalized = text.strip().upper()
    # Find any 6-char sequence of A-Z or 0-9 (room code format)
    match = re.search(r"\b([A-Z0-9]{6})\b", normalized)
    if match:
        return match.group(1)
    # No word boundary: take first 6 alphanumeric run
    match = re.search(r"[A-Z0-9]{6}", normalized)
    if match:
        return match.group(0)
    # Fallback: first 6 chars if they are all A-Z0-9
    if len(normalized) >= 6 and re.match(r"^[A-Z0-9]{6}", normalized):
        return normalized[:6]
    return None


def get_room_by_code(code: str) -> Optional["RoomState"]:
    from app.state import RoomState
    normalized = (code or "").strip().upper()
    if len(normalized) != 6:
        return None
    return rooms.get(normalized)


def find_room_for_user(user_id: int) -> Optional["RoomState"]:
    """Find a room where user is host or player (for /end)."""
    from app.state import RoomState
    for room in rooms.values():
        if room.host_id == user_id or user_id in room.players:
            return room
    return None
