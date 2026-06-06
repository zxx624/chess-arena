from __future__ import annotations

import asyncio
import html
import json
import os
import secrets
import sqlite3
import hmac
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from .engine import BLACK, INITIAL_FEN, RED, RuleError, apply_ucci, is_in_check, legal_moves, parse_fen
from .games import go9

DB_PATH = Path(os.environ.get("CHESS_ARENA_DB", "/mnt/cosmem/gulu1-1415708756/chess-arena/chess_arena.db"))
BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
ADMIN_TOKEN = os.environ.get("CHESS_ARENA_ADMIN_TOKEN", "").strip()

app = FastAPI(title="chess-arena-server", version="0.2.0")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

DEFAULT_GAME = "xiangqi"
SUPPORTED_GAMES = {"xiangqi", "go"}


def normalize_game(game: str | None) -> str:
    value = (game or DEFAULT_GAME).strip().lower()
    if value not in SUPPORTED_GAMES:
        raise HTTPException(status_code=400, detail=f"unsupported game: {value}")
    return value


class Side(str, Enum):
    red = RED
    black = BLACK
    random = "random"


@dataclass
class Bot:
    id: str
    name: str
    token: str
    created_at: float = field(default_factory=time.time)
    avatar_url: str | None = None
    description: str | None = None
    chess_style: str = "random"
    persona_prompt: str | None = None
    engine_mode: str = "random"
    client_type: str = "astrbot"
    instance_name: str | None = None
    is_public: bool = True
    is_enabled: bool = True
    online_status: str = "offline"
    last_seen_at: float | None = None
    updated_at: float = field(default_factory=time.time)
    challenge_policy: str = "auto_accept"
    owner_review_timeout_sec: int = 180
    game: str = DEFAULT_GAME


@dataclass
class Challenge:
    id: str
    challenger_bot_id: str
    opponent_bot_id: str
    challenger_side: str
    status: str = "pending"
    match_id: str | None = None
    created_at: float = field(default_factory=time.time)
    owner_decision: str | None = None
    owner_decision_reason: str | None = None
    expires_at: float | None = None
    updated_at: float = field(default_factory=time.time)
    game: str = DEFAULT_GAME


@dataclass
class Match:
    id: str
    red_bot_id: str
    black_bot_id: str
    fen: str = INITIAL_FEN
    status: str = "active"
    result: str | None = None
    winner_bot_id: str | None = None
    finish_reason: str | None = None
    ply: int = 0
    moves: list[dict[str, Any]] = field(default_factory=list)
    challenge_id: str | None = None
    paused: bool = False
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    red_time_left_ms: int = 1_800_000
    black_time_left_ms: int = 1_800_000
    total_time_ms: int = 1_800_000
    last_move_at: float | None = None
    started_at: float | None = None
    finished_at: float | None = None
    game: str = DEFAULT_GAME


bots: dict[str, Bot] = {}
tokens: dict[str, str] = {}
challenges: dict[str, Challenge] = {}
matches: dict[str, Match] = {}
subscribers: dict[str, set[asyncio.Queue]] = {}
match_sse_queues: dict[str, set[asyncio.Queue]] = {}
match_queue_entries: dict[str, dict[str, Any]] = {}  # bot_id -> {bot_id, rating, joined_at}
state_lock = asyncio.Lock()
timeout_sweeper_task: asyncio.Task | None = None

CHALLENGE_POLICIES = {"auto_accept", "manual_approve", "reject_all"}
ACTIONABLE_CHALLENGE_STATUSES = {"pending", "owner_review"}
MATCH_TIMEOUT_SWEEP_INTERVAL_SEC = 2


