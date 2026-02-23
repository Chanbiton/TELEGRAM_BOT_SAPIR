"""Inline keyboards — menu on screen (no reply keyboard)."""
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton

# Room code length for join
JOIN_CODE_LENGTH = 6

# Card-style separators (original theme, game-like)
SEP = "─────────────────"

# Back navigation (step-by-step to home)
BACK_HOME = "← Back"


def main_menu_inline() -> InlineKeyboardMarkup:
    """Main menu (original): Create Room, Join Room, Practice."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎮 Create Room", callback_data="menu_create")],
        [InlineKeyboardButton(text="🔗 Join Room", callback_data="menu_join")],
        [InlineKeyboardButton(text="📚 Practice", callback_data="menu_practice")],
    ])


def level_inline() -> InlineKeyboardMarkup:
    """Difficulty for Create flow (first step; then mode/chapters)."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🟢 Easy", callback_data="level_easy"),
            InlineKeyboardButton(text="🟡 Medium", callback_data="level_medium"),
        ],
        [
            InlineKeyboardButton(text="🟠 Hard", callback_data="level_hard"),
            InlineKeyboardButton(text="🔴 Legend", callback_data="level_master"),
        ],
        [InlineKeyboardButton(text=BACK_HOME, callback_data="back_home")],
    ])


def practice_count_inline() -> InlineKeyboardMarkup:
    """How many questions for practice: 5, 10, 15, 20."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="5", callback_data="practice_count_5"),
            InlineKeyboardButton(text="10", callback_data="practice_count_10"),
        ],
        [
            InlineKeyboardButton(text="15", callback_data="practice_count_15"),
            InlineKeyboardButton(text="20", callback_data="practice_count_20"),
        ],
        [InlineKeyboardButton(text=BACK_HOME, callback_data="back_home")],
    ])


def practice_level_inline(from_question_count: bool = False) -> InlineKeyboardMarkup:
    """Difficulty for Practice only — starts quiz immediately, no participants."""
    back_data = "back_to_practice_count" if from_question_count else "back_home"
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🟢 Easy", callback_data="practice_level_easy"),
            InlineKeyboardButton(text="🟡 Medium", callback_data="practice_level_medium"),
        ],
        [
            InlineKeyboardButton(text="🟠 Hard", callback_data="practice_level_hard"),
            InlineKeyboardButton(text="🔴 Legend", callback_data="practice_level_master"),
        ],
        [InlineKeyboardButton(text=BACK_HOME, callback_data=back_data)],
    ])


def mode_inline() -> InlineKeyboardMarkup:
    """Leetcode, Algorithms, Code Review, Marathon."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📖 Leetcode", callback_data="mode_leetcode")],
        [InlineKeyboardButton(text="📐 Algorithms", callback_data="mode_algorithms")],
        [InlineKeyboardButton(text="🔍 Code Review", callback_data="mode_code_review")],
        [InlineKeyboardButton(text="🏃 Marathon", callback_data="mode_marathon")],
        [InlineKeyboardButton(text=BACK_HOME, callback_data="back_to_level")],
    ])


def participants_count_inline():
    """1–10 as inline (first step of create flow)."""
    row1 = [InlineKeyboardButton(text=str(i), callback_data=f"part_{i}") for i in range(1, 6)]
    row2 = [InlineKeyboardButton(text=str(i), callback_data=f"part_{i}") for i in range(6, 11)]
    return InlineKeyboardMarkup(inline_keyboard=[row1, row2, [InlineKeyboardButton(text=BACK_HOME, callback_data="back_home")]])


