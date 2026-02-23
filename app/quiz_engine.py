"""Quiz logic: scoring, timer, question formatting. Score 0–100, no penalty."""
import asyncio
import random
from typing import List, Callable, Awaitable, Optional

from app.config import (
    QUESTION_DURATION_SEC,
    SCORE_MAX_TOTAL,
    TIMER_UPDATE_INTERVAL_SEC,
)

# Encouraging phrases during the game (Kahoot-style)
ENCOURAGING_PHRASES = [
    "🔥 You're on fire!",
    "⭐ Great job!",
    "🚀 Keep it up!",
    "💪 You've got this!",
    "🌟 Amazing!",
    "🎯 Nailed it!",
    "✨ So close!",
    "🏆 Champion vibes!",
    "👍 Well done!",
    "🎉 Awesome!",
]

# Practice session complete: random closing line for final summary
PRACTICE_FINISH_PHRASES = [
    "🚀 Keep it up!",
    "📚 Keep learning!",
    "⚡ Great progress!",
    "🌟 Well done!",
    "💪 You've got this!",
    "🎯 Nice work!",
    "✨ Keep going!",
    "🔥 On fire!",
    "📖 Keep reading!",
    "🎉 Awesome session!",
    "👍 Solid effort!",
    "⭐ Star performance!",
]

# Answer choices: 4 different colors, 2 shapes (circle & square) so all render same size.
# Large circles and large squares (U+1F7E0 block) are the same size; triangle/diamond are smaller.
# Each set = 2 circles + 2 squares in 4 different colors, shuffled.
CHOICE_EMOJI_SETS = [
    ["🔴", "🟦", "🟩", "🟨"],   # red circle, blue square, green square, yellow square — 4 colors
    ["🟥", "🟢", "🟦", "🟨"],   # red square, green circle, blue square, yellow square — 4 colors
    ["🟥", "🟩", "🔵", "🟨"],   # red square, green square, blue circle, yellow square — 4 colors
    ["🟥", "🟩", "🟦", "🟡"],   # red square, green square, blue square, yellow circle — 4 colors
]
_LEGACY_EMOJIS = [
    "🔴", "🟢", "🔵", "🟡", "🟥", "🟩", "🟦", "🟨",
]


def get_choice_emojis(count: int = 4) -> list:
    """Return 4 emojis: 4 different colors, 2 shapes (circle & square), all same size."""
    if count != 4:
        return random.sample(_LEGACY_EMOJIS, min(count, len(_LEGACY_EMOJIS)))
    chosen_set = random.choice(CHOICE_EMOJI_SETS)
    return random.sample(chosen_set, 4)


def get_encouraging_phrase() -> str:
    return random.choice(ENCOURAGING_PHRASES)


def get_practice_finish_phrase() -> str:
    """Random closing line for practice session final summary."""
    return random.choice(PRACTICE_FINISH_PHRASES)


def get_answer_recorded_phrase() -> str:
    """Encouraging feedback when user submits an answer (stays visible)."""
    return random.choice([
        "✓ Got it! You're doing great – wait for the reveal! 🚀",
        "✓ Locked in! Stay tuned for the result – you've got this! 💪",
        "✓ Recorded! Almost there – keep that energy! 🔥",
        "✓ Nice! Hold tight – the reveal is coming! ⭐",
    ])


# Short motivating phrases shown after each answer (solo practice)
AFTER_ANSWER_CORRECT = [
    "Keep it up! 🚀",
    "You're on fire! 🔥",
    "Nice one! 💪",
    "Crushing it! ⭐",
    "Well done! 🎯",
    "Champion vibes! 🏆",
    "So sharp! ✨",
]
AFTER_ANSWER_WRONG = [
    "Next one's yours! 💪",
    "Keep going – you've got this! 🚀",
    "Learn and bounce back! 🔥",
    "Stay focused – next question! ⭐",
    "No worries – keep pushing! 💪",
]


def get_after_answer_motivating(correct: bool) -> str:
    """Random short motivating line after answering."""
    return random.choice(AFTER_ANSWER_CORRECT if correct else AFTER_ANSWER_WRONG)


def get_reveal_correct_phrase(points: float) -> str:
    """Immediate feedback: Correct! ✅ +points"""
    return f"Correct! ✅ +{points:.0f} pts. "


def get_reveal_wrong_phrase() -> str:
    """Feedback when user picked a wrong answer."""
    return "Wrong! ✗ "


def get_reveal_time_up_phrase() -> str:
    """Feedback when time ran out and user didn't answer (no 'Wrong!' — not applicable)."""
    return "Time ran out — no answer. "