class RegisterReq(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    avatar_url: str | None = None
    description: str | None = None
    chess_style: str = "random"
    persona_prompt: str | None = None
    engine_mode: str = "random"
    client_type: str = "astrbot"
    instance_name: str | None = None
    is_public: bool = True
    challenge_policy: str = "auto_accept"
    owner_review_timeout_sec: int = 180
    game: str = DEFAULT_GAME


class BotUpdateReq(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    avatar_url: str | None = None
    description: str | None = None
    chess_style: str | None = None
    persona_prompt: str | None = None
    is_public: bool | None = None
    is_enabled: bool | None = None
    challenge_policy: str | None = None
    owner_review_timeout_sec: int | None = Field(default=None, ge=1, le=3600)
    game: str | None = None


class ChallengeReq(BaseModel):
    opponent_bot_id: str
    side: Side | None = None
    game: str | None = None


class OwnerDecisionReq(BaseModel):
    decision: str
    reason: str | None = Field(default=None, max_length=500)


class MoveReq(BaseModel):
    move: str = Field(min_length=2, max_length=4)
    comment: str | None = None
    duration_ms: int | None = None


class AnalyzeReq(BaseModel):
    fen: str
    depth: int = 3


class AdminBotCreateReq(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    token: str | None = None
    avatar_url: str | None = None
    description: str | None = None
    chess_style: str = "random"
    persona_prompt: str | None = None
    is_public: bool = True
    is_enabled: bool = True
    challenge_policy: str = "auto_accept"
    owner_review_timeout_sec: int = 180
    game: str = DEFAULT_GAME


def db_connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def init_db() -> None:
    with db_connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS bots (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                token TEXT NOT NULL UNIQUE,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_bots_token ON bots(token);

            CREATE TABLE IF NOT EXISTS challenges (
                id TEXT PRIMARY KEY,
                challenger_bot_id TEXT NOT NULL,
                opponent_bot_id TEXT NOT NULL,
                challenger_side TEXT NOT NULL,
                status TEXT NOT NULL,
                match_id TEXT,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_challenges_created_at ON challenges(created_at DESC);

            CREATE TABLE IF NOT EXISTS matches (
                id TEXT PRIMARY KEY,
                red_bot_id TEXT NOT NULL,
                black_bot_id TEXT NOT NULL,
                fen TEXT NOT NULL,
                status TEXT NOT NULL,
                result TEXT,
                ply INTEGER NOT NULL,
                moves_json TEXT NOT NULL,
                challenge_id TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_matches_updated_at ON matches(updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_matches_created_at ON matches(created_at DESC);

            CREATE TABLE IF NOT EXISTS rankings (
                bot_id TEXT PRIMARY KEY,
                rating INTEGER DEFAULT 1000,
                games INTEGER DEFAULT 0,
                wins INTEGER DEFAULT 0,
                losses INTEGER DEFAULT 0,
                draws INTEGER DEFAULT 0,
                win_rate REAL DEFAULT 0,
                streak INTEGER DEFAULT 0,
                updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS rating_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bot_id TEXT NOT NULL,
                rating INTEGER NOT NULL,
                match_id TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_rating_history_bot ON rating_history(bot_id, created_at);

            CREATE TABLE IF NOT EXISTS match_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bot_id TEXT UNIQUE NOT NULL,
                rating INTEGER DEFAULT 1000,
                joined_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS moves (
                id TEXT PRIMARY KEY,
                match_id TEXT NOT NULL,
                ply INTEGER NOT NULL,
                bot_id TEXT NOT NULL,
                side TEXT NOT NULL,
                move TEXT NOT NULL,
                fen_before TEXT NOT NULL,
                fen_after TEXT NOT NULL,
                captured TEXT,
                comment TEXT,
                duration_ms INTEGER,
                created_at REAL NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_moves_match_ply ON moves(match_id, ply);
            CREATE INDEX IF NOT EXISTS idx_moves_match_id ON moves(match_id);
            """
        )
        ensure_columns(conn, "bots", {
            "avatar_url": "TEXT",
            "description": "TEXT",
            "chess_style": "TEXT DEFAULT 'random'",
            "persona_prompt": "TEXT",
            "engine_mode": "TEXT DEFAULT 'random'",
            "client_type": "TEXT DEFAULT 'astrbot'",
            "instance_name": "TEXT",
            "is_public": "INTEGER DEFAULT 1",
            "is_enabled": "INTEGER DEFAULT 1",
            "online_status": "TEXT DEFAULT 'offline'",
            "last_seen_at": "REAL",
            "updated_at": "REAL",
            "challenge_policy": "TEXT DEFAULT 'auto_accept'",
            "owner_review_timeout_sec": "INTEGER DEFAULT 180",
            "game": "TEXT DEFAULT 'xiangqi'",
        })
        ensure_columns(conn, "challenges", {
            "owner_decision": "TEXT",
            "owner_decision_reason": "TEXT",
            "expires_at": "REAL",
            "updated_at": "REAL",
            "game": "TEXT DEFAULT 'xiangqi'",
        })
        ensure_columns(conn, "matches", {
            "game": "TEXT DEFAULT 'xiangqi'",
            "winner_bot_id": "TEXT",
            "finish_reason": "TEXT",
            "paused": "INTEGER DEFAULT 0",
            "red_time_left_ms": "INTEGER",
            "black_time_left_ms": "INTEGER",
            "total_time_ms": "INTEGER",
            "last_move_at": "REAL",
            "started_at": "REAL",
            "finished_at": "REAL",
        })
        ensure_columns(conn, "match_queue", {
            "game": "TEXT DEFAULT 'xiangqi'",
        })
        now = time.time()
        conn.execute("UPDATE bots SET updated_at = COALESCE(updated_at, created_at, ?)", (now,))
        conn.execute("UPDATE bots SET challenge_policy = COALESCE(NULLIF(challenge_policy, ''), 'auto_accept')")
        conn.execute("UPDATE bots SET owner_review_timeout_sec = COALESCE(owner_review_timeout_sec, 180)")
        conn.execute("UPDATE challenges SET updated_at = COALESCE(updated_at, created_at, ?)", (now,))
        conn.execute("INSERT OR IGNORE INTO rankings(bot_id, rating, games, wins, losses, draws, win_rate, streak, updated_at) SELECT id, 1000, 0, 0, 0, 0, 0, 0, ? FROM bots", (now,))


def ensure_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, ddl in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def load_state_from_db() -> None:
    init_db()
    bots.clear()
    tokens.clear()
    challenges.clear()
    matches.clear()
    match_queue_entries.clear()
    with db_connect() as conn:
        for row in conn.execute("SELECT * FROM bots"):
            bot = Bot(
                id=row["id"], name=row["name"], token=row["token"], created_at=row["created_at"],
                avatar_url=row["avatar_url"], description=row["description"], chess_style=row["chess_style"] or "random",
                persona_prompt=row["persona_prompt"], engine_mode=row["engine_mode"] or "random",
                client_type=row["client_type"] or "astrbot", instance_name=row["instance_name"],
                is_public=bool(row["is_public"]), is_enabled=bool(row["is_enabled"]),
                online_status=row["online_status"] or "offline", last_seen_at=row["last_seen_at"],
                updated_at=row["updated_at"] or row["created_at"],
                challenge_policy=row["challenge_policy"] if row["challenge_policy"] in CHALLENGE_POLICIES else "auto_accept",
                owner_review_timeout_sec=int(row["owner_review_timeout_sec"] or 180),
                game=row["game"] or DEFAULT_GAME,
            )
            bots[bot.id] = bot
            tokens[bot.token] = bot.id
        for row in conn.execute("SELECT * FROM challenges"):
            challenges[row["id"]] = Challenge(
                id=row["id"],
                challenger_bot_id=row["challenger_bot_id"],
                opponent_bot_id=row["opponent_bot_id"],
                challenger_side=row["challenger_side"],
                status=row["status"],
                match_id=row["match_id"],
                created_at=row["created_at"],
                owner_decision=row["owner_decision"],
                owner_decision_reason=row["owner_decision_reason"],
                expires_at=row["expires_at"],
                updated_at=row["updated_at"] or row["created_at"],
                game=row["game"] or DEFAULT_GAME,
            )
        for row in conn.execute("SELECT * FROM matches"):
            try:
                moves = json.loads(row["moves_json"] or "[]")
            except json.JSONDecodeError:
                moves = []
            matches[row["id"]] = Match(
                id=row["id"],
                red_bot_id=row["red_bot_id"],
                black_bot_id=row["black_bot_id"],
                fen=row["fen"],
                status=row["status"],
                result=row["result"],
                winner_bot_id=row["winner_bot_id"],
                finish_reason=row["finish_reason"],
                ply=row["ply"],
                moves=moves,
                challenge_id=row["challenge_id"],
                paused=bool(row["paused"]),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                red_time_left_ms=row["red_time_left_ms"] if row["red_time_left_ms"] is not None else 1_800_000,
                black_time_left_ms=row["black_time_left_ms"] if row["black_time_left_ms"] is not None else 1_800_000,
                total_time_ms=row["total_time_ms"] if row["total_time_ms"] is not None else 1_800_000,
                last_move_at=row["last_move_at"],
                started_at=row["started_at"],
                finished_at=row["finished_at"],
                game=row["game"] or DEFAULT_GAME,
            )

        for row in conn.execute("SELECT * FROM match_queue"):
            match_queue_entries[row["bot_id"]] = {
                "bot_id": row["bot_id"],
                "rating": row["rating"] or 1000,
                "joined_at": row["joined_at"],
                "game": row["game"] or DEFAULT_GAME,
            }


def save_bot(bot: Bot) -> None:
    with db_connect() as conn:
        conn.execute(
            """INSERT INTO bots(id, name, token, created_at, avatar_url, description, chess_style, persona_prompt, engine_mode, client_type, instance_name, is_public, is_enabled, online_status, last_seen_at, updated_at, challenge_policy, owner_review_timeout_sec, game)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET name=excluded.name, token=excluded.token, created_at=excluded.created_at,
                 avatar_url=excluded.avatar_url, description=excluded.description, chess_style=excluded.chess_style,
                 persona_prompt=excluded.persona_prompt, engine_mode=excluded.engine_mode, client_type=excluded.client_type,
                 instance_name=excluded.instance_name, is_public=excluded.is_public, is_enabled=excluded.is_enabled,
                 online_status=excluded.online_status, last_seen_at=excluded.last_seen_at, updated_at=excluded.updated_at,
                 challenge_policy=excluded.challenge_policy, owner_review_timeout_sec=excluded.owner_review_timeout_sec, game=excluded.game""",
            (bot.id, bot.name, bot.token, bot.created_at, bot.avatar_url, bot.description, bot.chess_style, bot.persona_prompt, bot.engine_mode, bot.client_type, bot.instance_name, int(bot.is_public), int(bot.is_enabled), bot.online_status, bot.last_seen_at, bot.updated_at, bot.challenge_policy, bot.owner_review_timeout_sec, bot.game),
        )
        conn.execute("INSERT OR IGNORE INTO rankings(bot_id, rating, games, wins, losses, draws, win_rate, streak, updated_at) VALUES(?, 1000, 0, 0, 0, 0, 0, 0, ?)", (bot.id, time.time()))


def save_challenge(ch: Challenge) -> None:
    with db_connect() as conn:
        conn.execute(
            """INSERT INTO challenges(id, challenger_bot_id, opponent_bot_id, challenger_side, status, match_id, created_at, owner_decision, owner_decision_reason, expires_at, updated_at, game)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 challenger_bot_id=excluded.challenger_bot_id,
                 opponent_bot_id=excluded.opponent_bot_id,
                 challenger_side=excluded.challenger_side,
                 status=excluded.status,
                 match_id=excluded.match_id,
                 created_at=excluded.created_at,
                 owner_decision=excluded.owner_decision,
                 owner_decision_reason=excluded.owner_decision_reason,
                 expires_at=excluded.expires_at,
                 updated_at=excluded.updated_at,
                 game=excluded.game""",
            (ch.id, ch.challenger_bot_id, ch.opponent_bot_id, ch.challenger_side, ch.status, ch.match_id, ch.created_at, ch.owner_decision, ch.owner_decision_reason, ch.expires_at, ch.updated_at, ch.game),
        )


def save_match(m: Match) -> None:
    with db_connect() as conn:
        conn.execute(
            """INSERT INTO matches(id, red_bot_id, black_bot_id, fen, status, result, winner_bot_id, finish_reason, ply, moves_json, challenge_id, paused, created_at, updated_at, red_time_left_ms, black_time_left_ms, total_time_ms, last_move_at, started_at, finished_at, game)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 red_bot_id=excluded.red_bot_id,
                 black_bot_id=excluded.black_bot_id,
                 fen=excluded.fen,
                 status=excluded.status,
                 result=excluded.result,
                 winner_bot_id=excluded.winner_bot_id,
                 finish_reason=excluded.finish_reason,
                 ply=excluded.ply,
                 moves_json=excluded.moves_json,
                 challenge_id=excluded.challenge_id,
                 paused=excluded.paused,
                 created_at=excluded.created_at,
                 updated_at=excluded.updated_at,
                 red_time_left_ms=excluded.red_time_left_ms,
                 black_time_left_ms=excluded.black_time_left_ms,
                 total_time_ms=excluded.total_time_ms,
                 last_move_at=excluded.last_move_at,
                 started_at=excluded.started_at,
                 finished_at=excluded.finished_at,
                 game=excluded.game""",
            (m.id, m.red_bot_id, m.black_bot_id, m.fen, m.status, m.result, m.winner_bot_id, m.finish_reason, m.ply, json.dumps(m.moves, ensure_ascii=False), m.challenge_id, int(m.paused), m.created_at, m.updated_at, m.red_time_left_ms, m.black_time_left_ms, m.total_time_ms, m.last_move_at, m.started_at, m.finished_at, m.game),
        )
        for mv in m.moves:
            move_id = mv.get("move_id") or f"move_{m.id}_{mv.get('ply')}"
            mv["move_id"] = move_id
            conn.execute(
                """INSERT OR IGNORE INTO moves(id, match_id, ply, bot_id, side, move, fen_before, fen_after, captured, comment, duration_ms, created_at)
                   VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (move_id, m.id, mv.get("ply"), mv.get("bot_id"), mv.get("side"), mv.get("move"), mv.get("fen_before"), mv.get("fen_after"), json.dumps(mv.get("captured"), ensure_ascii=False) if isinstance(mv.get("captured"), (list, dict)) else mv.get("captured"), mv.get("comment"), mv.get("duration_ms"), mv.get("created_at")),
            )


def refresh_state_from_db() -> None:
    # Keep the public 8787 process in sync with changes made by the separate
    # 8788 admin service. The platform stores hot state in memory for SSE/matches,
    # so DB-only account changes must be pulled before read/auth operations.
    load_state_from_db()


async def finish_timeout_match_locked(m: Match, now: float | None = None) -> dict[str, Any] | None:
    """Finish an active match whose side-to-move clock has expired.

    Caller must hold state_lock. The normal move endpoint checks this right
    before accepting a move; the background sweeper uses the same helper so a
    Bot that never submits again still loses on time.
    """
    if m.status != "active" or m.paused:
        return None
    # Go MVP stores JSON state in Match.fen; do not send it through xiangqi parse_fen.
    # Go clocks/forfeit timeout will be implemented separately after the MVP is stable.
    if m.game == "go":
        return None
    now = now or time.time()
    _, turn, _, _ = parse_fen(m.fen)
    if m.ply == 0:
        base = m.started_at or m.last_move_at or m.created_at
    else:
        base = m.last_move_at or m.started_at or m.created_at
    elapsed_ms = max(0, int((now - base) * 1000))
    if turn == RED:
        remaining = max(0, m.red_time_left_ms - elapsed_ms)
        if remaining > 0:
            return None
        m.red_time_left_ms = 0
        winner = BLACK
        winner_bot_id = m.black_bot_id
    else:
        remaining = max(0, m.black_time_left_ms - elapsed_ms)
        if remaining > 0:
            return None
        m.black_time_left_ms = 0
        winner = RED
        winner_bot_id = m.red_bot_id
    m.status = "finished"
    m.result = f"{winner}_win"
    m.winner_bot_id = winner_bot_id
    m.finish_reason = "timeout"
    m.last_move_at = now
    m.updated_at = now
    m.finished_at = now
    save_match(m)
    update_rankings_for_finished_match(m)
    return {"match": match_public(m), "timeout": True, "side": turn}


async def sweep_match_timeouts_once() -> list[tuple[str, list[str], dict[str, Any]]]:
    finished: list[tuple[str, list[str], dict[str, Any]]] = []
    async with state_lock:
        now = time.time()
        for m in list(matches.values()):
            data = await finish_timeout_match_locked(m, now)
            if not data:
                continue
            finished.append((m.id, [m.red_bot_id, m.black_bot_id], data))
    for match_id, participants, data in finished:
        for pid in participants:
            await emit(pid, "match_finished", data["match"])
        await broadcast_match_sse(match_id)
    return finished


async def match_timeout_sweeper() -> None:
    while True:
        await asyncio.sleep(MATCH_TIMEOUT_SWEEP_INTERVAL_SEC)
        try:
            await sweep_match_timeouts_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[match-timeout-sweeper] error: {exc}")


@app.on_event("startup")
async def startup_load_state() -> None:
    global timeout_sweeper_task
    load_state_from_db()
    if timeout_sweeper_task is None or timeout_sweeper_task.done():
        timeout_sweeper_task = asyncio.create_task(match_timeout_sweeper())


@app.on_event("shutdown")
async def shutdown_timeout_sweeper() -> None:
    global timeout_sweeper_task
    if timeout_sweeper_task is not None:
        timeout_sweeper_task.cancel()
        try:
            await timeout_sweeper_task
        except asyncio.CancelledError:
            pass
        timeout_sweeper_task = None


# Ensure TestClient and direct imports have a usable state before startup hooks run.
load_state_from_db()


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(10)}"


def bot_public(bot: Bot) -> dict[str, Any]:
    return {
        "bot_id": bot.id,
        "name": bot.name,
        "avatar_url": bot.avatar_url or "",
        "description": bot.description or "",
        "chess_style": bot.chess_style,
        "persona_prompt": bot.persona_prompt or "",
        "engine_mode": bot.engine_mode,
        "client_type": bot.client_type,
        "instance_name": bot.instance_name or "",
        "is_public": bot.is_public,
        "is_enabled": bot.is_enabled,
        "online_status": bot.online_status,
        "last_seen_at": bot.last_seen_at,
        "created_at": bot.created_at,
        "updated_at": bot.updated_at,
        "challenge_policy": bot.challenge_policy,
        "owner_review_timeout_sec": bot.owner_review_timeout_sec,
        "game": bot.game,
    }


def token_hint(token: str) -> str:
    return token[:6] + "..." + token[-4:] if len(token) > 12 else token[:3] + "..."


def ranking_for(bot_id: str) -> dict[str, Any]:
    with db_connect() as conn:
        row = conn.execute("SELECT * FROM rankings WHERE bot_id = ?", (bot_id,)).fetchone()
    if not row:
        return {"rating": 1000, "games": 0, "wins": 0, "losses": 0, "draws": 0, "win_rate": 0, "streak": 0}
    return {"rating": row["rating"], "games": row["games"], "wins": row["wins"], "losses": row["losses"], "draws": row["draws"], "win_rate": row["win_rate"], "streak": row["streak"]}


def bot_with_ranking(bot: Bot) -> dict[str, Any]:
    data = bot_public(bot)
    data.update(ranking_for(bot.id))
    return data


def admin_bot_payload(bot: Bot) -> dict[str, Any]:
    data = bot_with_ranking(bot)
    data["token"] = bot.token
    data["token_hint"] = token_hint(bot.token)
    return data


def delete_bot_from_db(bot_id: str) -> None:
    with db_connect() as conn:
        match_ids = [row["id"] for row in conn.execute("SELECT id FROM matches WHERE red_bot_id = ? OR black_bot_id = ?", (bot_id, bot_id)).fetchall()]
        for match_id in match_ids:
            conn.execute("DELETE FROM moves WHERE match_id = ?", (match_id,))
            conn.execute("DELETE FROM rating_history WHERE match_id = ?", (match_id,))
        conn.execute("DELETE FROM matches WHERE red_bot_id = ? OR black_bot_id = ?", (bot_id, bot_id))
        conn.execute("DELETE FROM challenges WHERE challenger_bot_id = ? OR opponent_bot_id = ?", (bot_id, bot_id))
        conn.execute("DELETE FROM match_queue WHERE bot_id = ?", (bot_id,))
        conn.execute("DELETE FROM rating_history WHERE bot_id = ?", (bot_id,))
        conn.execute("DELETE FROM rankings WHERE bot_id = ?", (bot_id,))
        conn.execute("DELETE FROM bots WHERE id = ?", (bot_id,))


def update_rankings_for_finished_match(m: Match) -> None:
    if m.status != "finished" or not m.result:
        return
    red = ranking_for(m.red_bot_id)
    black = ranking_for(m.black_bot_id)
    if m.result == "draw":
        red_score = black_score = 0.5
    elif m.result == "red_win":
        red_score, black_score = 1.0, 0.0
    elif m.result == "black_win":
        red_score, black_score = 0.0, 1.0
    else:
        return
    def new_rating(rating: int, opp: int, score: float) -> int:
        expected = 1 / (1 + 10 ** ((opp - rating) / 400))
        return round(rating + 32 * (score - expected))
    red_rating = new_rating(int(red["rating"]), int(black["rating"]), red_score)
    black_rating = new_rating(int(black["rating"]), int(red["rating"]), black_score)
    now = time.time()
    rows = [(m.red_bot_id, red, red_score, red_rating), (m.black_bot_id, black, black_score, black_rating)]
    with db_connect() as conn:
        for bot_id, old, score, rating in rows:
            games = int(old["games"]) + 1
            wins = int(old["wins"]) + (1 if score == 1 else 0)
            losses = int(old["losses"]) + (1 if score == 0 else 0)
            draws = int(old["draws"]) + (1 if score == 0.5 else 0)
            streak = 0 if score == 0.5 else (int(old["streak"]) + 1 if score == 1 else -1 if int(old["streak"]) >= 0 else int(old["streak"]) - 1)
            win_rate = wins / games if games else 0
            conn.execute(
                """INSERT INTO rankings(bot_id, rating, games, wins, losses, draws, win_rate, streak, updated_at)
                   VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(bot_id) DO UPDATE SET rating=excluded.rating, games=excluded.games, wins=excluded.wins,
                     losses=excluded.losses, draws=excluded.draws, win_rate=excluded.win_rate, streak=excluded.streak, updated_at=excluded.updated_at""",
                (bot_id, rating, games, wins, losses, draws, win_rate, streak, now),
            )
            conn.execute(
                "INSERT INTO rating_history(bot_id, rating, match_id, created_at) VALUES(?, ?, ?, ?)",
                (bot_id, rating, m.id, fmt_ts(now)),
            )


def recompute_rankings_from_visible_matches() -> None:
    """Rebuild rankings from matches whose two bots still exist.

    Deleting a bot removes its games from the visible match universe, so every
    remaining bot's game count/rating must be recalculated instead of keeping
    stale counts from matches that no longer exist.
    """
    now = time.time()
    with db_connect() as conn:
        conn.execute("DELETE FROM rating_history")
        conn.execute("DELETE FROM rankings")
        conn.execute(
            "INSERT INTO rankings(bot_id, rating, games, wins, losses, draws, win_rate, streak, updated_at) SELECT id, 1000, 0, 0, 0, 0, 0, 0, ? FROM bots",
            (now,),
        )
    finished = sorted(
        [m for m in visible_matches() if m.status == "finished" and m.result in {"red_win", "black_win", "draw"}],
        key=lambda m: (m.finished_at or m.updated_at or m.created_at, m.created_at, m.id),
    )
    for m in finished:
        update_rankings_for_finished_match(m)


def challenge_public(ch: Challenge) -> dict[str, Any]:
    return {
        "challenge_id": ch.id,
        "challenger_bot_id": ch.challenger_bot_id,
        "challenger_name": bot_name(ch.challenger_bot_id),
        "opponent_bot_id": ch.opponent_bot_id,
        "opponent_name": bot_name(ch.opponent_bot_id),
        "challenger_side": ch.challenger_side,
        "game": ch.game,
        "status": ch.status,
        "match_id": ch.match_id,
        "owner_decision": ch.owner_decision,
        "owner_decision_reason": ch.owner_decision_reason,
        "requires_owner_decision": ch.status == "owner_review",
        "created_at": ch.created_at,
        "updated_at": ch.updated_at,
        "expires_at": ch.expires_at,
    }


def bot_name(bot_id: str) -> str:
    bot = bots.get(bot_id)
    return bot.name if bot else bot_id


def match_public(m: Match, include_legal_moves: bool = True) -> dict[str, Any]:
    if m.game == "go":
        state = go9.loads_state(m.fen)
        turn = state["turn"]
        turn_bot_id = m.black_bot_id if turn == go9.BLACK else m.red_bot_id
        data = {
            "match_id": m.id,
            "game": m.game,
            "red_bot_id": m.red_bot_id,
            "red_bot_name": bot_name(m.red_bot_id),
            "red_bot_avatar_url": (bots.get(m.red_bot_id).avatar_url if bots.get(m.red_bot_id) else "") or "",
            "white_bot_id": m.red_bot_id,
            "white_bot_name": bot_name(m.red_bot_id),
            "white_bot_avatar_url": (bots.get(m.red_bot_id).avatar_url if bots.get(m.red_bot_id) else "") or "",
            "black_bot_id": m.black_bot_id,
            "black_bot_name": bot_name(m.black_bot_id),
            "black_bot_avatar_url": (bots.get(m.black_bot_id).avatar_url if bots.get(m.black_bot_id) else "") or "",
            "fen": m.fen,
            "state_json": m.fen,
            "state": state,
            "board": state["board"],
            "turn": turn,
            "turn_bot_id": turn_bot_id,
            "captures": state.get("captures", {}),
            "passes": state.get("passes", 0),
            "status": m.status,
            "result": m.result,
            "winner_bot_id": m.winner_bot_id,
            "finish_reason": m.finish_reason,
            "ply": m.ply,
            "moves": m.moves,
            "challenge_id": m.challenge_id,
            "paused": m.paused,
            "created_at": m.created_at,
            "updated_at": m.updated_at,
            "red_time_left_ms": m.red_time_left_ms,
            "black_time_left_ms": m.black_time_left_ms,
            "total_time_ms": m.total_time_ms,
        }
        return data

    _, turn, _, _ = parse_fen(m.fen)
    data = {
        "match_id": m.id,
        "game": m.game,
        "red_bot_id": m.red_bot_id,
        "red_bot_name": bot_name(m.red_bot_id),
        "red_bot_avatar_url": (bots.get(m.red_bot_id).avatar_url if bots.get(m.red_bot_id) else "") or "",
        "black_bot_id": m.black_bot_id,
        "black_bot_name": bot_name(m.black_bot_id),
        "black_bot_avatar_url": (bots.get(m.black_bot_id).avatar_url if bots.get(m.black_bot_id) else "") or "",
        "fen": m.fen,
        "turn": turn,
        "turn_bot_id": m.red_bot_id if turn == RED else m.black_bot_id,
        "status": m.status,
        "result": m.result,
        "winner_bot_id": m.winner_bot_id,
        "finish_reason": m.finish_reason,
        "ply": m.ply,
        "moves": m.moves,
        "challenge_id": m.challenge_id,
        "paused": m.paused,
        "created_at": m.created_at,
        "updated_at": m.updated_at,
        "red_time_left_ms": m.red_time_left_ms,
        "black_time_left_ms": m.black_time_left_ms,
        "total_time_ms": m.total_time_ms,
    }
    if include_legal_moves and m.status == "active":
        data["legal_moves"] = legal_moves(m.fen)
    return data


def admin_match_summary(m: Match) -> dict[str, Any]:
    summary = {k: v for k, v in match_public(m, include_legal_moves=False).items() if k != "moves"} | {"move_count": len(m.moves)}
    if m.winner_bot_id:
        summary["winner_bot_name"] = bot_name(m.winner_bot_id)
    return summary


def match_has_known_bots(m: Match) -> bool:
    return m.red_bot_id in bots and m.black_bot_id in bots


def visible_matches() -> list[Match]:
    """Matches shown in public/admin lists must have both participants still present.

    If a bot was deleted directly from the database, old matches can become orphaned.
    Those games should no longer contribute to homepage/recent-match totals.
    """
    return [m for m in matches.values() if match_has_known_bots(m)]


def active_match_for_bot(bot_id: str, game: str | None = None) -> Match | None:
    game = normalize_game(game) if game else None
    for m in matches.values():
        if game and m.game != game:
            continue
        if m.status == "active" and bot_id in (m.red_bot_id, m.black_bot_id):
            return m
    return None


def ensure_bot_available(bot_id: str, game: str | None = None) -> None:
    m = active_match_for_bot(bot_id, game)
    if not m:
        return
    raise HTTPException(
        status_code=409,
        detail={
            "code": "bot_busy",
            "message": "bot is already in an active match",
            "bot_id": bot_id,
            "match_id": m.id,
            "game": m.game,
        },
    )


def remove_bot_from_queue(bot_id: str) -> None:
    match_queue_entries.pop(bot_id, None)
    with db_connect() as conn:
        conn.execute("DELETE FROM match_queue WHERE bot_id = ?", (bot_id,))


def create_match_from_challenge(ch: Challenge) -> Match:
    ensure_bot_available(ch.challenger_bot_id, ch.game)
    ensure_bot_available(ch.opponent_bot_id, ch.game)
    red_id = ch.challenger_bot_id if ch.challenger_side == RED else ch.opponent_bot_id
    black_id = ch.opponent_bot_id if ch.challenger_side == RED else ch.challenger_bot_id
    fen = go9.initial_state_json() if ch.game == "go" else INITIAL_FEN
    return Match(id=new_id("match"), red_bot_id=red_id, black_bot_id=black_id, fen=fen, challenge_id=ch.id, game=ch.game)


def expire_challenge_if_needed(ch: Challenge, now: float | None = None) -> bool:
    """Mark timed-out owner-review challenges as expired before listing/replaying/deciding.

    Older rows can survive process restarts, so this check is deliberately
    centralized instead of only living in the owner_decision endpoint.
    """
    if ch.status != "owner_review" or not ch.expires_at:
        return False
    now = now or time.time()
    if ch.expires_at >= now:
        return False
    ch.status = "expired"
    ch.updated_at = now
    save_challenge(ch)
    return True


def challenge_is_actionable(ch: Challenge) -> bool:
    expire_challenge_if_needed(ch)
    return ch.status in ACTIONABLE_CHALLENGE_STATUSES


def challenge_received_payload(ch: Challenge) -> dict[str, Any]:
    return {
        **challenge_public(ch),
        "type": "challenge_received",
        "requires_owner_decision": ch.status == "owner_review",
    }


def owner_decision_response(ch: Challenge, m: Match | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {"challenge": challenge_public(ch), "match": match_public(m) if m else None}
    if m:
        data["match_id"] = m.id
        data["match_url"] = match_url(m)
    return data


def match_url(m: Match) -> str:
    base_url = os.environ.get("CHESS_ARENA_PUBLIC_BASE_URL", "").strip().rstrip("/")
    path = f"/matches/{m.id}"
    return f"{base_url}{path}" if base_url else path


def accept_challenge_locked(ch: Challenge, owner_decision: str | None = None, owner_decision_reason: str | None = None) -> Match:
    m = create_match_from_challenge(ch)
    matches[m.id] = m
    now = time.time()
    ch.status = "accepted"
    ch.match_id = m.id
    if owner_decision is not None:
        ch.owner_decision = owner_decision
    if owner_decision_reason is not None:
        ch.owner_decision_reason = owner_decision_reason
    ch.updated_at = now
    save_challenge(ch)
    save_match(m)
    return m


def fmt_ts(ts: float | None) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts or 0))


def bearer_token(authorization: str | None = Header(default=None)) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    return authorization.split(" ", 1)[1].strip()


def optional_bearer_token(authorization: str | None = Header(default=None)) -> str | None:
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    return authorization.split(" ", 1)[1].strip()


def is_admin_token(token: str | None) -> bool:
    return bool(ADMIN_TOKEN and token and hmac.compare_digest(token, ADMIN_TOKEN))


def require_admin(token: str | None = Depends(optional_bearer_token)) -> None:
    if not is_admin_token(token):
        raise HTTPException(status_code=403, detail="admin token required")


def actor_bot_or_admin(token: str | None = Depends(optional_bearer_token)) -> tuple[Bot | None, bool]:
    if is_admin_token(token):
        return None, True
    if not token:
        raise HTTPException(status_code=401, detail="missing bearer token")
    bot_id = tokens.get(token)
    if not bot_id or bot_id not in bots:
        raise HTTPException(status_code=401, detail="invalid token")
    return bots[bot_id], False


def ensure_match_control_allowed(m: Match, bot: Bot | None, is_admin: bool) -> None:
    if is_admin:
        return
    if not bot or bot.id not in (m.red_bot_id, m.black_bot_id):
        raise HTTPException(status_code=403, detail="only participants or admin can control this match")


async def x_bot_token(
    x_bot_token: str | None = Header(default=None, alias="X-Bot-Token"),
    authorization: str | None = Header(default=None),
) -> str:
    token = x_bot_token
    if not token and authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(status_code=401, detail="missing X-Bot-Token header")
    bot_id = tokens.get(token)
    if not bot_id or bot_id not in bots:
        raise HTTPException(status_code=401, detail="invalid token")
    return bot_id


async def get_current_bot(token: str = Depends(bearer_token)) -> Bot:
    bot_id = tokens.get(token)
    if not bot_id or bot_id not in bots:
        raise HTTPException(status_code=401, detail="invalid token")
    return bots[bot_id]


async def emit(bot_id: str, event: str, data: dict[str, Any]) -> None:
    payload = {"event": event, "data": data, "ts": time.time()}
    for q in list(subscribers.get(bot_id, set())):
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            try:
                q.get_nowait()
                q.put_nowait(payload)
            except Exception:
                pass


async def emit_match_started(m: Match) -> None:
    data = match_public(m)
    await emit(m.red_bot_id, "match_started", {**data, "side": RED})
    await emit(m.black_bot_id, "match_started", {**data, "side": BLACK})
    await emit_turn(m)


def current_turn_bot_id(m: Match) -> str:
    if m.game == "go":
        state = go9.loads_state(m.fen)
        return m.black_bot_id if state["turn"] == go9.BLACK else m.red_bot_id
    _, turn, _, _ = parse_fen(m.fen)
    return m.red_bot_id if turn == RED else m.black_bot_id


async def emit_pending_turns_for_bot(bot_id: str) -> int:
    """Re-send any currently pending turn for a reconnecting bot."""
    emitted = 0
    for m in list(matches.values()):
        if m.status != "active" or m.paused:
            continue
        if current_turn_bot_id(m) != bot_id:
            continue
        await emit_turn(m)
        emitted += 1
    return emitted


async def emit_pending_challenges_for_bot(bot_id: str) -> int:
    """Best-effort replay of pending challenges for a reconnecting bot."""
    emitted = 0
    for ch in list(challenges.values()):
        if challenge_is_actionable(ch) and ch.opponent_bot_id == bot_id:
            await emit(bot_id, "challenge_received", challenge_received_payload(ch))
            emitted += 1
    return emitted


async def emit_turn(m: Match) -> None:
    if m.status != "active" or m.paused:
        return
    if m.game == "go":
        state = go9.loads_state(m.fen)
        turn = state["turn"]
        bot_id = m.black_bot_id if turn == go9.BLACK else m.red_bot_id
    else:
        _, turn, _, _ = parse_fen(m.fen)
        bot_id = m.red_bot_id if turn == RED else m.black_bot_id
    await emit(bot_id, "your_turn", {**match_public(m), "side": turn})


def choose_challenger_side(side: Side | None) -> str:
    if side is None or side == Side.random:
        return RED if secrets.randbelow(2) == 0 else BLACK
    return side.value


def validate_challenge_policy(policy: str) -> str:
    if policy not in CHALLENGE_POLICIES:
        raise HTTPException(status_code=400, detail="invalid challenge_policy")
    return policy


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/bots/register")
async def register(req: RegisterReq) -> dict[str, Any]:
    policy = validate_challenge_policy(req.challenge_policy)
    game = normalize_game(req.game)
    async with state_lock:
        now = time.time()
        bot = Bot(
            id=new_id("bot"), name=req.name, token=secrets.token_urlsafe(32), created_at=now, updated_at=now, game=game,
            avatar_url=req.avatar_url, description=req.description, chess_style=req.chess_style or "random",
            persona_prompt=req.persona_prompt, engine_mode=req.engine_mode or "random", client_type=req.client_type or "astrbot",
            instance_name=req.instance_name, is_public=req.is_public, challenge_policy=policy,
            owner_review_timeout_sec=req.owner_review_timeout_sec,
        )
        bots[bot.id] = bot
        tokens[bot.token] = bot.id
        save_bot(bot)
    return {"bot_id": bot.id, "token": bot.token, "name": bot.name, "game": bot.game}


@app.get("/api/bots/me")
async def me(bot: Bot = Depends(get_current_bot)) -> dict[str, Any]:
    return {**bot_public(bot), "token_hint": token_hint(bot.token)}


@app.patch("/api/bots/me")
async def update_me(req: BotUpdateReq, bot: Bot = Depends(get_current_bot)) -> dict[str, Any]:
    async with state_lock:
        updates = req.model_dump(exclude_unset=True)
        if "challenge_policy" in updates and updates["challenge_policy"] is not None:
            updates["challenge_policy"] = validate_challenge_policy(updates["challenge_policy"])
        for key, value in updates.items():
            setattr(bot, key, value)
        bot.updated_at = time.time()
        save_bot(bot)
    return {**bot_public(bot), "token_hint": token_hint(bot.token)}


@app.get("/api/bots")
async def list_bots(
    q: str = "",
    online_only: bool = False,
    game: str = DEFAULT_GAME,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    refresh_state_from_db()
    game = normalize_game(game)
    items = [b for b in bots.values() if b.is_public and b.is_enabled and b.game == game]
    if q:
        q_lower = q.lower()
        items = [b for b in items if q_lower in b.name.lower() or q_lower in (b.description or "").lower() or q_lower in (b.chess_style or "").lower()]
    if online_only:
        items = [b for b in items if b.online_status == "online"]
    items.sort(key=lambda b: (b.online_status != "online", b.name.lower()))
    page = items[offset : offset + limit]
    return {"total": len(items), "limit": limit, "offset": offset, "bots": [bot_with_ranking(b) for b in page]}


@app.get("/api/rankings")
async def get_rankings(game: str = DEFAULT_GAME, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)) -> dict[str, Any]:
    refresh_state_from_db()
    game = normalize_game(game)
    with db_connect() as conn:
        total = conn.execute("SELECT COUNT(*) AS c FROM rankings r JOIN bots b ON b.id = r.bot_id WHERE b.is_public = 1 AND b.is_enabled = 1 AND b.game = ?", (game,)).fetchone()["c"]
        rows = conn.execute(
            """SELECT r.*, b.name, b.avatar_url, b.online_status FROM rankings r JOIN bots b ON b.id = r.bot_id
               WHERE b.is_public = 1 AND b.is_enabled = 1 AND b.game = ?
               ORDER BY r.rating DESC, r.wins DESC, r.games ASC, b.name ASC LIMIT ? OFFSET ?""",
            (game, limit, offset),
        ).fetchall()
    rankings = []
    for i, row in enumerate(rows, start=offset + 1):
        rankings.append({
            "rank": i, "bot_id": row["bot_id"], "name": row["name"], "avatar_url": row["avatar_url"] or "", "online_status": row["online_status"] or "offline",
            "rating": row["rating"], "games": row["games"], "wins": row["wins"], "losses": row["losses"],
            "draws": row["draws"], "win_rate": row["win_rate"], "streak": row["streak"],
        })
    return {"total": total, "limit": limit, "offset": offset, "rankings": rankings}


@app.get("/api/stats/{bot_id}")
async def api_bot_stats(bot_id: str) -> dict[str, Any]:
    refresh_state_from_db()
    if bot_id not in bots:
        raise HTTPException(status_code=404, detail="bot not found")
    bot = bots[bot_id]
    rank = ranking_for(bot_id)
    with db_connect() as conn:
        history_rows = conn.execute(
            "SELECT rating, match_id, created_at FROM rating_history WHERE bot_id = ? ORDER BY created_at DESC LIMIT 50",
            (bot_id,),
        ).fetchall()
        rating_history = [{"rating": r["rating"], "match_id": r["match_id"], "created_at": r["created_at"]} for r in history_rows]

        if bot_id in matches:
            recent = sorted(matches.values(), key=lambda m: m.updated_at, reverse=True)
        else:
            recent = []
        recent_matches = []
        for m in recent:
            if (m.red_bot_id == bot_id or m.black_bot_id == bot_id) and len(recent_matches) < 20:
                opp_id = m.black_bot_id if m.red_bot_id == bot_id else m.red_bot_id
                if m.result:
                    if m.winner_bot_id == bot_id:
                        result_str = "win"
                    elif m.result == "draw":
                        result_str = "draw"
                    else:
                        result_str = "loss"
                else:
                    result_str = "pending"
                recent_matches.append({
                    "id": m.id,
                    "opponent": bot_name(opp_id),
                    "result": result_str,
                    "ply": m.ply,
                    "created_at": fmt_ts(m.created_at),
                })
    return {
        "name": bot.name,
        "rating": rank["rating"],
        "games": rank["games"],
        "wins": rank["wins"],
        "losses": rank["losses"],
        "draws": rank["draws"],
        "win_rate": rank["win_rate"],
        "rating_history": rating_history,
        "recent_matches": recent_matches,
    }


@app.get("/stats/{bot_id}", response_class=HTMLResponse)
async def bot_stats_page(request: Request, bot_id: str) -> HTMLResponse:
    refresh_state_from_db()
    if bot_id not in bots:
        raise HTTPException(status_code=404, detail="bot not found")
    bot = bots[bot_id]
    rank = ranking_for(bot_id)
    with db_connect() as conn:
        history_rows = conn.execute(
            "SELECT rating, match_id, created_at FROM rating_history WHERE bot_id = ? ORDER BY created_at DESC LIMIT 50",
            (bot_id,),
        ).fetchall()
        rating_history = [{"rating": r["rating"], "match_id": r["match_id"], "created_at": r["created_at"]} for r in history_rows]

        recent_matches_sorted = sorted(
            [m for m in matches.values() if m.red_bot_id == bot_id or m.black_bot_id == bot_id],
            key=lambda m: m.updated_at, reverse=True,
        )[:20]
        recent_matches = []
        for m in recent_matches_sorted:
            opp_id = m.black_bot_id if m.red_bot_id == bot_id else m.red_bot_id
            if m.result:
                if m.winner_bot_id == bot_id:
                    result_str = "win"
                elif m.result == "draw":
                    result_str = "draw"
                else:
                    result_str = "loss"
            else:
                result_str = "pending"
            recent_matches.append({
                "id": m.id,
                "opponent": bot_name(opp_id),
                "result": result_str,
                "ply": m.ply,
                "created_at": fmt_ts(m.created_at),
            })
    return templates.TemplateResponse(request, "stats.html", {
        "title": f"{bot.name} 战绩",
        "bot": bot_public(bot),
        "rating": rank["rating"],
        "games": rank["games"],
        "wins": rank["wins"],
        "losses": rank["losses"],
        "draws": rank["draws"],
        "win_rate": rank["win_rate"],
        "rating_history": rating_history,
        "recent_matches": recent_matches,
        "bot_name": bot_name,
    })


@app.get("/sse/bot")
async def sse_bot(request: Request, token: str = Query(...)) -> StreamingResponse:
    bot_id = tokens.get(token)
    if not bot_id or bot_id not in bots:
        raise HTTPException(status_code=401, detail="invalid token")
    q: asyncio.Queue = asyncio.Queue(maxsize=100)
    subscribers.setdefault(bot_id, set()).add(q)
    bots[bot_id].online_status = "online"
    bots[bot_id].last_seen_at = time.time()
    bots[bot_id].updated_at = bots[bot_id].last_seen_at or time.time()
    save_bot(bots[bot_id])
    asyncio.create_task(emit_pending_turns_for_bot(bot_id))
    asyncio.create_task(emit_pending_challenges_for_bot(bot_id))

    async def gen():
        try:
            connected = {"event": "connected", "data": {"bot_id": bot_id, "name": bots[bot_id].name}, "ts": time.time()}
            yield format_sse(connected)
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=15)
                    yield format_sse(msg)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            subscribers.get(bot_id, set()).discard(q)
            if not subscribers.get(bot_id):
                bots[bot_id].online_status = "offline"
                bots[bot_id].last_seen_at = time.time()
                bots[bot_id].updated_at = bots[bot_id].last_seen_at or time.time()
                save_bot(bots[bot_id])

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def format_sse(msg: dict[str, Any]) -> str:
    return f"event: {msg['event']}\ndata: {json.dumps(msg['data'], ensure_ascii=False)}\n\n"


@app.get("/sse/match/{match_id}")
async def sse_match(request: Request, match_id: str) -> StreamingResponse:
    if match_id not in matches:
        raise HTTPException(status_code=404, detail="match not found")
    q: asyncio.Queue = asyncio.Queue(maxsize=128)
    match_sse_queues.setdefault(match_id, set()).add(q)

    async def gen():
        try:
            # Send current state immediately on connect
            m = matches.get(match_id)
            if m:
                initial = {"event": "match_state", "data": match_sse_payload(m), "ts": time.time()}
                yield format_sse(initial)
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=15)
                    yield format_sse(msg)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            match_sse_queues.get(match_id, set()).discard(q)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def match_sse_payload(m: Match) -> dict[str, Any]:
    data = match_public(m, include_legal_moves=False)
    data["last_move"] = m.moves[-1] if m.moves else None
    return data


async def broadcast_match_sse(match_id: str) -> None:
    m = matches.get(match_id)
    if not m:
        return
    payload = {"event": "match_state", "data": match_sse_payload(m), "ts": time.time()}
    for q in list(match_sse_queues.get(match_id, set())):
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            try:
                q.get_nowait()
                q.put_nowait(payload)
            except Exception:
                pass


# ── Queue endpoints ──────────────────────────────────────────────

@app.post("/api/queue/join")
async def queue_join(request: Request, game: str = DEFAULT_GAME, bot_id: str = Depends(x_bot_token)) -> dict[str, Any]:
    game = normalize_game(game)
    async with state_lock:
        ensure_bot_available(bot_id, game)
        if bots[bot_id].game != game:
            raise HTTPException(status_code=400, detail="bot game mismatch")
        # Reject if already in queue
        if bot_id in match_queue_entries:
            raise HTTPException(status_code=400, detail="already in queue")

        # Determine rating
        rank = ranking_for(bot_id)
        rating = rank.get("rating", 1000)
        now_iso = fmt_ts(time.time())

        # Insert into DB and in-memory dict
        with db_connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO match_queue(bot_id, rating, joined_at, game) VALUES(?, ?, ?, ?)",
                (bot_id, rating, now_iso, game),
            )
        match_queue_entries[bot_id] = {"bot_id": bot_id, "rating": rating, "joined_at": now_iso, "game": game}

        matched_bot_id: str | None = None
        matched_rating: int | None = None
        for other_id, entry in list(match_queue_entries.items()):
            if other_id == bot_id:
                continue
            if entry.get("game", DEFAULT_GAME) != game:
                continue
            if active_match_for_bot(other_id, game):
                remove_bot_from_queue(other_id)
                continue
            other_rating = entry.get("rating", 1000)
            if abs(rating - other_rating) <= 200:
                matched_bot_id = other_id
                matched_rating = other_rating
                break

        if matched_bot_id:
            # Remove both from queue
            match_queue_entries.pop(bot_id, None)
            match_queue_entries.pop(matched_bot_id, None)
            with db_connect() as conn:
                conn.execute("DELETE FROM match_queue WHERE bot_id IN (?, ?)", (bot_id, matched_bot_id))

            # Create challenge: current bot challenges the matched bot
            ch = Challenge(
                id=new_id("ch"),
                challenger_bot_id=bot_id,
                opponent_bot_id=matched_bot_id,
                challenger_side=choose_challenger_side(None),
                game=game,
            )
            challenges[ch.id] = ch
            save_challenge(ch)

            # Auto-accept: create match immediately
            m = create_match_from_challenge(ch)
            matches[m.id] = m
            ch.status = "accepted"
            ch.match_id = m.id
            save_challenge(ch)
            save_match(m)

            ch_data = challenge_public(ch)
            m_data = match_public(m)
            # Emit to both bots
            await emit(ch.challenger_bot_id, "challenge_accepted", {**ch_data, "match": m_data})
            await emit(ch.opponent_bot_id, "challenge_accepted", {**ch_data, "match": m_data})
            await emit_match_started(m)

            return {
                "matched": True,
                "match_id": m.id,
                "opponent_bot_id": matched_bot_id,
                "opponent_name": bot_name(matched_bot_id),
                "opponent_rating": matched_rating,
                "match": m_data,
                "game": game,
            }
        else:
            return {
                "matched": False,
                "queue_count": len(match_queue_entries),
                "message": "waiting for opponent",
                "game": game,
            }


@app.post("/api/queue/leave")
async def queue_leave(bot_id: str = Depends(x_bot_token)) -> dict[str, Any]:
    async with state_lock:
        if bot_id not in match_queue_entries:
            raise HTTPException(status_code=400, detail="not in queue")
        match_queue_entries.pop(bot_id, None)
        with db_connect() as conn:
            conn.execute("DELETE FROM match_queue WHERE bot_id = ?", (bot_id,))
    return {"success": True, "queue_count": len(match_queue_entries)}


@app.get("/api/queue/status")
async def queue_status(game: str | None = None) -> dict[str, Any]:
    game = normalize_game(game) if game else None
    queue_list = []
    for bot_id, entry in match_queue_entries.items():
        if game and entry.get("game", DEFAULT_GAME) != game:
            continue
        queue_list.append({
            "bot_id": bot_id,
            "name": bot_name(bot_id),
            "rating": entry.get("rating", 1000),
            "joined_at": entry.get("joined_at", ""),
            "game": entry.get("game", DEFAULT_GAME),
        })
    return {"queue": queue_list, "count": len(queue_list)}


@app.post("/api/challenges")
async def create_challenge(req: ChallengeReq, bot: Bot = Depends(get_current_bot)) -> dict[str, Any]:
    game = normalize_game(req.game or bot.game)
    async with state_lock:
        if req.opponent_bot_id not in bots:
            raise HTTPException(status_code=404, detail="opponent bot not found")
        if req.opponent_bot_id == bot.id:
            raise HTTPException(status_code=400, detail="cannot challenge self")
        if bot.game != game or bots[req.opponent_bot_id].game != game:
            raise HTTPException(status_code=400, detail="bot game mismatch")
        ensure_bot_available(bot.id, game)
        ensure_bot_available(req.opponent_bot_id, game)
        opponent = bots[req.opponent_bot_id]
        now = time.time()
        ch = Challenge(
            id=new_id("ch"),
            challenger_bot_id=bot.id,
            opponent_bot_id=req.opponent_bot_id,
            challenger_side=choose_challenger_side(req.side),
            updated_at=now,
            game=game,
        )
        if opponent.challenge_policy == "manual_approve":
            ch.status = "owner_review"
            ch.expires_at = now + max(1, int(opponent.owner_review_timeout_sec or 180))
        elif opponent.challenge_policy == "reject_all":
            ch.status = "rejected"
            ch.owner_decision = "reject"
            ch.owner_decision_reason = "opponent policy reject_all"
        challenges[ch.id] = ch
        save_challenge(ch)
        data = challenge_public(ch)
    if ch.status == "rejected":
        await emit(ch.challenger_bot_id, "challenge_rejected", {"message": "challenge rejected", "challenge": data})
    else:
        await emit(req.opponent_bot_id, "challenge_received", challenge_received_payload(ch))
    return data


@app.get("/api/bots/me/challenges/pending")
async def pending_challenges(bot: Bot = Depends(get_current_bot)) -> dict[str, Any]:
    rows = [
        challenge_public(ch)
        for ch in challenges.values()
        if ch.opponent_bot_id == bot.id and challenge_is_actionable(ch)
    ]
    rows.sort(key=lambda ch: ch.get("created_at") or 0, reverse=True)
    return {"total": len(rows), "challenges": rows}


@app.post("/api/challenges/{challenge_id}/owner_decision")
async def owner_decision(challenge_id: str, req: OwnerDecisionReq, bot: Bot = Depends(get_current_bot)) -> dict[str, Any]:
    decision = (req.decision or "").strip().lower()
    if decision not in {"accept", "reject"}:
        raise HTTPException(status_code=400, detail="decision must be accept or reject")
    async with state_lock:
        ch = challenges.get(challenge_id)
        if not ch:
            raise HTTPException(status_code=404, detail="challenge not found")
        if ch.opponent_bot_id != bot.id:
            raise HTTPException(status_code=403, detail="only challenged bot can decide")
        now = time.time()
        if expire_challenge_if_needed(ch, now):
            raise HTTPException(status_code=400, detail="challenge expired")
        if not challenge_is_actionable(ch):
            raise HTTPException(status_code=400, detail="challenge not actionable")
        if decision == "accept":
            m = accept_challenge_locked(ch, owner_decision="accept", owner_decision_reason=req.reason)
            ch_data = challenge_public(ch)
            m_data = match_public(m)
            response = owner_decision_response(ch, m)
        else:
            ch.status = "rejected"
            ch.owner_decision = "reject"
            ch.owner_decision_reason = req.reason
            ch.updated_at = now
            save_challenge(ch)
            ch_data = challenge_public(ch)
            m = None
            m_data = None
            response = owner_decision_response(ch, None)
    if decision == "accept" and m:
        await emit(ch.challenger_bot_id, "challenge_accepted", {**ch_data, "match": m_data, "match_url": match_url(m)})
        await emit(ch.opponent_bot_id, "challenge_accepted", {**ch_data, "match": m_data, "match_url": match_url(m)})
        await emit_match_started(m)
    else:
        await emit(ch.challenger_bot_id, "challenge_rejected", {"message": "challenge rejected", "challenge": ch_data})
        await emit(ch.challenger_bot_id, "error", {"message": "challenge rejected", "challenge": ch_data})
    return response


@app.post("/api/challenges/{challenge_id}/accept")
async def accept_challenge(challenge_id: str, bot: Bot = Depends(get_current_bot)) -> dict[str, Any]:
    async with state_lock:
        ch = challenges.get(challenge_id)
        if not ch:
            raise HTTPException(status_code=404, detail="challenge not found")
        if ch.opponent_bot_id != bot.id:
            raise HTTPException(status_code=403, detail="only challenged bot can accept")
        now = time.time()
        if expire_challenge_if_needed(ch, now):
            raise HTTPException(status_code=400, detail="challenge expired")
        if not challenge_is_actionable(ch):
            raise HTTPException(status_code=400, detail="challenge not actionable")
        m = accept_challenge_locked(ch)
        ch_data = challenge_public(ch)
        m_data = match_public(m)
    await emit(ch.challenger_bot_id, "challenge_accepted", {**ch_data, "match": m_data, "match_url": match_url(m)})
    await emit(ch.opponent_bot_id, "challenge_accepted", {**ch_data, "match": m_data, "match_url": match_url(m)})
    await emit_match_started(m)
    return {**ch_data, "match": m_data, "match_url": match_url(m)}


@app.post("/api/challenges/{challenge_id}/reject")
async def reject_challenge(challenge_id: str, bot: Bot = Depends(get_current_bot)) -> dict[str, Any]:
    async with state_lock:
        ch = challenges.get(challenge_id)
        if not ch:
            raise HTTPException(status_code=404, detail="challenge not found")
        if ch.opponent_bot_id != bot.id:
            raise HTTPException(status_code=403, detail="only challenged bot can reject")
        now = time.time()
        if expire_challenge_if_needed(ch, now):
            raise HTTPException(status_code=400, detail="challenge expired")
        if not challenge_is_actionable(ch):
            raise HTTPException(status_code=400, detail="challenge not actionable")
        ch.status = "rejected"
        ch.owner_decision = ch.owner_decision or "reject"
        ch.updated_at = time.time()
        save_challenge(ch)
        data = challenge_public(ch)
    await emit(ch.challenger_bot_id, "challenge_rejected", {"message": "challenge rejected", "challenge": data})
    await emit(ch.challenger_bot_id, "error", {"message": "challenge rejected", "challenge": data})
    return data


@app.get("/api/admin/matches")
async def api_admin_matches(game: str | None = None, limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)) -> dict[str, Any]:
    refresh_state_from_db()
    game = normalize_game(game) if game else None
    source = [m for m in visible_matches() if not game or m.game == game]
    ordered = sorted(source, key=lambda m: m.updated_at, reverse=True)
    page = ordered[offset : offset + limit]
    return {"total": len(ordered), "limit": limit, "offset": offset, "matches": [admin_match_summary(m) for m in page]}


@app.get("/api/admin/matches/{match_id}")
async def api_admin_match(match_id: str) -> dict[str, Any]:
    m = matches.get(match_id)
    if not m:
        raise HTTPException(status_code=404, detail="match not found")
    return match_public(m, include_legal_moves=False)


@app.get("/api/admin/bots")
async def api_admin_bots(_: None = Depends(require_admin)) -> dict[str, Any]:
    refresh_state_from_db()
    items = sorted(bots.values(), key=lambda b: (b.online_status != "online", b.name.lower()))
    return {"total": len(items), "bots": [admin_bot_payload(b) for b in items]}


@app.post("/api/admin/bots")
async def api_admin_create_bot(req: AdminBotCreateReq, _: None = Depends(require_admin)) -> dict[str, Any]:
    token = (req.token or secrets.token_urlsafe(32)).strip()
    if not token:
        raise HTTPException(status_code=400, detail="token cannot be empty")
    policy = validate_challenge_policy(req.challenge_policy)
    game = normalize_game(req.game)
    async with state_lock:
        if token in tokens:
            raise HTTPException(status_code=409, detail="token already exists")
        now = time.time()
        bot = Bot(
            id=new_id("bot"), name=req.name, token=token, created_at=now, updated_at=now, game=game,
            avatar_url=req.avatar_url, description=req.description, chess_style=req.chess_style or "random",
            persona_prompt=req.persona_prompt, engine_mode="random", client_type="astrbot",
            instance_name=None, is_public=req.is_public, is_enabled=req.is_enabled,
            challenge_policy=policy, owner_review_timeout_sec=req.owner_review_timeout_sec,
        )
        bots[bot.id] = bot
        tokens[bot.token] = bot.id
        save_bot(bot)
    return {"bot": admin_bot_payload(bot)}


@app.delete("/api/admin/bots/{bot_id}")
async def api_admin_delete_bot(bot_id: str, _: None = Depends(require_admin)) -> dict[str, Any]:
    async with state_lock:
        bot = bots.get(bot_id)
        if not bot:
            raise HTTPException(status_code=404, detail="bot not found")
        token = bot.token
        deleted_matches = [m.id for m in matches.values() if m.red_bot_id == bot_id or m.black_bot_id == bot_id]
        deleted_challenges = [c.id for c in challenges.values() if c.challenger_bot_id == bot_id or c.opponent_bot_id == bot_id]
        for match_id in deleted_matches:
            matches.pop(match_id, None)
            match_sse_queues.pop(match_id, None)
        for challenge_id in deleted_challenges:
            challenges.pop(challenge_id, None)
        match_queue_entries.pop(bot_id, None)
        subscribers.pop(bot_id, None)
        bots.pop(bot_id, None)
        tokens.pop(token, None)
        delete_bot_from_db(bot_id)
        load_state_from_db()
        recompute_rankings_from_visible_matches()
        load_state_from_db()
    return {"deleted": True, "bot_id": bot_id, "deleted_matches": len(deleted_matches), "deleted_challenges": len(deleted_challenges)}


@app.post("/api/admin/matches/backfill_rankings")
async def backfill_rankings() -> dict[str, Any]:
    """Backfill rankings for all finished matches that haven't been scored yet."""
    finished = [m for m in visible_matches() if m.status == "finished" and m.result]
    already = set()
    with db_connect() as conn:
        rows = conn.execute("SELECT DISTINCT match_id FROM rating_history").fetchall()
        already = {r["match_id"] for r in rows}
    updated = 0
    skipped = 0
    for m in finished:
        if m.id in already:
            skipped += 1
            continue
        if m.result == "stopped":
            skipped += 1
            continue
        update_rankings_for_finished_match(m)
        updated += 1
    return {"updated": updated, "skipped": skipped, "total_finished": len(finished)}


@app.get("/", response_class=HTMLResponse)
async def arena_home(request: Request) -> HTMLResponse:
    refresh_state_from_db()
    recent_matches = sorted(visible_matches(), key=lambda m: m.updated_at, reverse=True)[:20]
    return templates.TemplateResponse(request, "arena.html", {"title": "ChessBot Arena 大厅", "recent_matches": recent_matches, "bot_name": bot_name})


@app.get("/arena", response_class=HTMLResponse)
async def arena_page(request: Request) -> HTMLResponse:
    refresh_state_from_db()
    recent_matches = sorted(visible_matches(), key=lambda m: m.updated_at, reverse=True)[:20]
    return templates.TemplateResponse(request, "arena.html", {"title": "ChessBot Arena 大厅", "recent_matches": recent_matches, "bot_name": bot_name})


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "settings.html", {"title": "接入设置"})


@app.get("/admin/bots", response_class=HTMLResponse)
async def admin_bots_page(request: Request) -> HTMLResponse:
    if os.environ.get("CHESS_ARENA_ADMIN_APP") != "1":
        raise HTTPException(status_code=404, detail="not found")
    return templates.TemplateResponse(request, "admin_bots.html", {"title": "后台账号管理"})


@app.get("/matches/{match_id}", response_class=HTMLResponse)
async def match_view_page(request: Request, match_id: str) -> HTMLResponse:
    if match_id not in matches:
        raise HTTPException(status_code=404, detail="match not found")
    return templates.TemplateResponse(request, "match.html", {"match_id": match_id, "title": f"对局 {match_id}"})


@app.get("/admin/matches", response_class=HTMLResponse)
async def admin_matches_page() -> RedirectResponse:
    return RedirectResponse(url="/arena", status_code=307)


@app.get("/admin/matches/{match_id}", response_class=HTMLResponse)
async def admin_match_page(match_id: str) -> RedirectResponse:
    return RedirectResponse(url=f"/matches/{match_id}", status_code=307)


@app.get("/api/matches/{match_id}")
async def get_match(match_id: str, bot: Bot = Depends(get_current_bot)) -> dict[str, Any]:
    m = matches.get(match_id)
    if not m:
        raise HTTPException(status_code=404, detail="match not found")
    if bot.id not in (m.red_bot_id, m.black_bot_id):
        raise HTTPException(status_code=403, detail="not a participant")
    return match_public(m)


@app.post("/api/matches/{match_id}/stop")
async def stop_match(match_id: str, actor: tuple[Bot | None, bool] = Depends(actor_bot_or_admin)) -> dict[str, Any]:
    bot, is_admin = actor
    async with state_lock:
        m = matches.get(match_id)
        if not m:
            raise HTTPException(status_code=404, detail="match not found")
        ensure_match_control_allowed(m, bot, is_admin)
        if m.status != "active":
            return {"match": match_public(m, include_legal_moves=False), "stopped": False, "message": "match already stopped"}
        m.status = "finished"
        m.result = "stopped"
        m.finish_reason = "stopped_by_admin" if is_admin else f"stopped_by_{bot.id}"
        m.finished_at = time.time()
        m.updated_at = m.finished_at
        save_match(m)
        update_rankings_for_finished_match(m)
        data = match_public(m, include_legal_moves=False)
        participants = [m.red_bot_id, m.black_bot_id]
    for pid in participants:
        await emit(pid, "match_finished", data)
    await broadcast_match_sse(match_id)
    return {"match": data, "stopped": True, "admin": is_admin}


@app.post("/api/matches/{match_id}/pause")
async def pause_match(match_id: str, actor: tuple[Bot | None, bool] = Depends(actor_bot_or_admin)) -> dict[str, Any]:
    """Toggle paused state. Participants can control their own matches; admin can control any match."""
    bot, is_admin = actor
    async with state_lock:
        m = matches.get(match_id)
        if not m:
            raise HTTPException(status_code=404, detail="match not found")
        ensure_match_control_allowed(m, bot, is_admin)
        if m.status != "active":
            raise HTTPException(status_code=400, detail="match not active")
        m.paused = not m.paused
        m.updated_at = time.time()
        save_match(m)
        data = match_public(m, include_legal_moves=False)
        should_resume = not m.paused
    await broadcast_match_sse(match_id)
    # After unpausing, tell the bot whose turn it is to resume
    if should_resume:
        await emit_turn(m)
    return {"match": data, "paused": m.paused, "admin": is_admin}


@app.post("/api/admin/matches/stop_all")
async def admin_stop_all(_: None = Depends(require_admin)) -> dict[str, Any]:
    stopped: list[dict[str, Any]] = []
    async with state_lock:
        now = time.time()
        targets = [m for m in matches.values() if m.status == "active"]
        for m in targets:
            m.status = "finished"
            m.result = "stopped"
            m.finish_reason = "stopped_all_by_admin"
            m.finished_at = now
            m.updated_at = now
            save_match(m)
            update_rankings_for_finished_match(m)
            stopped.append(match_public(m, include_legal_moves=False))
    for data in stopped:
        for pid in (data["red_bot_id"], data["black_bot_id"]):
            await emit(pid, "match_finished", data)
        await broadcast_match_sse(data["match_id"])
    return {"stopped": len(stopped), "matches": stopped}


@app.post("/api/analyze")
async def analyze(req: AnalyzeReq, bot_id: str = Depends(x_bot_token)) -> dict[str, Any]:
    """Proxy analyze request to the server-side xqwlight engine."""
    try:
        import aiohttp
    except ImportError as exc:
        raise HTTPException(status_code=503, detail="aiohttp dependency is not installed") from exc

    started = time.perf_counter()
    depth = max(1, min(int(req.depth or 3), 8))
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                "http://127.0.0.1:8789/analyze",
                json={"fen": req.fen, "depth": depth},
            ) as resp:
                text = await resp.text()
                if resp.status >= 400:
                    raise HTTPException(status_code=502, detail=f"engine error: {text[:200]}")
                data = json.loads(text)
                if not isinstance(data, dict):
                    raise HTTPException(status_code=502, detail="engine error: invalid response")
                data.setdefault("engine", "server_xqwlight")
                data.setdefault("depth", depth)
                data["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
                return data
    except aiohttp.ClientError as exc:
        raise HTTPException(status_code=502, detail=f"engine unreachable: {exc}")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"engine error: {exc}")