def build_lobby_keyboard(room, viewer_id: int):
    """
    Inline keyboard under lobby card. Host sees: Copy code, Start Game (when ready), Cancel.
    Host can start anytime (up to 10 players).
    """
    if viewer_id != room.host_id:
        return None
    buttons = [
        [InlineKeyboardButton(text="📋 Copy code", callback_data=f"lobby_copy:{room.code}")],
        [InlineKeyboardButton(text="🚀 Start Game!", callback_data=f"lobby_start:{room.code}")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data=f"lobby_cancel:{room.code}")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def lobby_keyboard(room, is_host: bool):
    """Compat: build_lobby_keyboard(room, viewer_id) with is_host from room.host_id."""
    if not is_host:
        return None
    return build_lobby_keyboard(room, room.host_id)


def question_choices_keyboard(question, room_code: str = "", locked: bool = False, choice_emojis: list = None):
    """A/B/C/D with shape/color emoji per choice. choice_emojis: list of 4 emojis (random per question)."""
    choices = question.get("choices", [])
    labels = "ABCD"
    if not choice_emojis:
        from app.quiz_engine import get_choice_emojis
        choice_emojis = get_choice_emojis(min(4, len(choices)))
    row = []
    for i in range(min(4, len(choices))):
        label = labels[i]
        em = choice_emojis[i] if i < len(choice_emojis) else "▪️"
        choice_text = choices[i][:25] + "…" if len(choices[i]) > 25 else choices[i]
        btn_text = f"{em} {label}" if locked else f"{em} {label}"
        if locked:
            row.append(InlineKeyboardButton(text=btn_text, callback_data="noop"))
        else:
            row.append(InlineKeyboardButton(text=btn_text, callback_data=f"ans:{room_code}:{i}"))
    return InlineKeyboardMarkup(inline_keyboard=[row] if row else [])


def _code_box(content: str, width: int = 10) -> str:
    """Bordered box for code (reference style: thin border, code centered)."""
    content = content.strip()
    w = max(len(content), width)
    top = "┌" + "─" * w + "┐"
    mid = "│" + content.center(w) + "│"
    bot = "└" + "─" * w + "┘"
    return f"{top}\n{mid}\n{bot}"


def format_pin_entry_screen(current_code: str = "", error: str = None) -> str:
    """
    PIN entry screen: header, code box (placeholders or typed), reference style.
    """
    code = (current_code or "").upper()[:JOIN_CODE_LENGTH]
    parts = [c if c else "•" for c in (code + " " * JOIN_CODE_LENGTH)[:JOIN_CODE_LENGTH]]
    slots = " ".join(parts)
    box = _code_box(slots, width=JOIN_CODE_LENGTH * 2)
    lines = [
        "🎮 Join a Room",
        "",
        "Enter the 6-character code",
        "",
        box,
        "",
        "Type the code in the chat and send.",
    ]
    if error:
        lines.append("")
        lines.append(f"❌ {error}")
    return "\n".join(lines)


def join_code_box_text(current_code: str, error: str = None) -> str:
    """Alias for PIN entry screen (keeps callers simple)."""
    return format_pin_entry_screen(current_code, error)


def join_pin_screen_keyboard() -> InlineKeyboardMarkup:
    """Back to home; code is entered by typing in chat."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=BACK_HOME, callback_data="back_home")],
    ])


def join_code_keyboard() -> InlineKeyboardMarkup:
    """Optional numeric keypad (1–9, 0, ⌫, Join). Use for keypad-based entry if enabled."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=c, callback_data=f"join_key:{c}") for c in "123"],
        [InlineKeyboardButton(text=c, callback_data=f"join_key:{c}") for c in "456"],
        [InlineKeyboardButton(text=c, callback_data=f"join_key:{c}") for c in "789"],
        [
            InlineKeyboardButton(text="⌫", callback_data="join_key:DEL"),
            InlineKeyboardButton(text="0", callback_data="join_key:0"),
            InlineKeyboardButton(text="✅ Join", callback_data="join_key:GO"),
        ],
        [InlineKeyboardButton(text=BACK_HOME, callback_data="back_home")],
    ])


def create_name_box_text(default_name: str = "") -> str:
    """Card-style name entry — header, subheader, separator."""
    hint = (default_name or "Your name")[:16]
    inner = hint.center(14)
    return (
        "🎮 Create a Room\n\n"
        "Your name\n"
        f"{SEP}\n"
        f"  {inner}\n"
        f"{SEP}\n\n"
        "Type your name and send."
    )


def create_name_skip_inline() -> InlineKeyboardMarkup:
    """Back to chapters (mode) step. User must type their name."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=BACK_HOME, callback_data="back_to_mode")],
    ])


def join_name_keyboard() -> InlineKeyboardMarkup:
    """Join name step: Use Telegram name + Back (inline for convenience)."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Use my Telegram name", callback_data="join_skip_name")],
        [InlineKeyboardButton(text=BACK_HOME, callback_data="back_to_pin")],
    ])


def lobby_message_text(room) -> str:
    """Lobby with 'Game created!' and code in bordered box (reference style)."""
    code = room.code
    names = ", ".join(p.display_name for p in room.players.values()) or "—"
    box = _code_box(code, width=max(6, len(code)))
    return (
        "🎮 Game created!\n\n"
        "Share this code with friends:\n\n"
        f"{box}\n\n"
        f"Players joined: {room.joined_count}/{room.expected_players}\n"
        f"{names}\n\n"
        "Press Start when everyone is in!"
    )


def format_copy_code_message(code: str) -> str:
    """Forwardable message: code + short join steps (stay inside bot)."""
    return (
        "🎮 Join this quiz room\n\n"
        f"{SEP}\n"
        f"  Code: {code}\n"
        f"{SEP}\n\n"
        "1) Open this bot\n"
        "2) Press Join Room\n"
        "3) Enter code above"
    )


def next_chapter_keyboard(room_code: str, is_host: bool):
    if not is_host:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Next chapter ▶", callback_data=f"next_ch:{room_code}")]
    ])


def main_menu() -> ReplyKeyboardMarkup:
    """Legacy reply keyboard (empty)."""
    return ReplyKeyboardMarkup(keyboard=[], resize_keyboard=True)


def remove_keyboard():
    return ReplyKeyboardMarkup(keyboard=[], resize_keyboard=True)
