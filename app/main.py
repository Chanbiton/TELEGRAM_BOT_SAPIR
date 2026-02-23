"""
Telegram Quiz Bot — Kahoot-like multiplayer quiz.
In-memory state only. aiogram v3.
"""
import asyncio
import time
import logging

try:
    from aiogram import Bot, Dispatcher, F, Router
    from aiogram.filters import Command
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.memory import MemoryStorage
    from aiogram.types import Message, CallbackQuery
except ModuleNotFoundError as e:
    print("Missing dependency. From project root with venv activated run:")
    print("  pip install -r requirements.txt")
    raise

from app.config import (
    BOT_TOKEN,
    QUESTION_DURATION_SEC,
    ROOM_IDLE_TIMEOUT_MIN,
    CHAPTERS,
)
from app.state import (
    RoomStatus,
    RoomState,
    Player,
    rooms,
)
from app.utils import (
    generate_room_code,
    get_room_by_code,
    find_room_for_user,
    extract_room_code,
    get_marathon_questions,
    get_questions_for_subject,
    get_practice_questions,
)
from app.keyboards import (
    main_menu_inline,
    main_menu,
    level_inline,
    practice_count_inline,
    practice_level_inline,
    practice_subject_inline,
    practice_another_round_inline,
    practice_same_difficulty_inline,
    mode_inline,
    lobby_keyboard,
    build_lobby_keyboard,
    question_choices_keyboard,
    remove_keyboard,
    join_code_box_text,
    join_pin_screen_keyboard,
    join_name_keyboard,
    create_name_box_text,
    create_name_skip_inline,
)
from app.renderers import render_lobby_text, render_invite_message
from app.quiz_engine import (
    format_question_message,
    format_reveal,
    format_personal_round_feedback,
    format_final_leaderboard,
    format_after_question_scores,
    format_round_message_one,
    compute_score,
    run_timer_edits,
    shuffle_question_choices,
    get_encouraging_phrase,
    get_practice_finish_phrase,
    get_choice_emojis,
    get_answer_recorded_phrase,
    get_reveal_correct_phrase,
    get_reveal_wrong_phrase,
    get_reveal_time_up_phrase,
    get_after_answer_motivating,
)
from app.config import SCORE_MAX_TOTAL, QUESTIONS_MAX_PER_QUIZ, QUESTIONS_PER_SUBJECT, MARATHON_QUESTIONS_TOTAL

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()

# --- FSM States ---
from aiogram.fsm.state import State, StatesGroup


class CreateStates(StatesGroup):
    waiting_level = State()
    waiting_mode = State()
    waiting_participants_count = State()
    waiting_host_name = State()


class JoinStates(StatesGroup):
    waiting_code = State()
    waiting_name = State()


class PracticeStates(StatesGroup):
    waiting_question_count = State()
    waiting_level = State()
    waiting_subject = State()


# --- Helpers ---
HOME_MESSAGE = (
    "👋 <b>Welcome to DevDuel Quiz!</b>\n\n"
    "Practice your skills, compete in quiz battles, and learn something new – all while having fun! 🚀\n\n"
    "Tap an option below:"
)


def get_display_name(user) -> str:
    """Default Telegram name if user doesn't type one."""
    return (user.first_name or user.username or "Player").strip() or "Player"


def display_level_name(level: str) -> str:
    """User-facing difficulty name: Master for master, else capitalized."""
    return "Master" if (level or "").lower() == "master" else (level or "Medium").capitalize()