@app.post("/api/matches/{match_id}/move")
async def make_move(match_id: str, req: MoveReq, bot: Bot = Depends(get_current_bot)) -> dict[str, Any]:
    async with state_lock:
        m = matches.get(match_id)
        if not m:
            raise HTTPException(status_code=404, detail="match not found")
        if m.status != "active":
            raise HTTPException(status_code=400, detail="match not active")
        if m.paused:
            raise HTTPException(status_code=400, detail="match is paused")
        if m.game == "go":
            try:
                state = go9.loads_state(m.fen)
                turn = state["turn"]
                expected_bot = m.black_bot_id if turn == go9.BLACK else m.red_bot_id
                if bot.id != expected_bot:
                    raise HTTPException(status_code=403, detail="not your turn")
                old_state = m.fen
                new_state, move_info = go9.apply_move(m.fen, req.move)
            except go9.GoRuleError as e:
                err = {"message": str(e), "match_id": match_id, "move": req.move}
                await emit(bot.id, "error", err)
                raise HTTPException(status_code=400, detail=str(e))
            m.fen = new_state
            m.ply += 1
            now = time.time()
            if m.ply == 1:
                m.started_at = now
            m.last_move_at = now
            m.updated_at = now
            captured = move_info.get("captured") or []
            move_rec = {
                "move_id": new_id("move"),
                "ply": m.ply,
                "bot_id": bot.id,
                "side": turn,
                "move": move_info.get("move", req.move),
                "comment": req.comment,
                "bot_speech": req.comment,
                "duration_ms": req.duration_ms,
                "fen_before": old_state,
                "fen_after": new_state,
                "state_before": old_state,
                "state_after": new_state,
                "captured": captured,
                "captured_count": len(captured),
                "check": False,
                "created_at": m.updated_at,
                "red_time_left_ms": m.red_time_left_ms,
                "black_time_left_ms": m.black_time_left_ms,
            }
            m.moves.append(move_rec)
            new_state_obj = go9.loads_state(new_state)
            if move_info.get("finished"):
                m.status = "finished"
                m.result = new_state_obj.get("result") or "draw"
                winner = new_state_obj.get("winner")
                m.winner_bot_id = m.black_bot_id if winner == go9.BLACK else (m.red_bot_id if winner == go9.WHITE else None)
                m.finish_reason = new_state_obj.get("finish_reason") or "double_pass"
                m.finished_at = now
            save_match(m)
            if m.status == "finished":
                update_rankings_for_finished_match(m)
            data = {"match": match_public(m), "move": move_rec}
            participants = [m.red_bot_id, m.black_bot_id]
            for pid in participants:
                await emit(pid, "move_made", data)
            if m.status == "finished":
                for pid in participants:
                    await emit(pid, "match_finished", match_public(m))
            else:
                await emit_turn(m)
            await broadcast_match_sse(match_id)
            return data
        else:
            _, turn, _, _ = parse_fen(m.fen)
            expected_bot = m.red_bot_id if turn == RED else m.black_bot_id
            if bot.id != expected_bot:
                raise HTTPException(status_code=403, detail="not your turn")
        # ── Timer: track elapsed time ──
        now = time.time()
        timeout_data = await finish_timeout_match_locked(m, now)
        if timeout_data:
            participants = [m.red_bot_id, m.black_bot_id]
            for pid in participants:
                await emit(pid, "match_finished", timeout_data["match"])
            await broadcast_match_sse(match_id)
            return timeout_data
        if m.ply == 0:
            m.started_at = now
            m.last_move_at = now
        else:
            elapsed_ms = int((now - (m.last_move_at or now)) * 1000)
            if turn == RED:
                m.red_time_left_ms = max(0, m.red_time_left_ms - elapsed_ms)
            else:
                m.black_time_left_ms = max(0, m.black_time_left_ms - elapsed_ms)
            m.last_move_at = now
        try:
            new_fen, captured, finished = apply_ucci(m.fen, req.move)
        except RuleError as e:
            err = {"message": str(e), "match_id": match_id, "move": req.move}
            await emit(bot.id, "error", err)
            raise HTTPException(status_code=400, detail=str(e))
        old_fen = m.fen
        m.fen = new_fen
        m.ply += 1
        m.updated_at = time.time()
        board_after, next_turn, _, _ = parse_fen(new_fen)
        move_rec = {"move_id": new_id("move"), "ply": m.ply, "bot_id": bot.id, "side": turn, "move": req.move, "chinese_notation": ucci_to_chinese(req.move, old_fen), "comment": req.comment, "bot_speech": req.comment, "duration_ms": req.duration_ms, "fen_before": old_fen, "fen_after": new_fen, "captured": captured, "check": is_in_check(board_after, next_turn), "created_at": m.updated_at, "red_time_left_ms": m.red_time_left_ms, "black_time_left_ms": m.black_time_left_ms}
        m.moves.append(move_rec)
        if m.ply >= 200 and not finished:
            m.status = "finished"
            m.result = "draw"
            m.finish_reason = "move_limit"
            m.finished_at = time.time()
        if finished:
            m.status = "finished"
            m.result = f"{turn}_win"
            m.winner_bot_id = bot.id
            m.finish_reason = "capture_general"
            m.finished_at = time.time()
        save_match(m)
        if m.status == "finished":
            update_rankings_for_finished_match(m)
        data = {"match": match_public(m), "move": move_rec}
        participants = [m.red_bot_id, m.black_bot_id]
    for pid in participants:
        await emit(pid, "move_made", data)
    if m.status == "finished":
        for pid in participants:
            await emit(pid, "match_finished", match_public(m))
    else:
        await emit_turn(m)
    await broadcast_match_sse(match_id)
    return data