def format_question_message(
    chapter_index: int,
    total_chapters: int,
    question_index: int,
    questions_in_chapter: int,
    time_left_sec: int,
    question: dict,
    is_bonus: bool = False,
    encouraging: str = "",
    choice_emojis: list = None,
) -> str:
    """Format question with sand clock timer and shape/color emojis per choice. question_index is 1-based."""
    ch = chapter_index + 1
    q = question_index
    timer_display = f"⏳ ┃ {time_left_sec:02d}s ┃"
    bonus_tag = " 🎁 BONUS" if is_bonus else ""
    lines = [
        f"━━━ CHAPTER {ch}/{total_chapters} • Q {q}/{questions_in_chapter}{bonus_tag} ━━━",
        "",
        f"  {timer_display}  ",
        "",
        question.get("text", ""),
        "",
    ]
    if encouraging:
        lines.append(f"  {encouraging}")
        lines.append("")
    choices = question.get("choices", [])
    emojis = choice_emojis or get_choice_emojis(len(choices))
    for i, choice in enumerate(choices):
        label = "ABCD"[i] if i < 4 else str(i + 1)
        em = emojis[i] if i < len(emojis) else "▪️"
        lines.append(f"  {em} {label}) {choice}")
    return "\n".join(lines)


def shuffle_question_choices(question: dict) -> None:
    """Shuffle choices and update correct_index so the correct answer is in a random position. Modifies question in place."""
    choices = question.get("choices", []) or []
    if len(choices) < 2:
        return
    correct_idx = int(question.get("correct_index", 0))
    correct_idx = max(0, min(len(choices) - 1, correct_idx))
    correct_choice = choices[correct_idx]
    indices = list(range(len(choices)))
    random.shuffle(indices)
    new_choices = [choices[i] for i in indices]
    new_correct_idx = new_choices.index(correct_choice)
    question["choices"] = new_choices
    question["correct_index"] = new_correct_idx


def compute_score(
    correct: bool,
    time_left_sec: float,
    total_time_sec: float,
    per_question_max: float,
) -> float:
    """Points for this question. Wrong = 0. Correct = full per_question_max (no time factor). All correct = 100."""
    if not correct:
        return 0.0
    return round(per_question_max, 1)


async def run_timer_edits(
    total_seconds: int,
    edit_fn: Callable[[int], Awaitable[None]],
    on_end: Callable[[], Awaitable[None]],
    early_stop_event: Optional[asyncio.Event] = None,
    all_answered_event: Optional[asyncio.Event] = None,
    wait_while_paused: Optional[Callable[[], Awaitable[None]]] = None,
    cancel_event: Optional[asyncio.Event] = None,
):
    """Update timer every TIMER_UPDATE_INTERVAL_SEC (e.g. 5s): 30→25→…→0, then on_end. Stops early if cancel_event or early_stop_event set."""
    interval = max(1, TIMER_UPDATE_INTERVAL_SEC)
    def should_stop():
        if cancel_event and cancel_event.is_set():
            return True
        if early_stop_event and early_stop_event.is_set():
            return True
        if all_answered_event and all_answered_event.is_set():
            return True
        return False

    try:
        steps = list(range(total_seconds - interval, -1, -interval))
        if not steps and total_seconds > 0:
            steps = [0]
        for s in steps:
            if wait_while_paused:
                await wait_while_paused()
            sleep_task = asyncio.create_task(asyncio.sleep(interval))
            tasks = [sleep_task]
            if early_stop_event:
                tasks.append(asyncio.create_task(early_stop_event.wait()))
            if all_answered_event:
                tasks.append(asyncio.create_task(all_answered_event.wait()))
            if cancel_event:
                tasks.append(asyncio.create_task(cancel_event.wait()))
            if len(tasks) > 1:
                done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for t in pending:
                    t.cancel()
                    try:
                        await t
                    except asyncio.CancelledError:
                        pass
            else:
                await sleep_task
            if wait_while_paused:
                await wait_while_paused()
            if should_stop():
                break
            await edit_fn(s)
        if not should_stop():
            await on_end()
    finally:
        pass


def format_reveal(question: dict, correct_index: int, explain: str, is_bonus: bool = False) -> str:
    """Generic reveal text (correct answer + explain)."""
    bonus_tag = " 🎁 BONUS" if is_bonus else ""
    return f"✅ Correct{bonus_tag}: {question.get('choices', [])[correct_index]}\n\n{explain}"


def format_chapter_results(
    standings: List,
    total_chapters: int,
    chapter_index: int,
    is_solo: bool,
    encouraging: str = "",
) -> str:
    """Chapter results: full table of all participants (0–100 score) and encouraging line."""
    ch = chapter_index + 1
    lines = [
        "╔═══════════════════════════╗",
        f"║  📊 Chapter {ch}/{total_chapters} results  ║",
        "╚═══════════════════════════╝",
        "",
    ]
    if encouraging:
        lines.append(f"  {encouraging}")
        lines.append("")
    if is_solo and standings:
        p = standings[0]
        score_display = max(0, min(100, round(p.score)))
        lines.append(f"  Your score: {score_display}/100")
        lines.append(f"  Correct: {p.correct_count}")
        return "\n".join(lines)
    for i, p in enumerate(standings, 1):
        if i == 1:
            icon = "🏆"
        elif i == 2:
            icon = "🥇"
        elif i == 3:
            icon = "🥈"
        else:
            icon = "  "
        score_display = max(0, min(100, round(p.score)))
        name = (p.display_name.strip() + " " * 14)[:14]
        lines.append(f"  {icon} {i:2}.  {name}  {score_display:3}/100")
    lines.append("")
    lines.append("  ➡️ Next question in a moment…")
    return "\n".join(lines)