async def update_lobby_message(room: RoomState):
    """Edit lobby message with current joined list (HTML card + <pre> box)."""
    if room.lobby_chat_id is None or room.lobby_message_id is None:
        return
    text = render_lobby_text(room)
    kb = build_lobby_keyboard(room, room.host_id)
    try:
        await bot.edit_message_text(
            chat_id=room.lobby_chat_id,
            message_id=room.lobby_message_id,
            text=text,
            reply_markup=kb,
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning("Lobby edit failed: %s", e)


async def cancel_room_and_notify(room: RoomState, reason: str):
    """Set room cancelled/expired and notify all players."""
    room.status = RoomStatus.CANCELLED if "cancel" in reason.lower() else RoomStatus.EXPIRED
    if room.idle_task and not room.idle_task.done():
        room.idle_task.cancel()
        try:
            await room.idle_task
        except asyncio.CancelledError:
            pass
    msg = "❌ Room was cancelled by the host." if RoomStatus.CANCELLED else "⏱ Room expired (idle timeout)."
    for uid, p in list(room.players.items()):
        try:
            await bot.send_message(uid, msg)
        except Exception:
            pass
    if room.code in rooms:
        del rooms[room.code]


def _build_question_list(room: RoomState):
    """Build question list: practice = subject + difficulty; multiplayer = one subject (10) or marathon (30)."""
    is_solo = room.expected_players == 1
    if is_solo:
        limit = getattr(room, "num_questions", None) or QUESTIONS_PER_SUBJECT
        subject = getattr(room, "quiz_mode", "basic")
        if subject not in ("leetcode", "algorithms", "code_review", "marathon"):
            subject = "mixed"
        items = get_practice_questions(room.level, limit=limit, subject=subject)
        return [(ch_idx, q, False) for ch_idx, q in items]
    if room.quiz_mode == "marathon":
        items = get_marathon_questions(room.level)
        return [(ch_idx, q, False) for ch_idx, q in items]
    # Single subject: Leetcode, Algorithms, or Code Review — 10 questions each
    items = get_questions_for_subject(room.quiz_mode, room.level, limit=QUESTIONS_PER_SUBJECT)
    return [(ch_idx, q, False) for ch_idx, q in items]


async def start_quiz_for_room(room: RoomState):
    """Run quiz: basic (1 chapter) or marathon (all 3), level, 0–100 score, timer every second."""
    room.status = RoomStatus.RUNNING
    room._cancelled = False
    # Show "Preparing…" immediately so start doesn't feel slow
    for uid in list(room.players.keys()):
        try:
            await bot.send_message(uid, "⏳ <b>Preparing quiz…</b>", parse_mode="HTML")
        except Exception:
            pass
    # Build question list (may call Groq in sync; run in thread to avoid blocking)
    question_list = await asyncio.to_thread(_build_question_list, room)
    if not question_list:
        for uid in list(room.players.keys()):
            try:
                await bot.send_message(uid, "No questions available for this level.")
            except Exception:
                pass
        if room.code in rooms:
            del rooms[room.code]
        return

    total_questions = len(question_list)
    room.total_questions = total_questions
    per_question_max = SCORE_MAX_TOTAL / total_questions
    room.per_question_max = per_question_max
    total_chapters = len(CHAPTERS)
    is_solo = room.expected_players == 1
    last_ch_idx = -1

    if not is_solo:
        room._paused = False
        room._pause_event = asyncio.Event()
        room._pause_event.set()
        room._cancel_event = asyncio.Event()

    if is_solo:
        room._solo_question_list = question_list

    for q_idx, (ch_idx, q, is_bonus) in enumerate(question_list):
        if getattr(room, "_cancelled", False):
            break
        room.chapter_index = ch_idx
        room.question_index = q_idx
        room.current_question = q
        shuffle_question_choices(q)
        room.current_choice_emojis = get_choice_emojis(4)
        room.answers.clear()
        room.answer_time_left.clear()
        room.question_message_ids.clear()
        if not is_solo:
            room._all_answered_event = asyncio.Event()
            room._is_chapter_end = (q_idx + 1 >= len(question_list)) or (question_list[q_idx + 1][0] != ch_idx)
        total_sec = QUESTION_DURATION_SEC
        end_ts = time.time() + total_sec
        room.question_end_ts = end_ts
        encouraging = get_encouraging_phrase()
        emojis = room.current_choice_emojis

        def text_for(t_left: int):
            return format_question_message(
                ch_idx, total_chapters, q_idx + 1, total_questions,
                t_left, q, is_bonus=is_bonus, encouraging=encouraging,
                choice_emojis=emojis,
            )

        for uid in list(room.players.keys()):
            try:
                msg = await bot.send_message(
                    uid,
                    text_for(total_sec),
                    reply_markup=question_choices_keyboard(q, room.code, locked=False, choice_emojis=emojis),
                )
                room.question_message_ids[uid] = msg.message_id
            except Exception as e:
                logger.warning("Send question to %s failed: %s", uid, e)

        # Solo: start live timer (countdown, "time running out" at 5s, auto-advance on timeout)
        if is_solo:
            room._solo_answered_event = asyncio.Event()
            room._solo_encouraging = encouraging
            room._pause_event = asyncio.Event()
            room._pause_event.set()
            room._cancel_event = asyncio.Event()
            room._solo_timer_task = asyncio.create_task(run_solo_question_timer(room))
            return

        async def edit_timer(seconds_left: int):
            # Notify at 10 seconds left (running time)
            if seconds_left == 10:
                for uid in list(room.players.keys()):
                    try:
                        await bot.send_message(
                            uid,
                            "⏰ <b>10 seconds left!</b>\n\nAnswer now to score points! 🏃",
                            parse_mode="HTML",
                        )
                    except Exception:
                        pass
            # "Time running out" at 5 seconds left
            if seconds_left == 5:
                for uid in list(room.players.keys()):
                    try:
                        await bot.send_message(uid, "⏰ <b>Time running out!</b> Answer quick! 🏃", parse_mode="HTML")
                    except Exception:
                        pass
            for uid, mid in list(room.question_message_ids.items()):
                try:
                    await bot.edit_message_text(
                        chat_id=uid,
                        message_id=mid,
                        text=text_for(seconds_left),
                        reply_markup=question_choices_keyboard(q, room.code, locked=(seconds_left <= 0), choice_emojis=emojis),
                    )
                except Exception:
                    pass

        async def on_timer_end():
            """Reveal, score, scoreboard, then return so the quiz always advances to the next question."""
            try:
                # Update question to 00s and lock when not everyone answered
                edit_to_zero = not is_solo and len(room.answers) < len(room.players)
                if edit_to_zero:
                    for uid, mid in list(room.question_message_ids.items()):
                        try:
                            await bot.edit_message_text(
                                chat_id=uid,
                                message_id=mid,
                                text=text_for(0),
                                reply_markup=question_choices_keyboard(q, room.code, locked=True, choice_emojis=emojis),
                            )
                        except Exception:
                            pass
                correct_idx = q.get("correct_index", 0)
                correct_answer_text = (q.get("choices", []) or [""])[correct_idx]
                explain = q.get("explain", "Keep going!")
                for uid in list(room.players.keys()):
                    player = room.players.get(uid)
                    if not player:
                        continue
                    chosen_idx = room.answers.get(uid)
                    correct = chosen_idx == correct_idx
                    time_left = room.answer_time_left.get(uid, 0.0)
                    pts = compute_score(correct, time_left, total_sec, per_question_max)
                    player.score = max(0, min(SCORE_MAX_TOTAL, round(player.score + pts, 1)))
                    if correct:
                        player.correct_count += 1
                    if correct:
                        msg = get_reveal_correct_phrase(pts) + explain
                    else:
                        time_up = chosen_idx is None
                        if time_up:
                            msg = "Time's up! " + get_reveal_time_up_phrase() + f"The answer was: {correct_answer_text}\n\n{explain}"
                        else:
                            msg = get_reveal_wrong_phrase() + f"The answer was: {correct_answer_text}\n\n{explain}"
                    try:
                        await bot.send_message(uid, msg)
                    except Exception:
                        pass

                await asyncio.sleep(5)

                # Single merged message per user: table (with user emphasized) + correct/wrong summary
                standings = room.get_sorted_standings()
                chapter_line = None
                if getattr(room, "_is_chapter_end", False):
                    chapter_line = f"━━━ 📊 Chapter {ch_idx + 1}/{total_chapters} complete ━━━"
                for uid in list(room.players.keys()):
                    player = room.players.get(uid)
                    if not player:
                        continue
                    wrong_count = max(0, player.answer_count - player.correct_count)
                    text = format_round_message_one(
                        standings,
                        q_idx + 1,
                        total_questions,
                        current_user_id=uid,
                        correct_count=player.correct_count,
                        wrong_count=wrong_count,
                        chapter_complete_line=chapter_line,
                    )
                    try:
                        await bot.send_message(uid, text, parse_mode="HTML")
                    except Exception:
                        pass
            except Exception as e:
                logger.exception("on_timer_end failed (quiz will still advance): %s", e)

        # Group: always run full 30s so everyone has time to answer; no early advance when all answered
        async def wait_while_paused():
            if getattr(room, "_pause_event", None) is not None:
                await room._pause_event.wait()

        try:
            await run_timer_edits(
                total_sec, edit_timer, on_timer_end,
                early_stop_event=room._cancel_event,
                all_answered_event=None,
                wait_while_paused=wait_while_paused,
            )
        except Exception as e:
            logger.exception("Timer/reveal failed for Q %s (advancing to next): %s", q_idx + 1, e)

        await asyncio.sleep(5)
        last_ch_idx = ch_idx

    if getattr(room, "_cancelled", False):
        if room.code in rooms:
            del rooms[room.code]
        return

    room.status = RoomStatus.FINISHED
    standings = room.get_sorted_standings()
    enc = get_encouraging_phrase()
    text = format_final_leaderboard(standings, encouraging=enc)
    rank_by_uid = {p.user_id: (i + 1) for i, p in enumerate(standings)}
    for uid in list(room.players.keys()):
        try:
            await bot.send_message(uid, text, parse_mode="HTML")
        except Exception:
            pass
        # Personal final summary for each user (same as "round feedback" for the last round)
        if not is_solo:
            player = room.players.get(uid)
            if player:
                rank = rank_by_uid.get(uid, 0)
                personal_text = format_personal_round_feedback(
                    rank, len(standings), player.score, player.correct_count, encouraging=enc,
                )
                try:
                    await bot.send_message(uid, personal_text)
                except Exception:
                    pass
    if room.code in rooms:
        del rooms[room.code]


async def run_solo_question_timer(room: RoomState):
    """Solo only: run live countdown, 'time running out' at 5s, and on timeout do reveal + advance_solo_quiz."""
    q = room.current_question
    if not q:
        return
    total_sec = QUESTION_DURATION_SEC
    total_chapters = len(CHAPTERS)
    ch_idx = room.chapter_index
    q_idx = room.question_index
    total_questions = room.total_questions
    emojis = room.current_choice_emojis
    encouraging = getattr(room, "_solo_encouraging", "") or get_encouraging_phrase()
    solo_list = getattr(room, "_solo_question_list", None)
    is_bonus = solo_list[room.question_index][2] if solo_list and room.question_index < len(solo_list) else False
    per_question_max = getattr(room, "per_question_max", None) or (SCORE_MAX_TOTAL / max(1, total_questions))

    def text_for(t_left: int):
        return format_question_message(
            ch_idx, total_chapters, q_idx + 1, total_questions,
            t_left, q, is_bonus=is_bonus, encouraging=encouraging,
            choice_emojis=emojis,
        )

    async def edit_timer(seconds_left: int):
        if seconds_left == 10:
            for uid in list(room.players.keys()):
                try:
                    await bot.send_message(uid, "⏰ <b>10 seconds left!</b> Pay attention — answer soon! 🏃", parse_mode="HTML")
                except Exception:
                    pass
        if seconds_left == 5:
            for uid in list(room.players.keys()):
                try:
                    await bot.send_message(uid, "⏰ <b>Time running out!</b> Answer quick! 🏃", parse_mode="HTML")
                except Exception:
                    pass
        for uid, mid in list(room.question_message_ids.items()):
            try:
                await bot.edit_message_text(
                    chat_id=uid,
                    message_id=mid,
                    text=text_for(seconds_left),
                    reply_markup=question_choices_keyboard(q, room.code, locked=(seconds_left <= 0), choice_emojis=emojis),
                )
            except Exception:
                pass

    async def on_timer_end():
        if getattr(room, "_solo_answered_event", None) and room._solo_answered_event.is_set():
            return
        for uid, mid in list(room.question_message_ids.items()):
            try:
                await bot.edit_message_text(
                    chat_id=uid,
                    message_id=mid,
                    text=text_for(0),
                    reply_markup=question_choices_keyboard(q, room.code, locked=True, choice_emojis=emojis),
                )
            except Exception:
                pass
        correct_idx = q.get("correct_index", 0)
        correct_answer_text = (q.get("choices", []) or [""])[correct_idx]
        explain = q.get("explain", "Keep going!")
        for uid in list(room.players.keys()):
            player = room.players.get(uid)
            if not player:
                continue
            chosen_idx = room.answers.get(uid)
            correct = chosen_idx == correct_idx
            time_left = room.answer_time_left.get(uid, 0.0)
            pts = compute_score(correct, time_left, total_sec, per_question_max)
            player.score = max(0, min(SCORE_MAX_TOTAL, round(player.score + pts, 1)))
            if correct:
                player.correct_count += 1
            msg = "Time's up! " + get_reveal_time_up_phrase() + f"The answer was: {correct_answer_text}\n\n{explain}"
            try:
                await bot.send_message(uid, msg)
            except Exception:
                pass
        await advance_solo_quiz(room)

    async def wait_while_paused():
        if getattr(room, "_pause_event", None) is not None:
            await room._pause_event.wait()

    try:
        await run_timer_edits(
            total_sec,
            edit_timer,
            on_timer_end,
            early_stop_event=getattr(room, "_solo_answered_event", None),
            all_answered_event=None,
            wait_while_paused=wait_while_paused,
            cancel_event=getattr(room, "_cancel_event", None),
        )
    except asyncio.CancelledError:
        raise

async def advance_solo_quiz(room: RoomState):
    """Solo only: after user answered, send next question or final leaderboard. Called from answer_callback."""
    if getattr(room, "_cancelled", False):
        return
    solo_list = getattr(room, "_solo_question_list", None)
    if not solo_list:
        return
    next_idx = room.question_index + 1
    if next_idx >= len(solo_list):
        room.status = RoomStatus.FINISHED
        enc = get_practice_finish_phrase()
        for uid in list(room.players.keys()):
            player = room.players.get(uid)
            if player:
                personal_text = format_personal_round_feedback(
                    1, 1, player.score, player.correct_count, encouraging=enc, include_place=False,
                )
                try:
                    await bot.send_message(uid, personal_text)
                except Exception:
                    pass
            try:
                await bot.send_message(
                    uid,
                    "Another round? 💪",
                    reply_markup=practice_another_round_inline(),
                )
            except Exception:
                pass
        return
    ch_idx, q, is_bonus = solo_list[next_idx]
    total_questions = len(solo_list)
    total_chapters = len(CHAPTERS)
    room.chapter_index = ch_idx
    room.question_index = next_idx
    room.current_question = q
    shuffle_question_choices(q)
    room.current_choice_emojis = get_choice_emojis(4)
    room.answers.clear()
    room.answer_time_left.clear()
    room.question_message_ids.clear()
    room.question_end_ts = time.time() + QUESTION_DURATION_SEC
    encouraging = get_encouraging_phrase()
    emojis = room.current_choice_emojis
    body = format_question_message(
        ch_idx, total_chapters, next_idx + 1, total_questions,
        QUESTION_DURATION_SEC, q, is_bonus=is_bonus, encouraging=encouraging,
        choice_emojis=emojis,
    )
    for uid in list(room.players.keys()):
        try:
            msg = await bot.send_message(
                uid,
                body,
                reply_markup=question_choices_keyboard(q, room.code, locked=False, choice_emojis=emojis),
            )
            room.question_message_ids[uid] = msg.message_id
        except Exception:
            pass
    room._solo_answered_event = asyncio.Event()
    room._solo_encouraging = encouraging
    room._solo_timer_task = asyncio.create_task(run_solo_question_timer(room))


# --- /start and /end ---
@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        HOME_MESSAGE,
        reply_markup=main_menu_inline(),
        parse_mode="HTML",
    )