# ── UCCI to Chinese notation ──
RED_STEP_CN = ["", "一", "二", "三", "四", "五", "六", "七", "八", "九", "十"]
RED_COL_CN = ["九", "八", "七", "六", "五", "四", "三", "二", "一"]
BLK_COL_CN = ["1", "2", "3", "4", "5", "6", "7", "8", "9"]
PIECE_CN = {
    "r": "車", "R": "車", "h": "馬", "H": "馬", "e": "象", "E": "相",
    "a": "士", "A": "仕", "k": "将", "K": "帅", "c": "砲", "C": "炮",
    "p": "卒", "P": "兵",
}


def _step_text(steps: int, side: str) -> str:
    if side == RED:
        return RED_STEP_CN[steps] if 0 <= steps < len(RED_STEP_CN) else str(steps)
    return str(steps)


def ucci_to_chinese(ucci: str, fen_before: str) -> str:
    """Convert UCCI move (e.g. 'b0c2') to Chinese notation like '馬八进七'."""
    if not ucci or len(ucci) < 4:
        return ucci
    from_f = ord(ucci[0]) - 97
    from_rank = int(ucci[1])
    to_f = ord(ucci[2]) - 97
    to_rank = int(ucci[3])
    from_row = 9 - from_rank
    board, turn, _, _ = parse_fen(fen_before)
    piece = board[from_row][from_f] if 0 <= from_row < 10 and 0 <= from_f < 9 else ""
    if not piece:
        return ucci
    piece_name = PIECE_CN.get(piece, "?")
    if turn == RED:
        src_col = RED_COL_CN[from_f]
        advance = to_rank > from_rank
        target_file = RED_COL_CN[to_f]
    else:
        src_col = BLK_COL_CN[from_f]
        advance = to_rank < from_rank
        target_file = BLK_COL_CN[to_f]
    direction = "进" if advance else "退"
    linear = piece.upper() not in ("H", "E", "A")
    if from_f == to_f:
        suffix = _step_text(abs(to_rank - from_rank), turn) if linear else target_file
        return f"{piece_name}{src_col}{direction}{suffix}"
    if linear:
        return f"{piece_name}{src_col}平{target_file}"
    return f"{piece_name}{src_col}{direction}{target_file}"
