"""In-memory state: rooms and players. No database."""
from dataclasses import dataclass, field
from typing import Dict, Optional
from enum import Enum
import asyncio


class RoomStatus(str, Enum):
    LOBBY = "lobby"
    RUNNING = "running"
    FINISHED = "finished"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


@dataclass
class Player:
    user_id: int
    display_name: str
    score: float = 0.0  # 0–100 scale
    correct_count: int = 0
    total_response_time_ms: float = 0.0  # for tie-breaker
    answer_count: int = 0


@dataclass
class RoomState:
    code: str
    host_id: int
    expected_players: int
    players: Dict[int, Player] = field(default_factory=dict)
    status: RoomStatus = RoomStatus.LOBBY
    lobby_message_id: Optional[int] = None
    lobby_chat_id: Optional[int] = None
    chapter_index: int = 0
    question_index: int = 0
    current_question: Optional[dict] = None
    question_end_ts: float = 0.0
    answers: Dict[int, int] = field(default_factory=dict)
    answer_time_left: Dict[int, float] = field(default_factory=dict)
    question_message_ids: Dict[int, int] = field(default_factory=dict)
    idle_task: Optional[asyncio.Task] = None
    timer_tasks: list = field(default_factory=list)
    # Quiz mode: "basic" = standard (1 chapter), "extended" = 2 chapters, "marathon" = 3
    quiz_mode: str = "basic"
    # Difficulty: easy, medium, hard, master
    level: str = "medium"
    # For /end or /stop: set to True to stop quiz loop
    _cancelled: bool = False
    # For /pause and /continue: host can pause the timer
    _paused: bool = False
    _pause_event: Optional[asyncio.Event] = field(default=None, repr=False)
    # Total number of questions (for scoring per_question_max)
    total_questions: int = 0
    # Practice: requested number of questions (5, 10, 15, 20). None = use default cap.
    num_questions: Optional[int] = None
    # Current question choice emojis (shape/color per A/B/C/D)
    current_choice_emojis: list = field(default_factory=list)

    @property
    def joined_count(self) -> int:
        return len(self.players)

    def is_full(self) -> bool:
        return self.joined_count >= self.expected_players

    def get_sorted_standings(self):
        """Sort by: score desc, correct_count desc, faster avg response time."""
        def key(p: Player):
            avg = p.total_response_time_ms / p.answer_count if p.answer_count else float("inf")
            return (-p.score, -p.correct_count, avg)
        return sorted(self.players.values(), key=key)


# Global in-memory store
rooms: Dict[str, RoomState] = {}