@router.message(Command("home"))
async def cmd_home(message: Message, state: FSMContext):
    """Go directly to the home screen."""
    await state.clear()
    await message.answer(
        HOME_MESSAGE,
        reply_markup=main_menu_inline(),
        parse_mode="HTML",
    )


@router.message(Command("back"))
async def cmd_back(message: Message, state: FSMContext):
    """Go back one step (previous page); from first step goes to home."""
    current = await state.get_state()
    if current is None:
        await message.answer(
            HOME_MESSAGE,
            reply_markup=main_menu_inline(),
            parse_mode="HTML",
        )
        return

    # First step of any flow → home
    if current == CreateStates.waiting_level or current == JoinStates.waiting_code or current == PracticeStates.waiting_question_count:
        await state.clear()
        await message.answer(
            HOME_MESSAGE,
            reply_markup=main_menu_inline(),
            parse_mode="HTML",
        )
        return

    # Create flow: difficulty (level) → home (level is first step)
    if current == CreateStates.waiting_level:
        await state.clear()
        await message.answer(
            HOME_MESSAGE,
            reply_markup=main_menu_inline(),
            parse_mode="HTML",
        )
        return

    # Create flow: chapters (mode) → difficulty
    if current == CreateStates.waiting_mode:
        await state.set_state(CreateStates.waiting_level)
        await message.answer(
            "🎮 <b>Create quiz room</b>\n\nChoose difficulty:",
            reply_markup=level_inline(),
            parse_mode="HTML",
        )
        return

    # Practice: difficulty → question count
    if current == PracticeStates.waiting_level:
        await state.set_state(PracticeStates.waiting_question_count)
        await message.answer(
            "📚 <b>Practice</b>\n\nHow many questions do you want?",
            reply_markup=practice_count_inline(),
            parse_mode="HTML",
        )
        return

    # Create flow: host name → chapters (remove room if created)
    if current == CreateStates.waiting_host_name:
        data = await state.get_data()
        room_code = data.get("room_code")
        if room_code and room_code in rooms:
            del rooms[room_code]
        await state.update_data(room_code=None)
        await state.set_state(CreateStates.waiting_mode)
        await message.answer(
            "🎮 <b>Quiz mode</b>\n\nPick one subject (10 questions) or Marathon (all 3, 30 questions).",
            reply_markup=mode_inline(),
            parse_mode="HTML",
        )
        return

    # Join flow: name → PIN entry
    if current == JoinStates.waiting_name:
        await state.set_state(JoinStates.waiting_code)
        await state.update_data(join_code="", room_code=None)
        sent = await message.answer(
            join_code_box_text(""),
            reply_markup=join_pin_screen_keyboard(),
        )
        await state.update_data(
            join_screen_chat_id=sent.chat.id,
            join_screen_message_id=sent.message_id,
        )
        return

    # Unknown state → home
    await state.clear()
    await message.answer(
        HOME_MESSAGE,
        reply_markup=main_menu_inline(),
        parse_mode="HTML",
    )


