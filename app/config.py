"""Bot configuration — loads from my_secrets.py, then os.environ (no .env required)."""
import os
import sys
from pathlib import Path

# Ensure project root is on path when running as python -m app.main
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Secrets: my_secrets.py first, then environment variables
try:
    import my_secrets
    _t = getattr(my_secrets, "TOKEN", "") or getattr(my_secrets, "BOT_TOKEN", "")
    BOT_TOKEN = _t or os.getenv("TOKEN", "") or os.getenv("BOT_TOKEN", "")
    _g = getattr(my_secrets, "GROQ_API_KEY", "")
    GROQ_API_KEY = _g or os.getenv("GROQ_API_KEY", "")
    _m = getattr(my_secrets, "GROQ_MODEL", "")
    GROQ_MODEL = _m or os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
except ImportError:
    BOT_TOKEN = os.getenv("TOKEN", "") or os.getenv("BOT_TOKEN", "")
    GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
    GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")

# Quiz — score 0–100 total, no penalty for wrong
QUESTIONS_DIR = Path(__file__).resolve().parent.parent / "questions"
CHAPTERS = ["chapter1.json", "chapter2.json", "chapter3.json"]  # Leetcode, Algorithms, Code Review
QUESTIONS_PER_SUBJECT = 10   # each subject (Leetcode / Algorithms / Code Review) gives 10 questions
MARATHON_QUESTIONS_TOTAL = 30  # marathon = all 3 subjects, 10 each
QUESTIONS_PER_CHAPTER = int(os.getenv("QUESTIONS_PER_CHAPTER", "10"))
QUESTIONS_MAX_PER_QUIZ = 30
QUESTION_DURATION_SEC = int(os.getenv("QUESTION_DURATION_SEC", "30"))
SCORE_MAX_TOTAL = 100  # total quiz score cap
# Per-question: 95% from correctness, 5% from speed; wrong = 0
BASE_POINTS = 0.95   # fraction of per-question max (correctness)
SPEED_BONUS_MAX = 0.05  # fraction of per-question max (speed bonus)

# Room
ROOM_CODE_LENGTH = 6
ROOM_IDLE_TIMEOUT_MIN = int(os.getenv("ROOM_IDLE_TIMEOUT_MIN", "10"))
TIMER_UPDATE_INTERVAL_SEC = 5  # countdown step: show 30s → 25s → 20s → … → 0s every 5 seconds
BONUS_QUESTION_CHANCE = 0.5  # 50% chance of bonus question (helps slower users reach 100)
