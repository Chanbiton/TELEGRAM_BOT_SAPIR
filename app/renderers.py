"""Lobby and invite message rendering — HTML card with <pre> code box."""
import html
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.state import RoomState

INNER_WIDTH = 30


def _code_box_pre(code: str) -> str:
    """Monospaced code box using <pre> and box-drawing chars (fixed width)."""
    code = (code or "").strip()
    padding = " " * (INNER_WIDTH - 3 - len(code)) if len(code) < INNER_WIDTH - 3 else ""
    line = f"│   {code}{padding}│"
    top = "┌" + "─" * INNER_WIDTH + "┐"
    bot = "└" + "─" * INNER_WIDTH + "┘"
    return f"<pre>{top}\n{line}\n{bot}</pre>"


def render_lobby_text(room: "RoomState") -> str:
    """
    'Game created!' lobby card (HTML). Use parse_mode='HTML'.
    Shows host, player count, code, and names. Updated live when players join.
    """
    code = room.code
    box = _code_box_pre(code)
    count = room.joined_count
    max_players = getattr(room, "expected_players", 10)
    host_player = room.players.get(room.host_id)
    host_name = html.escape(host_player.display_name) if host_player else "Host"
    parts = [
        "(you)" if pid == room.host_id else html.escape(p.display_name)
        for pid, p in room.players.items()
    ]
    names = ", ".join(parts) if parts else "(you)"
    return (
        "🎮 <b>Game created!</b>\n\n"
        f"👤 <b>Host:</b> {host_name}\n\n"
        f"👥 <b>Players joined: {count} / {max_players}</b>\n"
        f"( {names} )\n\n"
        "Share this code with friends:\n"
        f"{box}\n\n"
        "Tap <b>Start Game!</b> when you're ready to begin."
    )


def render_invite_message(code: str) -> str:
    """Forwardable invite message for Copy code. Backticks make code tappable to copy in Telegram."""
    return (
        "Join my game! 🎮\n"
        f"Code: `{code}`\n\n"
        "Steps:\n"
        "1) Open the bot\n"
        "2) Tap 🔗 Join Room\n"
        f"3) Enter code: `{code}`"
    )