async def _do_end_game(message: Message, state: FSMContext, by_command: str):
    """End game or practice: cancel timers, notify everyone, remove room."""
    await state.clear()
    room = find_room_for_user(message.from_user.id)
    if not room:
        await message.answer("You're not in a game. Use the menu to start or join one.")
        return
    room._cancelled = True
    ev = getattr(room, "_next_chapter_event", None)
    if ev and not ev.is_set():
        ev.set()
    cancel_ev = getattr(room, "_cancel_event", None)
    if cancel_ev is not None:
        cancel_ev.set()
    solo_task = getattr(room, "_solo_timer_task", None)
    if solo_task and not solo_task.done():
        solo_task.cancel()
        try:
            await solo_task
        except asyncio.CancelledError:
            pass
    msg = "🛑 Game ended. Use /start or /home for the menu."
    for uid in list(room.players.keys()):
        try:
            await bot.send_message(uid, msg)
        except Exception:
            pass
    if room.host_id != message.from_user.id and room.host_id not in room.players:
        try:
            await bot.send_message(room.host_id, msg)
        except Exception:
            pass
    if room.code in rooms:
        del rooms[room.code]


@router.message(Command("end"))
async def cmd_end(message: Message, state: FSMContext):
    await _do_end_game(message, state, "end")


@router.message(Command("stop"))
async def cmd_stop(message: Message, state: FSMContext):
    """End the quiz/practice and notify everyone. Feedback shows /stop."""
    await _do_end_game(message, state, "stop")


@router.message(Command("pause"))
async def cmd_pause(message: Message, state: FSMContext):
    room = find_room_for_user(message.from_user.id)
    if not room:
        await message.answer("You're not in a game.")
        return
    if message.from_user.id != room.host_id:
        await message.answer("Only the host can pause the quiz.")
        return
    if room.status != RoomStatus.RUNNING:
        await message.answer("Quiz is not running.")
        return
    room._paused = True
    ev = getattr(room, "_pause_event", None)
    if ev is not None:
        ev.clear()
    msg = "⏸ Quiz paused. Host: use /continue to resume."
    for uid in list(room.players.keys()):
        try:
            await bot.send_message(uid, msg)
        except Exception:
            pass
    if room.host_id not in room.players and room.host_id != message.from_user.id:
        try:
            await bot.send_message(room.host_id, msg)
        except Exception:
            pass
    await message.answer("Quiz paused. Use /continue to resume.")


@router.message(Command("continue"))
async def cmd_continue(message: Message, state: FSMContext):
    room = find_room_for_user(message.from_user.id)
    if not room:
        await message.answer("You're not in a game.")
        return
    if message.from_user.id != room.host_id:
        await message.answer("Only the host can resume the quiz.")
        return
    if room.status != RoomStatus.RUNNING:
        await message.answer("Quiz is not running.")
        return
    room._paused = False
    ev = getattr(room, "_pause_event", None)
    if ev is not None:
        ev.set()
    msg = "▶️ Quiz resumed!"
    for uid in list(room.players.keys()):
        try:
            await bot.send_message(uid, msg)
        except Exception:
            pass
    if room.host_id not in room.players and room.host_id != message.from_user.id:
        try:
            await bot.send_message(room.host_id, msg)
        except Exception:
            pass
    await message.answer("Quiz resumed.")