def format_after_question_scores(standings: List, question_num: int, total_questions: int) -> str:
    """Compact scoreboard after each question: all players and current scores."""
    lines = [
        f"📊 <b>Scores after Q {question_num}/{total_questions}</b>",
        "",
    ]
    for i, p in enumerate(standings, 1):
        icon = "🏆" if i == 1 else "🥇" if i == 2 else "🥈" if i == 3 else "  "
        score_display = max(0, min(100, round(p.score)))
        name = (p.display_name.strip() + " " * 14)[:14]
        lines.append(f"  {icon} {i:2}.  {name}  {score_display:3}/100")
    return "\n".join(lines)


def format_round_message_one(
    standings: List,
    question_num: int,
    total_questions: int,
    current_user_id: int,
    correct_count: int,
    wrong_count: int,
    chapter_complete_line: Optional[str] = None,
) -> str:
    """Single merged message after each round: leaderboard table (current user emphasized) + your correct/wrong summary. HTML."""
    parts = []
    if chapter_complete_line:
        parts.append(chapter_complete_line)
        parts.append("")
    parts.append(f"📊 <b>Scores after Q {question_num}/{total_questions}</b>")
    parts.append("")
    # Table header (visual)
    parts.append("<pre>┌────┬──────────────────┬────────┐")
    parts.append("│ #  │ Player            │ Score  │")
    parts.append("├────┼──────────────────┼────────┤")
    for i, p in enumerate(standings, 1):
        icon = "🏆" if i == 1 else "🥇" if i == 2 else "🥈" if i == 3 else "  "
        score_display = max(0, min(100, round(p.score)))
        raw_name = p.display_name.strip()[:14]
        if p.user_id == current_user_id:
            name_cell = ("► " + raw_name + " ◄").ljust(16)[:16]
        else:
            name_cell = ("  " + raw_name).ljust(16)[:16]
        parts.append(f"│{icon} {i:2} │ {name_cell} │ {score_display:3}/100 │")
    parts.append("└────┴──────────────────┴────────┘</pre>")
    parts.append("")
    parts.append("📋 <b>Your summary</b>")
    parts.append(f"  ✅ Correct: <b>{correct_count}</b>   <b>❌</b> Wrong: <b>{wrong_count}</b>")
    return "\n".join(parts)


def format_personal_round_feedback(
    rank: int,
    total_players: int,
    score: float,
    correct_count: int,
    encouraging: str = "",
    include_place: bool = True,
) -> str:
    """Per-user feedback after each round: score, correct count; optionally place (skip for solo)."""
    score_display = max(0, min(100, round(score)))
    lines = [
        "━━━ 📋 Your round summary ━━━",
        "",
        f"  🎯 Your score: {score_display}/100",
        f"  ✅ Correct answers: {correct_count}",
    ]
    if include_place:
        place_str = f"{rank}{'st' if rank == 1 else 'nd' if rank == 2 else 'rd' if rank == 3 else 'th'}"
        lines.append(f"  📍 Place: {place_str} of {total_players}")
    lines.append("")
    if encouraging:
        lines.append(f"  {encouraging}")
        lines.append("")
    return "\n".join(lines)


def format_final_leaderboard(standings: List, encouraging: str = "") -> str:
    """Final leaderboard as a clean table. Returns HTML (use parse_mode='HTML')."""
    parts = []
    parts.append("🏁 <b>FINAL LEADERBOARD</b>")
    parts.append("")
    if encouraging:
        parts.append(f"  {encouraging}")
        parts.append("")
    parts.append("<pre>┌────┬──────────────────┬────────┐")
    parts.append("│ #  │ Player            │ Score  │")
    parts.append("├────┼──────────────────┼────────┤")
    for i, p in enumerate(standings, 1):
        icon = "🏆" if i == 1 else "🥇" if i == 2 else "🥈" if i == 3 else "  "
        score = max(0, min(100, round(p.score)))
        name_cell = (p.display_name.strip() + " " * 16)[:16]
        parts.append(f"│{icon} {i:2} │ {name_cell} │ {score:3}/100 │")
    parts.append("└────┴──────────────────┴────────┘</pre>")
    parts.append("")
    parts.append("<i>Min 0 · Max 100 · No minus</i>")
    return "\n".join(parts)
