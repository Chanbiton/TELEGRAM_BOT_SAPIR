# Telegram Quiz Bot — Kahoot-like multiplayer quiz

In-memory, mobile-first quiz bot with aiogram v3. Create or join rooms, run 3 chapters of questions with timers and scoreboards.

## Setup

1. **Create a bot** via [@BotFather](https://t.me/BotFather) and copy the token.

2. **Configure secrets** — edit `my_secrets.py` in the project root and set your token:
   ```python
   TOKEN = "your_bot_token_here"   # or BOT_TOKEN = "..."
   ```
   (Optional: `GROQ_API_KEY`, `GROQ_MODEL` for AI-generated questions.)

3. **Install dependencies** (with venv activated):
   ```powershell
   python -m pip install -r requirements.txt
   ```

4. **Run the bot** from project root:
   ```powershell
   python -m app.main
   ```

## Features

- **Main menu:** Create quiz room | Join quiz room
- **Create:** Choose 1–10 participants. Solo (1) starts immediately; 2–10 get a 6-char room code and lobby with "Copy code", "Start quiz", "Cancel room".
- **Join:** Enter code (case-insensitive), set display name. Lobby updates as players join.
- **Quiz:** 3 chapters, configurable questions per chapter. Each question has a countdown; answer via A/B/C/D. Scoring: correctness + speed bonus. Chapter results and final leaderboard (🏆🥇🥈).
- **Copy code:** Sends a message with the room code and short instructions so the host can forward it.
- **Idle timeout:** Rooms in lobby expire after 10 minutes (configurable via `ROOM_IDLE_TIMEOUT_MIN`).

## Config (my_secrets.py or environment)

- `TOKEN` or `BOT_TOKEN` in `my_secrets.py` — required
- `GROQ_API_KEY`, `GROQ_MODEL` in `my_secrets.py` — optional (Groq)
- `QUESTIONS_PER_CHAPTER` — default 10 (env or config)
- `QUESTION_DURATION_SEC` — default 30 (env or config)
- `ROOM_IDLE_TIMEOUT_MIN` — default 10 (env or config)

## Question banks

Edit JSON files in `questions/`:

- `chapter1.json`, `chapter2.json`, `chapter3.json`

Format per question:

```json
{
  "id": "c1_q1",
  "text": "Question text?",
  "choices": ["A ...", "B ...", "C ...", "D ..."],
  "correct_index": 0,
  "explain": "Short explanation.",
  "difficulty": "easy"
}
```

## Project layout

```
/app
  main.py      — handlers, FSM, quiz flow
  config.py    — settings (loads my_secrets.py)
  state.py     — in-memory rooms & players
  quiz_engine.py — scoring, timer, formatting
  keyboards.py — reply & inline keyboards
  utils.py     — room code, question loading
/questions
  chapter1.json, chapter2.json, chapter3.json
my_secrets.py  — add your TOKEN (and optional GROQ_API_KEY); do not commit
requirements.txt
README.md
```

No database; state is in memory only. Restarting the bot clears all rooms.