# --- Main menu (inline callbacks) ---
@router.callback_query(F.data == "menu_create")
async def menu_create(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(CreateStates.waiting_level)
    await callback.message.edit_text(
        "🎮 <b>Create quiz room</b>\n\nChoose difficulty:",
        reply_markup=level_inline(),
        parse_mode="HTML",
    )
    await callback.answer()




@router.callback_query(F.data == "menu_join")
async def menu_join(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(JoinStates.waiting_code)
    await state.update_data(
        join_code="",
        join_screen_chat_id=callback.message.chat.id,
        join_screen_message_id=callback.message.message_id,
    )
    await callback.message.edit_text(
        join_code_box_text(""),
        reply_markup=join_pin_screen_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "join_cancel")
async def join_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        HOME_MESSAGE,
        reply_markup=main_menu_inline(),
        parse_mode="HTML",
    )
    await callback.answer()


# --- Back navigation (step-by-step to home) ---
@router.callback_query(F.data == "back_home")
async def back_home(callback: CallbackQuery, state: FSMContext):
    """← Back from first step → home (Join a Game, Menu)."""
    await state.clear()
    await callback.message.edit_text(
        HOME_MESSAGE,
        reply_markup=main_menu_inline(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "back_to_level")
async def back_to_level(callback: CallbackQuery, state: FSMContext):
    """← Back from chapters → difficulty (Create flow)."""
    await state.set_state(CreateStates.waiting_level)
    await callback.message.edit_text(
        "🎮 <b>Create quiz room</b>\n\nChoose difficulty:",
        reply_markup=level_inline(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "back_to_mode")
async def back_to_mode(callback: CallbackQuery, state: FSMContext):
    """← Back from create name → chapters; remove room if created."""
    data = await state.get_data()
    room_code = data.get("room_code")
    if room_code and room_code in rooms:
        del rooms[room_code]
    await state.update_data(room_code=None)
    await state.set_state(CreateStates.waiting_mode)
    await callback.message.edit_text(
        "🎮 <b>Quiz mode</b>\n\nPick one subject (10 questions) or Marathon (all 3, 30 questions).",
        reply_markup=mode_inline(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "back_to_pin")
async def back_to_pin(callback: CallbackQuery, state: FSMContext):
    """← Back from join name → PIN entry screen."""
    await state.set_state(JoinStates.waiting_code)
    await state.update_data(
        join_code="",
        room_code=None,
        join_screen_chat_id=callback.message.chat.id,
        join_screen_message_id=callback.message.message_id,
    )
    await callback.message.edit_text(
        join_code_box_text(""),
        reply_markup=join_pin_screen_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("join_key:"))
async def join_code_key(callback: CallbackQuery, state: FSMContext):
    if await state.get_state() != JoinStates.waiting_code:
        await callback.answer()
        return
    key = callback.data.replace("join_key:", "")
    data = await state.get_data()
    code = (data.get("join_code") or "")[:6]
    if key == "DEL":
        code = code[:-1]
    elif key == "GO":
        await callback.answer("Checking…")
        await _try_join_with_code(callback, state, code)
        return
    else:
        if len(key) == 1 and key.isalnum() and len(code) < 6:
            code = (code + key).upper()
    await state.update_data(join_code=code)
    try:
        await callback.message.edit_text(join_code_box_text(code))
    except Exception:
        pass
    await callback.answer()


async def _try_join_with_code(callback: CallbackQuery, state: FSMContext, code: str):
    """Validate room code (keypad flow) and move to name step or show error."""
    code = (code or "").strip().upper()[:6]
    err = None
    if len(code) != 6:
        err = "Code must be 6 characters."
    else:
        room = get_room_by_code(code)
        if not room:
            err = "Invalid code. Try again."
        elif room.status != RoomStatus.LOBBY:
            err = "Room isn't accepting joins right now."
        elif room.is_full():
            err = "Room is full."

    if err:
        await callback.message.edit_text(
            join_code_box_text(code, err),
            reply_markup=join_pin_screen_keyboard(),
        )
        await state.update_data(join_code=code)
        return
    room = get_room_by_code(code)
    await state.update_data(room_code=room.code, join_code=code)
    await state.set_state(JoinStates.waiting_name)
    default = get_display_name(callback.from_user)
    await callback.message.edit_text(
        f"✅ Code accepted!\n\nWhat name should appear on the scoreboard?\n(Send /skip to use: {default})",
        reply_markup=join_name_keyboard(),
    )


@router.callback_query(F.data == "menu_practice")
async def menu_practice(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(PracticeStates.waiting_question_count)
    await callback.message.edit_text(
        "📚 <b>Practice</b>\n\nHow many questions do you want?",
        reply_markup=practice_count_inline(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("practice_count_"))
async def practice_count_selected(callback: CallbackQuery, state: FSMContext):
    raw = callback.data.replace("practice_count_", "")
    try:
        count = int(raw)
    except ValueError:
        await callback.answer("Invalid option.")
        return
    if count not in (5, 10, 15, 20):
        await callback.answer("Choose 5, 10, 15, or 20.")
        return
    await state.update_data(practice_question_count=count)
    await state.set_state(PracticeStates.waiting_level)
    await callback.message.edit_text(
        "📚 <b>Practice</b>\n\nChoose difficulty:",
        reply_markup=practice_level_inline(from_question_count=True),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "back_to_practice_count")
async def back_to_practice_count(callback: CallbackQuery, state: FSMContext):
    await state.set_state(PracticeStates.waiting_question_count)
    await callback.message.edit_text(
        "📚 <b>Practice</b>\n\nHow many questions do you want?",
        reply_markup=practice_count_inline(),
        parse_mode="HTML",
    )
    await callback.answer()


# --- Practice: level → subject → start ---
@router.callback_query(F.data.startswith("practice_level_"))
async def practice_level_selected(callback: CallbackQuery, state: FSMContext):
    level = callback.data.replace("practice_level_", "")
    await state.update_data(practice_level=level)
    await state.set_state(PracticeStates.waiting_subject)
    await callback.message.edit_text(
        "📚 <b>Practice</b>\n\nChoose subject (same as Room options):",
        reply_markup=practice_subject_inline(from_level=True),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "back_to_practice_level")
async def back_to_practice_level(callback: CallbackQuery, state: FSMContext):
    await state.set_state(PracticeStates.waiting_level)
    await callback.message.edit_text(
        "📚 <b>Practice</b>\n\nChoose difficulty:",
        reply_markup=practice_level_inline(from_question_count=True),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("practice_subject_"))
async def practice_subject_selected(callback: CallbackQuery, state: FSMContext):
    raw = callback.data.replace("practice_subject_", "")
    subject = raw if raw != "mixed" else "mixed"
    data = await state.get_data()
    level = data.get("practice_level", "medium")
    num_questions = data.get("practice_question_count", 10)
    if num_questions not in (5, 10, 15, 20):
        num_questions = 10
    await state.clear()
    await callback.answer("Starting your practice… 🚀")
    name = get_display_name(callback.from_user)
    code = f"PRACTICE_{callback.from_user.id}"
    room = RoomState(
        code=code,
        host_id=callback.from_user.id,
        expected_players=1,
        status=RoomStatus.RUNNING,
        quiz_mode=subject if subject != "mixed" else "basic",
        level=level,
        num_questions=num_questions,
    )
    room.players[callback.from_user.id] = Player(user_id=callback.from_user.id, display_name=name[:30])
    rooms[code] = room
    asyncio.create_task(start_quiz_for_room(room))
    subj_name = {"leetcode": "Leetcode", "algorithms": "Algorithms", "code_review": "Code Review", "marathon": "Marathon", "mixed": "Mixed"}.get(subject, subject)
    await callback.message.edit_text(
        f"📚 <b>Practice started!</b> ({display_level_name(level)}) • {subj_name} • {num_questions} questions\n\nYou've got this! 💪",
        parse_mode="HTML",
    )


# --- Practice finished: Another round? / Same difficulty? ---
@router.callback_query(F.data == "practice_again_no")
async def practice_again_no(callback: CallbackQuery, state: FSMContext):
    uid = callback.from_user.id
    room = rooms.get(f"PRACTICE_{uid}")
    if room and room.code in rooms:
        del rooms[room.code]
    await state.clear()
    await callback.message.edit_text("👋 Back to home.")
    await callback.message.answer(
        HOME_MESSAGE,
        reply_markup=main_menu_inline(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "practice_again_yes")
async def practice_again_yes(callback: CallbackQuery, state: FSMContext):
    uid = callback.from_user.id
    room = rooms.get(f"PRACTICE_{uid}")
    if not room or room.code not in rooms:
        await callback.answer("Session ended.")
        return
    level = room.level
    num_questions = getattr(room, "num_questions", 10) or 10
    subject = getattr(room, "quiz_mode", "basic")
    if subject not in ("leetcode", "algorithms", "code_review", "marathon"):
        subject = "mixed"
    await callback.message.edit_text(
        "Same difficulty? 🤓",
        reply_markup=practice_same_difficulty_inline(level, num_questions, subject),
    )
    await callback.answer()


@router.callback_query(F.data == "practice_same_no")
async def practice_same_no(callback: CallbackQuery, state: FSMContext):
    uid = callback.from_user.id
    room = rooms.get(f"PRACTICE_{uid}")
    if room and room.code in rooms:
        num_questions = getattr(room, "num_questions", 10) or 10
        del rooms[room.code]
        await state.set_state(PracticeStates.waiting_level)
        await state.update_data(practice_question_count=num_questions)
    await callback.message.edit_text(
        "📚 <b>Choose difficulty</b>",
        reply_markup=practice_level_inline(from_question_count=True),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("practice_same_yes:"))
async def practice_same_yes(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer()
        return
    level = parts[1]
    try:
        num_questions = int(parts[2])
    except ValueError:
        num_questions = 10
    subject = parts[3] if len(parts) >= 4 else "mixed"
    if num_questions not in (5, 10, 15, 20):
        num_questions = 10
    uid = callback.from_user.id
    old_room = rooms.get(f"PRACTICE_{uid}")
    if old_room and old_room.code in rooms:
        del rooms[old_room.code]
    await state.clear()
    name = get_display_name(callback.from_user)
    code = f"PRACTICE_{uid}"
    quiz_mode = subject if subject in ("leetcode", "algorithms", "code_review", "marathon") else "basic"
    room = RoomState(
        code=code,
        host_id=uid,
        expected_players=1,
        status=RoomStatus.RUNNING,
        quiz_mode=quiz_mode,
        level=level,
        num_questions=num_questions,
    )
    room.players[uid] = Player(user_id=uid, display_name=name[:30])
    rooms[code] = room
    asyncio.create_task(start_quiz_for_room(room))
    subj_name = {"leetcode": "Leetcode", "algorithms": "Algorithms", "code_review": "Code Review", "marathon": "Marathon", "mixed": "Mixed"}.get(subject, "Mixed")
    await callback.message.edit_text(
        f"📚 <b>Practice started!</b> ({display_level_name(level)}) • {subj_name} • {num_questions} questions\n\nSame difficulty – let's go! 💪",
        parse_mode="HTML",
    )
    await callback.answer("Starting another round… 🚀")


# --- Level selected (Create flow: difficulty → then chapters) ---
@router.callback_query(F.data.startswith("level_"))
async def level_selected(callback: CallbackQuery, state: FSMContext):
    level = callback.data.replace("level_", "")
    await state.update_data(level=level)
    await state.set_state(CreateStates.waiting_mode)
    await callback.message.edit_text(
        "🎮 <b>Quiz mode</b>\n\nPick one subject (10 questions) or Marathon (all 3, 30 questions).",
        reply_markup=mode_inline(),
        parse_mode="HTML",
    )
    await callback.answer()


# --- Mode selected (Create) → create room (max 10 players) and ask for name ---
@router.callback_query(F.data.startswith("mode_"))
async def mode_selected(callback: CallbackQuery, state: FSMContext):
    mode = callback.data.replace("mode_", "")
    await state.update_data(quiz_mode=mode)
    data = await state.get_data()
    level = data.get("level", "medium")
    room_code = generate_room_code()
    room = RoomState(
        code=room_code,
        host_id=callback.from_user.id,
        expected_players=10,
        quiz_mode=mode,
        level=level,
    )
    rooms[room_code] = room
    await state.update_data(room_code=room_code)
    await state.set_state(CreateStates.waiting_host_name)
    name = get_display_name(callback.from_user)
    await callback.message.edit_text(
        create_name_box_text(name),
        reply_markup=create_name_skip_inline(),
    )
    await callback.answer()


# --- Create: host name (text only; user must type name) ---
@router.message(Command("skip"))
async def skip_name(message: Message, state: FSMContext):
    if await state.get_state() != CreateStates.waiting_host_name:
        return
    name = get_display_name(message.from_user)
    await _do_create_host(message, state, name)


@router.message(F.text == "Create quiz room")
async def create_quiz_room_legacy(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(CreateStates.waiting_level)
    await message.answer(
        "🎮 <b>Create quiz room</b>\n\nChoose difficulty:",
        reply_markup=level_inline(),
        parse_mode="HTML",
    )


@router.message(F.text == "Join quiz room")
async def join_quiz_room_legacy(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(JoinStates.waiting_code)
    await message.answer("Enter room code (send as text):")


# --- Create flow: host name (text or /skip) ---
@router.message(CreateStates.waiting_host_name, F.text)
async def create_host_name_text(message: Message, state: FSMContext):
    name = (message.text or "").strip() or get_display_name(message.from_user)
    await _do_create_host(message, state, name)


async def _do_create_host(message: Message, state: FSMContext, name: str, from_user=None):
    """Create/join lobby (multiplayer room, max 10). from_user: use when triggered by callback."""
    data = await state.get_data()
    room_code = data.get("room_code")
    await state.clear()
    user = from_user or message.from_user
    name = (name or get_display_name(user))[:30]

    room = rooms.get(room_code)
    if not room:
        await message.answer("Room expired. Please create again from /start.")
        return
    room.players[user.id] = Player(user_id=user.id, display_name=name)
    text = render_lobby_text(room)
    kb = build_lobby_keyboard(room, user.id)
    sent = await message.answer(text, reply_markup=kb, parse_mode="HTML")
    room.lobby_chat_id = message.chat.id
    room.lobby_message_id = sent.message_id

    async def idle_expire():
        await asyncio.sleep(ROOM_IDLE_TIMEOUT_MIN * 60)
        if room.code in rooms and room.status == RoomStatus.LOBBY:
            await cancel_room_and_notify(room, "expired")

    room.idle_task = asyncio.create_task(idle_expire())


async def create_host_name(message: Message, state: FSMContext, use_telegram_name: bool = False):
    name = get_display_name(message.from_user) if use_telegram_name else ((message.text or "").strip() or get_display_name(message.from_user))
    await _do_create_host(message, state, name)


# --- Join flow: code (typed or pasted in chat) ---
@router.message(JoinStates.waiting_code, F.text)
async def join_code(message: Message, state: FSMContext):
    raw = (message.text or "").strip()
    code = extract_room_code(raw) or (raw.upper()[:6] if raw else "")
    if len(code) < 6:
        code = ""
    error = None
    if len(code) != 6:
        error = "Code must be 6 characters. Paste the code from the host (e.g. 2TPWHJ)."
    else:
        room = get_room_by_code(code)
        if not room:
            error = "Invalid code. Try again."
        elif room.status != RoomStatus.LOBBY:
            error = "Room isn't accepting joins right now."
        elif room.is_full():
            error = "Room is full."

    data = await state.get_data()
    chat_id = data.get("join_screen_chat_id")
    msg_id = data.get("join_screen_message_id")

    if error:
        # Re-show PIN screen with error (edit same message to avoid spam)
        pin_text = join_code_box_text("", error)
        try:
            if chat_id is not None and msg_id is not None:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=msg_id,
                    text=pin_text,
                    reply_markup=join_pin_screen_keyboard(),
                )
            else:
                await message.answer(pin_text, reply_markup=join_pin_screen_keyboard())
        except Exception:
            await message.answer(pin_text, reply_markup=join_pin_screen_keyboard())
        return

    room = get_room_by_code(code)
    await state.update_data(room_code=room.code)
    await state.set_state(JoinStates.waiting_name)
    default = get_display_name(message.from_user)
    name_prompt = (
        f"✅ Code accepted!\n\n"
        f"What name should appear on the scoreboard?\n"
        f"(Send /skip to use: {default})"
    )
    try:
        if chat_id is not None and msg_id is not None:
            await bot.edit_message_text(
                chat_id=chat_id, message_id=msg_id, text=name_prompt,
                reply_markup=join_name_keyboard(),
            )
        else:
            await message.answer(name_prompt, reply_markup=join_name_keyboard())
    except Exception:
        await message.answer(name_prompt, reply_markup=join_name_keyboard())


# --- Join flow: name (text, /skip, or inline "Use my Telegram name") ---
@router.callback_query(F.data == "join_skip_name")
async def join_skip_name(callback: CallbackQuery, state: FSMContext):
    """Use my Telegram name (inline button) — complete join with default name."""
    if await state.get_state() != JoinStates.waiting_name:
        await callback.answer()
        return
    data = await state.get_data()
    room_code = data.get("room_code")
    await state.clear()
    room = get_room_by_code(room_code or "")
    if not room:
        await callback.message.edit_text("❌ Room no longer exists.", reply_markup=main_menu_inline())
        await callback.answer()
        return
    if room.status != RoomStatus.LOBBY:
        await callback.message.edit_text("❌ Room is not accepting joins.", reply_markup=main_menu_inline())
        await callback.answer()
        return
    if room.is_full():
        await callback.message.edit_text("❌ Room is full.", reply_markup=main_menu_inline())
        await callback.answer()
        return
    uid = callback.from_user.id
    name = get_display_name(callback.from_user)[:30]
    if uid in room.players:
        room.players[uid].display_name = name
        await callback.message.edit_text("Joined ✅ Name updated. Waiting for host to start.", reply_markup=main_menu_inline())
    else:
        room.players[uid] = Player(user_id=uid, display_name=name)
        await callback.message.edit_text("Joined ✅ Waiting for host to start.", reply_markup=main_menu_inline())
    await callback.answer("Using your Telegram name.")
    await update_lobby_message(room)


@router.message(JoinStates.waiting_name, F.text)
async def join_name(message: Message, state: FSMContext):
    name = (message.text or "").strip() if message.text != "/skip" else ""
    name = name or get_display_name(message.from_user)
    name = name[:30]
    data = await state.get_data()
    room_code = data.get("room_code")
    await state.clear()

    room = get_room_by_code(room_code or "")
    if not room:
        await message.answer("❌ Room no longer exists.", reply_markup=main_menu_inline())
        return
    if room.status != RoomStatus.LOBBY:
        await message.answer("❌ Room is not accepting joins.", reply_markup=main_menu_inline())
        return
    if room.is_full():
        await message.answer("❌ Room is full.", reply_markup=main_menu_inline())
        return
    uid = message.from_user.id
    if uid in room.players:
        room.players[uid].display_name = name
        await message.answer("Joined ✅ Name updated. Waiting for host to start.", reply_markup=main_menu_inline())
    else:
        room.players[uid] = Player(user_id=uid, display_name=name)
        await message.answer("Joined ✅ Waiting for host to start.", reply_markup=main_menu_inline())
    await update_lobby_message(room)


# --- Lobby callbacks ---
@router.callback_query(F.data.startswith("lobby_copy:"))
async def lobby_copy(callback: CallbackQuery):
    code = callback.data.split(":", 1)[1]
    room = rooms.get(code)
    if not room or callback.from_user.id != room.host_id:
        await callback.answer("Not allowed.")
        return
    share_text = render_invite_message(code)
    await callback.message.answer(share_text, parse_mode="Markdown")
    await callback.answer("Forward this message to share the code!")


@router.callback_query(F.data.startswith("lobby_start:"))
async def lobby_start(callback: CallbackQuery):
    code = callback.data.split(":", 1)[1]
    room = rooms.get(code)
    if not room or callback.from_user.id != room.host_id:
        await callback.answer("Not allowed.")
        return
    if room.idle_task and not room.idle_task.done():
        room.idle_task.cancel()
        try:
            await room.idle_task
        except asyncio.CancelledError:
            pass
    await callback.answer("Starting quiz…")
    asyncio.create_task(start_quiz_for_room(room))


@router.callback_query(F.data.startswith("lobby_cancel:"))
async def lobby_cancel(callback: CallbackQuery):
    code = callback.data.split(":", 1)[1]
    room = rooms.get(code)
    if not room or callback.from_user.id != room.host_id:
        await callback.answer("Not allowed.")
        return
    await cancel_room_and_notify(room, "cancel")
    await callback.message.edit_text(
        HOME_MESSAGE,
        reply_markup=main_menu_inline(),
        parse_mode="HTML",
    )
    await callback.answer("Room cancelled.")


@router.callback_query(F.data.startswith("lobby_back:"))
async def lobby_back(callback: CallbackQuery):
    """← Back from lobby → main menu (cancel room)."""
    code = callback.data.split(":", 1)[1]
    room = rooms.get(code)
    if not room or callback.from_user.id != room.host_id:
        await callback.answer("Not allowed.")
        return
    await cancel_room_and_notify(room, "cancel")
    await callback.message.edit_text(
        HOME_MESSAGE,
        reply_markup=main_menu_inline(),
        parse_mode="HTML",
    )
    await callback.answer()


# --- Answer callback (during quiz) ---
@router.callback_query(F.data.startswith("ans:"))
async def answer_callback(callback: CallbackQuery):
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer()
        return
    code = parts[1]
    try:
        choice_index = int(parts[2])
    except ValueError:
        await callback.answer()
        return
    room = rooms.get(code)
    if not room or room.status != RoomStatus.RUNNING:
        await callback.answer("Too late or invalid room.")
        return
    uid = callback.from_user.id
    if uid not in room.players:
        await callback.answer("You're not in this game.")
        return
    if uid in room.answers:
        await callback.answer("You already answered.")
        return
    if time.time() >= room.question_end_ts:
        await callback.answer("Time's up!")
        return
    room.answers[uid] = choice_index
    time_left_sec = max(0.0, room.question_end_ts - time.time())
    room.answer_time_left[uid] = time_left_sec
    time_took_ms = (QUESTION_DURATION_SEC - time_left_sec) * 1000
    room.players[uid].total_response_time_ms += max(0, time_took_ms)
    room.players[uid].answer_count += 1
    is_solo = room.expected_players == 1

    if is_solo:
        # Solo: stop timer task (user answered), then score, show feedback, send reveal, advance to next question
        room._solo_answered_event.set()
        solo_timer = getattr(room, "_solo_timer_task", None)
        if solo_timer and not solo_timer.done():
            solo_timer.cancel()
            try:
                await solo_timer
            except asyncio.CancelledError:
                pass
        q = room.current_question
        correct_idx = q.get("correct_index", 0) if q else 0
        correct = choice_index == correct_idx
        total_sec = QUESTION_DURATION_SEC
        per_max = getattr(room, "per_question_max", None) or (SCORE_MAX_TOTAL / max(1, room.total_questions))
        pts = compute_score(correct, time_left_sec, total_sec, per_max)
        player = room.players.get(uid)
        if player:
            player.score = max(0, min(SCORE_MAX_TOTAL, round(player.score + pts, 1)))
            if correct:
                player.correct_count += 1
        if correct:
            feedback = f"Correct! ✅ +{pts:.0f} pts"
        else:
            feedback = "Wrong! ✗"
        motivating = get_after_answer_motivating(correct)
        await callback.answer(feedback)
        mid = room.question_message_ids.get(uid)
        if mid and q:
            try:
                total_ch = len(CHAPTERS)
                t_display = int(round(time_left_sec))
                body = format_question_message(
                    room.chapter_index, total_ch, room.question_index + 1, room.total_questions,
                    t_display, q, is_bonus=False, encouraging="", choice_emojis=room.current_choice_emojis,
                )
                await bot.edit_message_text(
                    chat_id=uid,
                    message_id=mid,
                    text=body + f"\n\n  <b>{feedback}</b>\n  {motivating}",
                    reply_markup=question_choices_keyboard(q, room.code, locked=False, choice_emojis=room.current_choice_emojis),
                    parse_mode="HTML",
                )
            except Exception:
                pass
        correct_answer_text = (q.get("choices", []) or [""])[correct_idx]
        explain = q.get("explain", "Keep going!")
        if correct:
            reveal_msg = get_reveal_correct_phrase(pts) + explain
        else:
            reveal_msg = get_reveal_wrong_phrase() + f"The answer was: {correct_answer_text}\n\n{explain}"
        try:
            await bot.send_message(uid, reveal_msg)
        except Exception:
            pass
        await asyncio.sleep(1)
        await advance_solo_quiz(room)
    else:
        await callback.answer("✓ Got it!")
        try:
            await bot.send_message(uid, get_answer_recorded_phrase())
        except Exception:
            pass
        # Group: when everyone has answered, stop timer and advance to next question
        if len(room.answers) >= len(room.players):
            ev = getattr(room, "_all_answered_event", None)
            if ev:
                ev.set()


# --- Locked / noop (user tapped after time ran out) ---
@router.callback_query(F.data == "noop")
async def noop_callback(callback: CallbackQuery):
    await callback.answer("Time's up! Answer locked.")

# --- Next chapter (multiplayer) ---
@router.callback_query(F.data.startswith("next_ch:"))
async def next_chapter(callback: CallbackQuery):
    code = callback.data.split(":", 1)[1]
    room = rooms.get(code)
    if not room or callback.from_user.id != room.host_id:
        await callback.answer("Not allowed.")
        return
    ev = getattr(room, "_next_chapter_event", None)
    if ev and not ev.is_set():
        ev.set()
    await callback.answer("Next chapter!")


# --- Unrecognized command / message (catch-all; register last) ---
@router.message(F.text)
async def unknown_message(message: Message, state: FSMContext):
    """Send feedback when user types an unrecognized command or word."""
    await message.answer(
        "❌ <b>Unrecognized command or message.</b>\n\n"
        "Use /start or /home for the menu, or tap the buttons above.",
        parse_mode="HTML",
    )


# --- Run ---
async def main():
    dp.include_router(router)
    # Show which bot this project is running (token is from my_secrets.py in this folder)
    try:
        me = await bot.get_me()
        logger.info(
            "DevDuel app running as @%s (%s). Token from my_secrets.py in THIS folder. Wrong bot? Edit my_secrets.py and set TOKEN/BOT_TOKEN.",
            me.username, me.full_name,
        )
    except Exception as e:
        logger.warning("Could not get bot info: %s. Check TOKEN/BOT_TOKEN in my_secrets.py.", e)
    try:
        await dp.start_polling(bot)
    except Exception as e:
        err_msg = str(e).lower()
        if "conflict" in err_msg or "getupdates" in err_msg or "terminated by other" in err_msg:
            logger.error(
                "Telegram conflict: another bot instance is using this token. "
                "Stop all other copies (other terminals, old processes) and run only one instance."
            )
            raise SystemExit(1)
        raise


if __name__ == "__main__":
    import sys
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nBot stopped.")
    except SystemExit as e:
        sys.exit(e.code if e.code is not None else 1)
