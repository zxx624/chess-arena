"""9191-only CardRoom alpha APIs/helpers with SQLite persistence.

This module is deliberately separate from the production two-player Match model.
It wraps the lightweight DouDizhu rule state machine behind a room-shaped public
contract that can later grow into CardRoom/CardSeat/CardAction services.

9191 alpha persistence notes:
- SQLite is the source of truth.
- ``card_rooms`` remains a small process-local cache only.
- No production Match/red-black coupling and no AstrBot/chess_arena tool coupling.
"""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .games import doudizhu

SUPPORTED_CARD_GAMES = {"doudizhu"}
DEFAULT_SEATS = ["seat0", "seat1", "seat2"]
DEFAULT_DB_PATH = "/mnt/cosmem/gulu1-1415708756/chess-arena/chess_arena.db"
POOL_SLOT_COUNT = 5
POOL_SEAT_CAPACITY = 3
POOL_STATUS_WAITING = "waiting"
POOL_STATUS_PLAYING = "playing"
POOL_STATUS_FINISHED = "finished"



@dataclass
class CardRoom:
    id: str
    game: str
    raw_state: str
    status: str = "active"
    seats: list[str] = field(default_factory=lambda: list(DEFAULT_SEATS))
    created_at: float = 0.0
    updated_at: float = 0.0
    seat_tokens: dict[str, str] = field(default_factory=dict)


# 9191 alpha: cache only. SQLite is the source of truth.
card_rooms: dict[str, CardRoom] = {}


def new_room_id() -> str:
    return "cardroom_" + secrets.token_urlsafe(10).replace("-", "_")


def db_path() -> Path:
    return Path(os.environ.get("CHESS_ARENA_DB", DEFAULT_DB_PATH))


def db_connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def init_card_room_tables(conn: sqlite3.Connection) -> None:
    """Create 9191 CardRoom alpha tables without touching match/challenge data."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS card_rooms (
            id TEXT PRIMARY KEY,
            game TEXT NOT NULL,
            status TEXT NOT NULL,
            state_json TEXT NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            seat_tokens_json TEXT DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_card_rooms_updated_at ON card_rooms(updated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_card_rooms_game_status ON card_rooms(game, status);

        CREATE TABLE IF NOT EXISTS card_room_actions (
            id TEXT PRIMARY KEY,
            room_id TEXT NOT NULL,
            action_index INTEGER NOT NULL,
            actor TEXT,
            action TEXT,
            cards_json TEXT,
            move_json TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_card_room_actions_room_idx ON card_room_actions(room_id, action_index);
        CREATE INDEX IF NOT EXISTS idx_card_room_actions_room ON card_room_actions(room_id);

        CREATE TABLE IF NOT EXISTS card_room_pool_slots (
            slot INTEGER PRIMARY KEY,
            status TEXT NOT NULL DEFAULT 'waiting',
            room_id TEXT,
            seats_json TEXT NOT NULL DEFAULT '[]',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_card_room_pool_room ON card_room_pool_slots(room_id);
        """
    )
    _ensure_column(conn, "card_rooms", "seat_tokens_json", "TEXT DEFAULT '{}'")
    _ensure_pool_slots(conn)


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _ensure_pool_slots(conn: sqlite3.Connection) -> None:
    now = time.time()
    for slot in range(1, POOL_SLOT_COUNT + 1):
        conn.execute(
            """
            INSERT OR IGNORE INTO card_room_pool_slots(slot, status, room_id, seats_json, created_at, updated_at)
            VALUES (?, ?, NULL, '[]', ?, ?)
            """,
            (slot, POOL_STATUS_WAITING, now, now),
        )


def init_db() -> None:
    with db_connect() as conn:
        init_card_room_tables(conn)


def _normalise_game(game: str | None) -> str:
    value = (game or "doudizhu").strip().lower()
    if value not in SUPPORTED_CARD_GAMES:
        raise doudizhu.DouDizhuRuleError(f"unsupported card game: {value}")
    return value


def _status_from_raw(raw_state: str, fallback: str = "active") -> str:
    try:
        state = doudizhu.loads_state(raw_state)
    except Exception:
        return fallback
    return "finished" if state.get("phase") == "finished" else fallback or "active"


def _room_from_row(row: sqlite3.Row) -> CardRoom:
    state = doudizhu.loads_state(row["state_json"])
    seats = list(state.get("players") or DEFAULT_SEATS)
    try:
        token_raw = row["seat_tokens_json"]
    except (KeyError, IndexError):
        token_raw = "{}"
    try:
        seat_tokens = json.loads(token_raw or "{}")
    except json.JSONDecodeError:
        seat_tokens = {}
    room = CardRoom(
        id=row["id"],
        game=row["game"],
        raw_state=row["state_json"],
        status=row["status"] or _status_from_raw(row["state_json"]),
        seats=seats,
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
        seat_tokens={str(k): str(v) for k, v in dict(seat_tokens).items()},
    )
    card_rooms[room.id] = room
    return room


def _load_room_from_db(room_id: str) -> CardRoom | None:
    init_db()
    with db_connect() as conn:
        row = conn.execute("SELECT * FROM card_rooms WHERE id = ?", (room_id,)).fetchone()
    if not row:
        return None
    return _room_from_row(row)


def _room_or_error(room_id: str) -> CardRoom:
    # DB is the final source of truth. Refresh cache from DB on every public read/mutation.
    room = _load_room_from_db(room_id)
    if not room:
        raise KeyError(room_id)
    return room


def _upsert_room(conn: sqlite3.Connection, room: CardRoom) -> None:
    status = _status_from_raw(room.raw_state, room.status)
    now = time.time()
    if not room.created_at:
        room.created_at = now
    room.updated_at = now
    room.status = status
    conn.execute(
        """
        INSERT INTO card_rooms(id, game, status, state_json, created_at, updated_at, seat_tokens_json)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            game=excluded.game,
            status=excluded.status,
            state_json=excluded.state_json,
            updated_at=excluded.updated_at,
            seat_tokens_json=excluded.seat_tokens_json
        """,
        (
            room.id,
            room.game,
            room.status,
            room.raw_state,
            room.created_at,
            room.updated_at,
            json.dumps(room.seat_tokens or {}, ensure_ascii=False, separators=(",", ":")),
        ),
    )
    card_rooms[room.id] = room


def _next_action_index(conn: sqlite3.Connection, room_id: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(action_index), 0) AS max_idx FROM card_room_actions WHERE room_id = ?",
        (room_id,),
    ).fetchone()
    return int(row["max_idx"] or 0) + 1


def _record_move(conn: sqlite3.Connection, room_id: str, move: dict[str, Any], action_index: int | None = None) -> int:
    idx = action_index or _next_action_index(conn, room_id)
    action = move.get("action")
    cards = move.get("cards")
    conn.execute(
        """
        INSERT INTO card_room_actions(id, room_id, action_index, actor, action, cards_json, move_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "cardaction_" + secrets.token_urlsafe(10).replace("-", "_"),
            room_id,
            idx,
            move.get("player"),
            action,
            json.dumps(cards or [], ensure_ascii=False),
            json.dumps(move, ensure_ascii=False, separators=(",", ":")),
            time.time(),
        ),
    )
    return idx


def _public_state(room: CardRoom) -> dict[str, Any]:
    state = doudizhu.loads_state(room.raw_state)
    players = list(state.get("players") or [])
    hands = state.get("hands") or {}
    landlord = state.get("landlord")
    last_play = state.get("last_play")
    history = list(state.get("history") or [])
    phase = state.get("phase") or room.status
    winner = state.get("winner")

    seats: list[dict[str, Any]] = []
    hands_count: dict[str, int] = {}
    for idx, seat in enumerate(players):
        hand_count = len(hands.get(seat, []))
        hands_count[seat] = hand_count
        seats.append(
            {
                "id": seat,
                "index": idx,
                "role": "landlord" if seat == landlord else "farmer",
                "hand_count": hand_count,
                "is_landlord": seat == landlord,
            }
        )

    status = "finished" if phase == "finished" else room.status
    return {
        "game": room.game,
        "room_id": room.id,
        "status": status,
        "phase": phase,
        "seats": seats,
        "players": players,
        "landlord_seat": landlord,
        "current_seat": state.get("turn_player"),
        "turn_index": state.get("turn_index"),
        "hands_count": hands_count,
        "bottom_cards": list(state.get("bottom") or []),
        "last_play": last_play,
        "pass_count": int(state.get("passes") or 0),
        "passes": int(state.get("passes") or 0),
        "action_history": history,
        "history": history,
        "winner": winner,
    }



class CardRoomAccessError(PermissionError):
    """Raised when a seat token does not match the requested private seat."""

    def __init__(self, code: str, message: str, seat: int | str | None = None) -> None:
        super().__init__(message)
        self.detail = {"code": code, "message": message, "seat": seat}


class CardRoomActionError(ValueError):
    """Structured illegal action error for LLM/manual CardRoom actions."""

    def __init__(self, code: str, message: str, legal_hint: str, attempt: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.detail = {
            "code": code,
            "message": message,
            "legal_hint": legal_hint,
            "attempt": attempt or {},
        }


class CardRoomPoolError(ValueError):
    """Structured error for the fixed five-slot DouDizhu room pool."""

    def __init__(self, code: str, message: str, slot: int | None = None) -> None:
        super().__init__(message)
        self.detail = {"code": code, "message": message, "slot": slot}


def _seat_id(state: dict[str, Any], seat: int | str) -> str:
    players = list(state.get("players") or [])
    if isinstance(seat, int) or (isinstance(seat, str) and seat.strip().isdigit()):
        idx = int(seat)
        if not 0 <= idx < len(players):
            raise CardRoomActionError("invalid_seat", f"invalid seat: {seat}", "seat 必须是 0/1/2 或有效 seat id", {"seat": seat})
        return players[idx]
    value = str(seat).strip()
    if value not in players:
        raise CardRoomActionError("invalid_seat", f"invalid seat: {seat}", "seat 必须是 0/1/2 或有效 seat id", {"seat": seat})
    return value


def _seat_index(state: dict[str, Any], seat_id: str) -> int:
    players = list(state.get("players") or [])
    return players.index(seat_id)


def _normalise_token(value: str | None) -> str:
    return str(value or "").strip()


def _check_seat_token(room: CardRoom, state: dict[str, Any], seat_id: str, token: str | None = None) -> None:
    token = _normalise_token(token)
    tokens = room.seat_tokens or {}
    expected = str(tokens.get(seat_id) or "")
    admin_token = _normalise_token(os.environ.get("CARDROOM_ADMIN_TOKEN"))
    require_token = os.environ.get("CARDROOM_REQUIRE_SEAT_TOKEN", "").strip().lower() in {"1", "true", "yes", "on"}
    if token and admin_token and secrets.compare_digest(token, admin_token):
        return
    if token:
        if expected and secrets.compare_digest(token, expected):
            return
        raise CardRoomAccessError("invalid_seat_token", "seat token does not match requested seat", _seat_index(state, seat_id))
    if require_token and expected:
        raise CardRoomAccessError("missing_seat_token", "seat token is required for this private seat", _seat_index(state, seat_id))


def _last_play_target(state: dict[str, Any], seat_id: str | None = None) -> dict[str, Any] | None:
    last_play = state.get("last_play")
    if not last_play or last_play.get("action") != "play":
        return None
    if seat_id and last_play.get("player") == seat_id:
        return None
    pattern = last_play.get("pattern")
    if not pattern:
        pattern = doudizhu.classify_cards(list(last_play.get("cards") or []))
        last_play["pattern"] = pattern
    return pattern


def _action_hint(state: dict[str, Any], seat_id: str) -> str:
    target = _last_play_target(state, seat_id)
    if not target:
        return "新一轮出牌，可以出任意支持牌型；不能 pass。"
    t = target.get("type")
    rank = target.get("rank")
    if t == "single":
        return f"需要出大于 rank {rank} 的单张，或炸弹/王炸，或 pass。"
    if t == "pair":
        return f"需要出大于 rank {rank} 的对子，或炸弹/王炸，或 pass。"
    if t == "triple":
        return f"需要出大于 rank {rank} 的三张，或炸弹/王炸，或 pass。"
    if t == "triple_with_single":
        return f"需要出更大的三带一，或炸弹/王炸，或 pass。"
    if t == "triple_with_pair":
        return f"需要出更大的三带二，或炸弹/王炸，或 pass。"
    if t == "straight":
        return f"需要出同长度且更大的顺子，或炸弹/王炸，或 pass。"
    if t == "pair_straight":
        return f"需要出同长度且更大的连对，或炸弹/王炸，或 pass。"
    if t == "bomb":
        return f"需要出更大的炸弹或王炸，或 pass。"
    if t == "rocket":
        return "王炸无法被压过，只能 pass。"
    return "需要按上一手牌型压过，或炸弹/王炸，或 pass。"


def _candidate_groups_for_state(state: dict[str, Any], seat_id: str) -> dict[str, Any]:
    hand = list((state.get("hands") or {}).get(seat_id, []))
    target = _last_play_target(state, seat_id)
    grouped: dict[str, Any] = {
        "singles": [],
        "pairs": [],
        "triples": [],
        "triple_with_single": [],
        "triple_with_pair": [],
        "straights": [],
        "consecutive_pairs": [],
        "bombs": [],
        "rocket": False,
    }
    for cards in doudizhu.candidate_plays(hand):
        pattern = doudizhu.classify_cards(cards)
        if not doudizhu.can_beat(pattern, target):
            continue
        ptype = pattern["type"]
        if ptype == "single":
            grouped["singles"].append(cards[0])
        elif ptype == "pair":
            grouped["pairs"].append(cards)
        elif ptype == "triple":
            grouped["triples"].append(cards)
        elif ptype == "triple_with_single":
            grouped["triple_with_single"].append(cards)
        elif ptype == "triple_with_pair":
            grouped["triple_with_pair"].append(cards)
        elif ptype == "straight":
            grouped["straights"].append(cards)
        elif ptype == "pair_straight":
            grouped["consecutive_pairs"].append(cards)
        elif ptype == "bomb":
            grouped["bombs"].append(cards)
        elif ptype == "rocket":
            grouped["rocket"] = True
            grouped["rocket_cards"] = cards
    return grouped


def _legal_actions_from_state(room: CardRoom, state: dict[str, Any], seat_id: str) -> dict[str, Any]:
    current = doudizhu.current_player(state) if state.get("phase") == "playing" else None
    target = _last_play_target(state, seat_id)
    last_play = state.get("last_play") if target else None
    can_pass = bool(target and last_play and last_play.get("player") != seat_id and current == seat_id)
    return {
        "room_id": room.id,
        "game": room.game,
        "seat": _seat_index(state, seat_id),
        "seat_id": seat_id,
        "is_my_turn": current == seat_id,
        "can_pass": can_pass,
        "last_play": last_play,
        "legal_hint": _action_hint(state, seat_id),
        "candidate_groups": _candidate_groups_for_state(state, seat_id) if current == seat_id else {
            "singles": [], "pairs": [], "triples": [], "triple_with_single": [], "triple_with_pair": [],
            "straights": [], "consecutive_pairs": [], "bombs": [], "rocket": False,
        },
    }


def legal_actions(room_id: str, seat: int | str = 0, token: str | None = None) -> dict[str, Any]:
    room = _room_or_error(room_id)
    state = doudizhu.loads_state(room.raw_state)
    seat_id = _seat_id(state, seat)
    _check_seat_token(room, state, seat_id, token)
    return _legal_actions_from_state(room, state, seat_id)


def spectator_view(room_id: str) -> dict[str, Any]:
    """Full human spectator view for the 9191 CardRoom demo.

    This deliberately exposes all three hands for visitors while room_view()
    remains the private LLM/AI seat view.
    """
    room = _room_or_error(room_id)
    state = doudizhu.loads_state(room.raw_state)
    players = list(state.get("players") or [])
    hands = state.get("hands") or {}
    landlord = state.get("landlord")
    current = state.get("turn_player")
    history = list(state.get("history") or [])
    phase = state.get("phase") or room.status
    return {
        "room_id": room.id,
        "game": room.game,
        "status": "finished" if phase == "finished" else room.status,
        "phase": phase,
        "current_player": current,
        "current_seat": current,
        "current_seat_index": _seat_index(state, current) if current in players else None,
        "players": [
            {
                "seat": idx,
                "seat_id": player,
                "role": "landlord" if player == landlord else "farmer",
                "is_landlord": player == landlord,
                "is_current": player == current,
                "hand": list(hands.get(player, [])),
                "hand_count": len(hands.get(player, [])),
            }
            for idx, player in enumerate(players)
        ],
        "bottom_cards": list(state.get("bottom") or []),
        "last_play": state.get("last_play"),
        "pass_count": int(state.get("passes") or 0),
        "history": history,
        "recent_history": history[-20:],
        "winner": state.get("winner"),
    }


def room_view(room_id: str, seat: int | str = 0, token: str | None = None) -> dict[str, Any]:
    room = _room_or_error(room_id)
    state = doudizhu.loads_state(room.raw_state)
    seat_id = _seat_id(state, seat)
    _check_seat_token(room, state, seat_id, token)
    players = list(state.get("players") or [])
    hands = state.get("hands") or {}
    landlord = state.get("landlord")
    current = state.get("turn_player")
    history = list(state.get("history") or [])
    legal = _legal_actions_from_state(room, state, seat_id)
    return {
        "room_id": room.id,
        "game": room.game,
        "status": "finished" if state.get("phase") == "finished" else room.status,
        "phase": state.get("phase"),
        "my_seat": _seat_index(state, seat_id),
        "my_seat_id": seat_id,
        "my_role": "landlord" if seat_id == landlord else "farmer",
        "current_seat": current,
        "current_seat_index": _seat_index(state, current) if current in players else None,
        "is_my_turn": current == seat_id,
        "my_hand": list(hands.get(seat_id, [])),
        "players": [
            {
                "seat": idx,
                "seat_id": player,
                "role": "landlord" if player == landlord else "farmer",
                "hand_count": len(hands.get(player, [])),
                "is_landlord": player == landlord,
                "is_me": player == seat_id,
            }
            for idx, player in enumerate(players)
        ],
        "bottom_cards": list(state.get("bottom") or []),
        "last_play": state.get("last_play"),
        "pass_count": int(state.get("passes") or 0),
        "recent_history": history[-20:],
        "winner": state.get("winner"),
        "legal_summary": {
            "can_pass": legal["can_pass"],
            "expected": legal["legal_hint"],
            "last_play": legal["last_play"],
            "valid_families": [key for key, value in legal["candidate_groups"].items() if value and key != "rocket_cards"],
            "hint": legal["legal_hint"],
        },
    }


def _cards_action(cards: list[str]) -> str:
    return "play:" + ",".join(str(card).strip() for card in cards if str(card).strip())


def _structured_action_error(exc: Exception, attempt: dict[str, Any]) -> CardRoomActionError:
    msg = str(exc)
    if "not your turn" in msg:
        code, hint = "not_your_turn", "只能由 current_seat 对应的玩家出牌。"
    elif "card not in hand" in msg:
        code, hint = "card_not_in_hand", "cards 必须全部来自该 seat 的 my_hand。"
    elif "invalid card" in msg:
        code, hint = "invalid_card", "牌面编码必须使用 3S/3H/.../BJ/RJ 这套格式，且必须是有效牌。"
    elif "unsupported card pattern" in msg:
        code, hint = "unsupported_pattern", "当前只支持单张、对子、三张、三带一、三带二、顺子、连对、炸弹、王炸。"
    elif "does not beat" in msg:
        code, hint = "cannot_beat_last_play", "需要按上一手牌型出更大的牌，或出炸弹/王炸，或者 pass。"
    elif "cannot pass" in msg:
        code, hint = "cannot_pass", "新一轮必须出牌；不能 pass 自己上一手。"
    elif "room is not playing" in msg:
        code, hint = "room_not_playing", "房间已结束或不在 playing 阶段。"
    else:
        code, hint = "illegal_action", "请重新读取 view 和 legal-actions 后再选择动作。"
    return CardRoomActionError(code, msg, hint, attempt)


def apply_room_action(
    room_id: str,
    *,
    seat: int | str,
    action: str,
    cards: list[str] | None = None,
    source: str | None = None,
    reason: str | None = None,
    speech: str | None = None,
    retries: int | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    room = _room_or_error(room_id)
    state = doudizhu.loads_state(room.raw_state)
    seat_id = _seat_id(state, seat)
    _check_seat_token(room, state, seat_id, token)
    action_value = (action or "").strip().lower()
    cards = list(cards or [])
    attempt = {"seat": seat, "seat_id": seat_id, "action": action_value, "cards": cards, "source": source, "reason": reason}
    if action_value == "play":
        if not cards:
            raise CardRoomActionError("missing_cards", "play action needs cards", "action=play 时 cards 不能为空。", attempt)
        try:
            engine_action = _cards_action(cards)
        except doudizhu.DouDizhuRuleError as exc:
            raise _structured_action_error(exc, attempt) from exc
    elif action_value == "pass":
        if cards:
            raise CardRoomActionError("pass_with_cards", "pass action cards must be empty", "action=pass 时 cards 必须为空数组。", attempt)
        engine_action = "pass"
    else:
        raise CardRoomActionError("unknown_action", f"unknown action: {action}", "action 只能是 play 或 pass。", attempt)

    try:
        raw, move = doudizhu.apply_action(room.raw_state, seat_id, engine_action)
    except doudizhu.DouDizhuRuleError as exc:
        raise _structured_action_error(exc, attempt) from exc
    move["source"] = source or "manual"
    if reason:
        move["reason"] = reason
    if speech:
        move["speech"] = str(speech)[:300]
    if retries is not None:
        move["retries"] = max(0, int(retries))
    updated_state = doudizhu.loads_state(raw)
    history = updated_state.setdefault("history", [])
    if history:
        history[-1].update({"source": move["source"]})
        if reason:
            history[-1]["reason"] = reason
        if speech:
            history[-1]["speech"] = str(speech)[:300]
        if retries is not None:
            history[-1]["retries"] = max(0, int(retries))
        raw = doudizhu.dumps_state(updated_state)
    room.raw_state = raw
    with db_connect() as conn:
        _upsert_room(conn, room)
        _record_move(conn, room.id, move)
    out = public_room(room)
    state_out = out["state"]
    out.update({"move": move, "finished": state_out.get("phase") == "finished", "winner": state_out.get("winner")})
    return out

def _room_summary(room: CardRoom) -> dict[str, Any]:
    state = doudizhu.loads_state(room.raw_state)
    players = list(state.get("players") or [])
    landlord = state.get("landlord")
    current = state.get("turn_player")
    phase = state.get("phase") or room.status
    return {
        "room_id": room.id,
        "game": room.game,
        "status": "finished" if phase == "finished" else room.status,
        "phase": phase,
        "landlord_seat": landlord,
        "landlord_seat_index": _seat_index(state, landlord) if landlord in players else None,
        "current_seat": current,
        "current_seat_index": _seat_index(state, current) if current in players else None,
        "winner": state.get("winner"),
        "created_at": room.created_at,
        "updated_at": room.updated_at,
    }


def list_rooms(*, limit: int = 50, offset: int = 0, game: str | None = None) -> dict[str, Any]:
    """List recent CardRoom alpha rooms from SQLite, not the process cache."""
    init_db()
    limit = max(1, min(int(limit or 50), 100))
    offset = max(0, int(offset or 0))
    params: list[Any] = []
    where = ""
    if game:
        where = " WHERE game = ?"
        params.append(_normalise_game(game))
    with db_connect() as conn:
        total = int(conn.execute(f"SELECT COUNT(*) FROM card_rooms{where}", params).fetchone()[0])
        rows = conn.execute(
            f"SELECT * FROM card_rooms{where} ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()
    rooms = [_room_summary(_room_from_row(row)) for row in rows]
    return {"rooms": rooms, "total": total, "limit": limit, "offset": offset}


def public_room(room: CardRoom, *, include_seat_tokens: bool = False) -> dict[str, Any]:
    payload = {"room_id": room.id, "game": room.game, "state": _public_state(room)}
    if include_seat_tokens:
        payload["seat_tokens"] = dict(room.seat_tokens or {})
    return payload


def _create_room_locked(
    conn: sqlite3.Connection,
    *,
    game: str | None = "doudizhu",
    players: list[str] | None = None,
    seed: int | None = None,
    landlord_index: int = 0,
    room_id: str | None = None,
    seat_tokens: dict[str, str] | None = None,
) -> CardRoom:
    game = _normalise_game(game)
    seats = list(players or DEFAULT_SEATS)
    if game != "doudizhu":
        raise doudizhu.DouDizhuRuleError(f"unsupported card game: {game}")
    state = doudizhu.new_state(seats, seed=seed, landlord_index=landlord_index)
    now = time.time()
    tokens = dict(seat_tokens or {})
    for seat in seats:
        tokens.setdefault(seat, "seat_" + secrets.token_urlsafe(18).replace("-", "_"))
    room = CardRoom(
        id=room_id or new_room_id(),
        game=game,
        raw_state=doudizhu.dumps_state(state),
        seats=seats,
        created_at=now,
        updated_at=now,
        seat_tokens=tokens,
    )
    _upsert_room(conn, room)
    return room


def create_room(
    *,
    game: str | None = "doudizhu",
    players: list[str] | None = None,
    seed: int | None = None,
    landlord_index: int = 0,
    seat_tokens: dict[str, str] | None = None,
) -> dict[str, Any]:
    init_db()
    with db_connect() as conn:
        room = _create_room_locked(
            conn,
            game=game,
            players=players,
            seed=seed,
            landlord_index=landlord_index,
            seat_tokens=seat_tokens,
        )
    return public_room(room, include_seat_tokens=True)


def _pool_validate_slot(slot: int | str) -> int:
    try:
        value = int(slot)
    except (TypeError, ValueError) as exc:
        raise CardRoomPoolError("invalid_slot", "slot must be an integer from 1 to 5") from exc
    if not 1 <= value <= POOL_SLOT_COUNT:
        raise CardRoomPoolError("invalid_slot", "slot must be between 1 and 5", value)
    return value


def _pool_parse_seats(raw: str | None) -> list[dict[str, Any]]:
    try:
        value = json.loads(raw or "[]")
    except json.JSONDecodeError:
        value = []
    if not isinstance(value, list):
        return []
    seats: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            seats.append(dict(item))
    return seats


def _pool_seats_json(seats: list[dict[str, Any]]) -> str:
    return json.dumps(seats, ensure_ascii=False, separators=(",", ":"))


def _pool_load_row(conn: sqlite3.Connection, slot: int) -> sqlite3.Row:
    _ensure_pool_slots(conn)
    row = conn.execute("SELECT * FROM card_room_pool_slots WHERE slot = ?", (slot,)).fetchone()
    if not row:
        raise CardRoomPoolError("slot_not_found", "pool slot not found", slot)
    return row


def _pool_clean_identity(controller_type: str | None, controller_id: str | None, display_name: str | None) -> tuple[str, str, str]:
    ctype = str(controller_type or "web").strip().lower()[:40] or "web"
    cid = str(controller_id or "").strip()[:160]
    if not cid:
        raise CardRoomPoolError("missing_controller_id", "controller_id is required")
    name = str(display_name or cid).strip()[:80] or cid
    return ctype, cid, name


def _pool_public_seat(seat: dict[str, Any], *, include_token: bool = False) -> dict[str, Any]:
    out = {
        "seat": int(seat.get("seat") or 0),
        "seat_id": str(seat.get("seat_id") or f"seat{int(seat.get('seat') or 0)}"),
        "controller_type": str(seat.get("controller_type") or ""),
        "controller_id": str(seat.get("controller_id") or ""),
        "display_name": str(seat.get("display_name") or ""),
        "status": str(seat.get("status") or "joined"),
        "joined_at": float(seat.get("joined_at") or 0),
    }
    if include_token:
        out["token"] = str(seat.get("token") or "")
    return out


def _pool_slot_public(row: sqlite3.Row, *, include_tokens: bool = False, include_room: bool = True) -> dict[str, Any]:
    seats = _pool_parse_seats(row["seats_json"])
    status = str(row["status"] or POOL_STATUS_WAITING)
    room_id = row["room_id"]
    room_summary = None
    if room_id and include_room:
        try:
            room_summary = _room_summary(_room_or_error(str(room_id)))
            if room_summary.get("status") == "finished":
                status = POOL_STATUS_FINISHED
        except Exception:
            room_summary = None
    return {
        "slot": int(row["slot"]),
        "status": status,
        "room_id": room_id,
        "room_url": f"/doudizhu?room_id={room_id}" if room_id else None,
        "capacity": POOL_SEAT_CAPACITY,
        "occupied": len([seat for seat in seats if seat.get("status") != "left"]),
        "can_join": status == POOL_STATUS_WAITING and len(seats) < POOL_SEAT_CAPACITY,
        "can_start": status == POOL_STATUS_WAITING and len(seats) == POOL_SEAT_CAPACITY,
        "seats": [_pool_public_seat(seat, include_token=include_tokens) for seat in seats],
        "room": room_summary,
        "created_at": float(row["created_at"]),
        "updated_at": float(row["updated_at"]),
    }


def _pool_write_slot(
    conn: sqlite3.Connection,
    *,
    slot: int,
    status: str,
    room_id: str | None,
    seats: list[dict[str, Any]],
) -> sqlite3.Row:
    now = time.time()
    conn.execute(
        """
        UPDATE card_room_pool_slots
        SET status = ?, room_id = ?, seats_json = ?, updated_at = ?
        WHERE slot = ?
        """,
        (status, room_id, _pool_seats_json(seats), now, slot),
    )
    return _pool_load_row(conn, slot)


def _pool_reindex_waiting_seats(seats: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for idx, seat in enumerate(seat for seat in seats if seat.get("status") != "left"):
        next_seat = dict(seat)
        next_seat["seat"] = idx
        next_seat["seat_id"] = f"seat{idx}"
        out.append(next_seat)
    return out


def _pool_start_locked(conn: sqlite3.Connection, slot: int, seats: list[dict[str, Any]], *, seed: int | None = None) -> tuple[sqlite3.Row, CardRoom]:
    if len(seats) != POOL_SEAT_CAPACITY:
        raise CardRoomPoolError("not_enough_players", "pool slot needs exactly 3 joined seats before start", slot)
    landlord_index = secrets.randbelow(POOL_SEAT_CAPACITY)
    room_seed = seed if seed is not None else secrets.randbits(31)
    seat_tokens = {str(seat.get("seat_id") or f"seat{idx}"): str(seat.get("token") or "") for idx, seat in enumerate(seats)}
    # Build the room from the explicit room_seed.  This avoids any accidental
    # entropy wait in the auto-start join request while preserving fair shuffling.
    state = doudizhu.new_state(list(DEFAULT_SEATS), seed=room_seed, landlord_index=landlord_index)
    now = time.time()
    tokens = dict(seat_tokens)
    for seat_id in DEFAULT_SEATS:
        tokens.setdefault(seat_id, "seat_" + secrets.token_urlsafe(18).replace("-", "_"))
    room = CardRoom(
        id=new_room_id(),
        game="doudizhu",
        raw_state=doudizhu.dumps_state(state),
        seats=list(DEFAULT_SEATS),
        created_at=now,
        updated_at=now,
        seat_tokens=tokens,
    )
    _upsert_room(conn, room)
    started = []
    for idx, seat in enumerate(seats):
        next_seat = dict(seat)
        next_seat["seat"] = idx
        next_seat["seat_id"] = f"seat{idx}"
        next_seat["status"] = "playing"
        next_seat["room_id"] = room.id
        started.append(next_seat)
    row = _pool_write_slot(conn, slot=slot, status=POOL_STATUS_PLAYING, room_id=room.id, seats=started)
    return row, room


def list_pool_slots() -> dict[str, Any]:
    init_db()
    with db_connect() as conn:
        rows = conn.execute("SELECT * FROM card_room_pool_slots ORDER BY slot ASC").fetchall()
    return {
        "slot_count": POOL_SLOT_COUNT,
        "capacity": POOL_SEAT_CAPACITY,
        "slots": [_pool_slot_public(row) for row in rows],
    }


def join_pool_slot(
    slot: int | str,
    *,
    controller_type: str | None = "web",
    controller_id: str | None,
    display_name: str | None = None,
) -> dict[str, Any]:
    slot = _pool_validate_slot(slot)
    ctype, cid, name = _pool_clean_identity(controller_type, controller_id, display_name)
    init_db()
    with db_connect() as conn:
        row = _pool_load_row(conn, slot)
        seats = _pool_parse_seats(row["seats_json"])
        existing = next((seat for seat in seats if seat.get("controller_type") == ctype and seat.get("controller_id") == cid), None)
        if existing:
            public = _pool_slot_public(row)
            return {
                "joined": False,
                "already_joined": True,
                "auto_started": False,
                "slot": public,
                "seat": _pool_public_seat(existing, include_token=True),
                "seat_token": str(existing.get("token") or ""),
                "room_id": row["room_id"],
            }
        if row["status"] != POOL_STATUS_WAITING:
            raise CardRoomPoolError("slot_not_waiting", "pool slot is already playing; reset it before joining", slot)
        if len(seats) >= POOL_SEAT_CAPACITY:
            raise CardRoomPoolError("slot_full", "pool slot is full", slot)
        seat_idx = len(seats)
        seat = {
            "seat": seat_idx,
            "seat_id": f"seat{seat_idx}",
            "controller_type": ctype,
            "controller_id": cid,
            "display_name": name,
            "status": "joined",
            "joined_at": time.time(),
            "token": "seat_" + secrets.token_urlsafe(18).replace("-", "_"),
        }
        seats.append(seat)
        auto_started = len(seats) == POOL_SEAT_CAPACITY
        room_payload = None
        if auto_started:
            row, room = _pool_start_locked(conn, slot, seats)
            room_payload = public_room(room)
            seat = next((item for item in _pool_parse_seats(row["seats_json"]) if item.get("controller_type") == ctype and item.get("controller_id") == cid), seat)
        else:
            row = _pool_write_slot(conn, slot=slot, status=POOL_STATUS_WAITING, room_id=None, seats=seats)
        return {
            "joined": True,
            "already_joined": False,
            "auto_started": auto_started,
            "slot": _pool_slot_public(row, include_room=False),
            "seat": _pool_public_seat(seat, include_token=True),
            "seat_token": str(seat.get("token") or ""),
            "room_id": row["room_id"],
            "room": room_payload,
        }


def leave_pool_slot(slot: int | str, *, controller_type: str | None = "web", controller_id: str | None) -> dict[str, Any]:
    slot = _pool_validate_slot(slot)
    ctype, cid, _name = _pool_clean_identity(controller_type, controller_id, controller_id)
    init_db()
    with db_connect() as conn:
        row = _pool_load_row(conn, slot)
        seats = _pool_parse_seats(row["seats_json"])
        idx = next((i for i, seat in enumerate(seats) if seat.get("controller_type") == ctype and seat.get("controller_id") == cid), -1)
        if idx < 0:
            return {"left": False, "slot": _pool_slot_public(row), "room_id": row["room_id"]}
        if row["status"] == POOL_STATUS_WAITING:
            seats.pop(idx)
            seats = _pool_reindex_waiting_seats(seats)
            row = _pool_write_slot(conn, slot=slot, status=POOL_STATUS_WAITING, room_id=None, seats=seats)
        else:
            seats[idx] = dict(seats[idx])
            seats[idx]["status"] = "left"
            seats[idx]["left_at"] = time.time()
            row = _pool_write_slot(conn, slot=slot, status=row["status"], room_id=row["room_id"], seats=seats)
        return {"left": True, "slot": _pool_slot_public(row, include_room=False), "room_id": row["room_id"]}


def reset_pool_slot(slot: int | str) -> dict[str, Any]:
    slot = _pool_validate_slot(slot)
    init_db()
    with db_connect() as conn:
        row = _pool_load_row(conn, slot)
        row = _pool_write_slot(conn, slot=slot, status=POOL_STATUS_WAITING, room_id=None, seats=[])
    return {"reset": True, "slot": _pool_slot_public(row, include_room=False)}


def start_pool_slot(slot: int | str, *, seed: int | None = None) -> dict[str, Any]:
    slot = _pool_validate_slot(slot)
    init_db()
    with db_connect() as conn:
        row = _pool_load_row(conn, slot)
        seats = _pool_parse_seats(row["seats_json"])
        if row["status"] == POOL_STATUS_PLAYING and row["room_id"]:
            room = _room_or_error(str(row["room_id"]))
            return {"started": False, "already_started": True, "slot": _pool_slot_public(row), "room_id": room.id, "room": public_room(room)}
        row, room = _pool_start_locked(conn, slot, seats, seed=seed)
    return {"started": True, "already_started": False, "slot": _pool_slot_public(row), "room_id": room.id, "room": public_room(room)}


def get_room(room_id: str) -> dict[str, Any]:
    return public_room(_room_or_error(room_id))


def _normalise_prompt_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    action = str(candidate.get("action") or "").strip().lower()
    cards = candidate.get("cards") or []
    if isinstance(cards, str):
        cards = [card.strip() for card in cards.split(",") if card.strip()]
    cards = [str(card).strip() for card in cards if str(card).strip()]
    return {
        "action": action,
        "cards": doudizhu.sort_cards(cards) if cards else [],
        "reason": str(candidate.get("reason") or "").strip()[:500],
        "speech": str(candidate.get("speech") or "").strip()[:300],
    }


def _legal_play_sets(legal: dict[str, Any]) -> set[tuple[str, ...]]:
    groups = legal.get("candidate_groups") if isinstance(legal.get("candidate_groups"), dict) else {}
    out: set[tuple[str, ...]] = set()
    for key, value in groups.items():
        if key in {"rocket", "rocket_cards"} or not value:
            continue
        if key == "singles":
            for card in value if isinstance(value, list) else []:
                out.add(tuple(doudizhu.sort_cards([str(card)])))
            continue
        if isinstance(value, list):
            for cards in value:
                if isinstance(cards, str):
                    cards = [cards]
                out.add(tuple(doudizhu.sort_cards([str(card) for card in cards])))
    if groups.get("rocket") and groups.get("rocket_cards"):
        out.add(tuple(doudizhu.sort_cards([str(card) for card in groups.get("rocket_cards") or []])))
    return out


def _prompt_safe_view(view: dict[str, Any]) -> dict[str, Any]:
    """Return private prompt context: own hand is allowed; opponent hands never are."""
    safe = {
        "room_id": view.get("room_id"),
        "game": view.get("game"),
        "phase": view.get("phase"),
        "my_seat": view.get("my_seat"),
        "my_seat_id": view.get("my_seat_id"),
        "my_role": view.get("my_role"),
        "current_seat": view.get("current_seat"),
        "current_seat_index": view.get("current_seat_index"),
        "is_my_turn": view.get("is_my_turn"),
        "my_hand": list(view.get("my_hand") or []),
        "players": [],
        "bottom_cards": list(view.get("bottom_cards") or []),
        "last_play": view.get("last_play"),
        "pass_count": int(view.get("pass_count") or 0),
        "recent_history": list(view.get("recent_history") or [])[-12:],
        "winner": view.get("winner"),
        "legal_summary": view.get("legal_summary") or {},
    }
    for player in view.get("players") or []:
        if not isinstance(player, dict):
            continue
        safe["players"].append(
            {
                "seat": player.get("seat"),
                "seat_id": player.get("seat_id"),
                "role": player.get("role"),
                "hand_count": player.get("hand_count"),
                "is_landlord": player.get("is_landlord"),
                "is_me": player.get("is_me"),
            }
        )
    return safe


def _prompt_safe_legal(legal: dict[str, Any]) -> dict[str, Any]:
    return {
        "room_id": legal.get("room_id"),
        "game": legal.get("game"),
        "seat": legal.get("seat"),
        "seat_id": legal.get("seat_id"),
        "is_my_turn": legal.get("is_my_turn"),
        "can_pass": legal.get("can_pass"),
        "last_play": legal.get("last_play"),
        "legal_hint": legal.get("legal_hint"),
        "candidate_groups": legal.get("candidate_groups") or {},
    }


def build_cardroom_prompt(room_id: str, seat: int | str, token: str | None = None) -> dict[str, Any]:
    """Build a prompt packet from private view + legal-actions only.

    This helper deliberately does not call spectator_view() and does not expose
    opponent hand/cards. It is safe to hand to an LLM decision step.
    """
    room = _room_or_error(room_id)
    state = doudizhu.loads_state(room.raw_state)
    seat_id = _seat_id(state, seat)
    seat_index = _seat_index(state, seat_id)
    token_value = token or (room.seat_tokens or {}).get(seat_id)
    view = _prompt_safe_view(room_view(room_id, seat=seat_index, token=token_value))
    legal = _prompt_safe_legal(legal_actions(room_id, seat=seat_index, token=token_value))
    prompt = (
        "你是斗地主 Bot，只能根据 private_view 和 legal_actions 决策。\n"
        "禁止猜测或使用对手手牌；只能从 legal_actions.candidate_groups 选择 play，"
        "或在 legal_actions.can_pass=true 时 pass。\n"
        "只输出 JSON：{\"action\":\"play|pass\",\"cards\":[...],\"speech\":\"一句短台词\",\"reason\":\"理由\"}。"
    )
    return {
        "room_id": room_id,
        "seat": seat_index,
        "seat_id": seat_id,
        "prompt": prompt,
        "private_view": view,
        "legal_actions": legal,
        "contract": ["private_view", "legal_actions"],
    }


def _review_candidate_against_context(candidate: dict[str, Any], view: dict[str, Any], legal: dict[str, Any]) -> tuple[bool, dict[str, Any], dict[str, Any]]:
    normalised = _normalise_prompt_candidate(candidate)
    action = normalised["action"]
    cards = normalised["cards"]
    failure = {
        "candidate": normalised,
        "legal_hint": legal.get("legal_hint") or (view.get("legal_summary") or {}).get("hint"),
        "legal_actions": _prompt_safe_legal(legal),
    }
    if not legal.get("is_my_turn"):
        failure.update({"code": "not_my_turn", "message": "seat is not current turn"})
        return False, normalised, failure
    if action == "pass":
        if cards:
            failure.update({"code": "pass_with_cards", "message": "pass must not include cards"})
            return False, normalised, failure
        if not legal.get("can_pass"):
            failure.update({"code": "cannot_pass", "message": "pass is not legal now"})
            return False, normalised, failure
        return True, normalised, {}
    if action != "play":
        failure.update({"code": "unknown_action", "message": "action must be play or pass"})
        return False, normalised, failure
    if not cards:
        failure.update({"code": "missing_cards", "message": "play needs cards"})
        return False, normalised, failure
    if tuple(cards) not in _legal_play_sets(legal):
        failure.update({"code": "illegal_cards", "message": "cards are not in legal-actions candidates"})
        return False, normalised, failure
    return True, normalised, {}


def review_prompt_candidate(candidate: dict[str, Any], view: dict[str, Any], legal: dict[str, Any]) -> tuple[bool, dict[str, Any], dict[str, Any]]:
    """Backward-compatible review helper using already-built safe context."""
    return _review_candidate_against_context(candidate, view, legal)


def review_cardroom_action(room_id: str, seat: int | str, candidate: dict[str, Any], token: str | None = None) -> dict[str, Any]:
    """Review one candidate using only /view and /legal-actions derived context."""
    packet = build_cardroom_prompt(room_id, seat, token=token)
    ok, normalised, failure = _review_candidate_against_context(candidate, packet["private_view"], packet["legal_actions"])
    return {
        "accepted": ok,
        "candidate": normalised,
        "failure": failure if not ok else None,
        "legal_actions": packet["legal_actions"],
    }


def fallback_prompt_action(view: dict[str, Any], legal: dict[str, Any]) -> dict[str, Any]:
    # Prompt pipeline fallback requirement: legal pass first, otherwise smallest legal play.
    if legal.get("is_my_turn") and legal.get("can_pass"):
        return {"action": "pass", "cards": [], "reason": "builtin_fallback_legal_pass", "speech": "先不出。"}
    selected = choose_fair_bot_action(view, legal)
    selected["reason"] = "builtin_fallback_" + str(selected.get("reason") or "fair")
    selected.setdefault("speech", "我先走这手。")
    return selected


def run_prompt_decision(
    room_id: str,
    seat: int | str,
    max_retries: int = 5,
    candidates: list[dict[str, Any]] | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    """Review prompt candidates and apply the first legal action; fallback after failures."""
    room = _room_or_error(room_id)
    state = doudizhu.loads_state(room.raw_state)
    seat_id = _seat_id(state, seat)
    seat_index = _seat_index(state, seat_id)
    token_value = token or (room.seat_tokens or {}).get(seat_id)
    packet = build_cardroom_prompt(room_id, seat_index, token=token_value)
    view = packet["private_view"]
    legal = packet["legal_actions"]
    retry_limit = max(0, min(int(max_retries if max_retries is not None else 5), 5))
    failures: list[dict[str, Any]] = []
    selected: dict[str, Any] | None = None
    for attempt_index, candidate in enumerate(list(candidates or [])[: retry_limit]):
        ok, normalised, failure = _review_candidate_against_context(candidate, view, legal)
        if ok:
            selected = normalised
            break
        failure["attempt"] = attempt_index + 1
        failures.append(failure)
    used_fallback = selected is None
    if used_fallback:
        selected = fallback_prompt_action(view, legal)
    retries = len(failures)
    result = apply_room_action(
        room_id,
        seat=seat_index,
        action=selected.get("action") or "pass",
        cards=selected.get("cards") or [],
        source="builtin_fallback" if used_fallback else "astrbot_prompt",
        reason=selected.get("reason") or ("builtin_fallback" if used_fallback else "astrbot_prompt"),
        speech=selected.get("speech") or None,
        retries=retries,
        token=token_value,
    )
    result["prompt_review"] = {
        "accepted": not used_fallback,
        "fallback": used_fallback,
        "retries": retries,
        "failures": failures,
        "selected": selected,
        "prompt": packet,
        "used_contract": ["private_view", "legal_actions", "actions"],
    }
    return result


def apply_reviewed_prompt_decision(
    room_id: str,
    *,
    seat: int | str,
    candidates: list[dict[str, Any]] | None = None,
    token: str | None = None,
    max_retries: int = 5,
    source: str = "astrbot_prompt",
) -> dict[str, Any]:
    # source is accepted for API compatibility; the reviewer writes astrbot_prompt
    # or builtin_fallback so history is unambiguous.
    return run_prompt_decision(room_id, seat, max_retries=max_retries, candidates=candidates, token=token)


def choose_fair_bot_action(view: dict[str, Any], legal: dict[str, Any]) -> dict[str, Any]:
    """Choose a deterministic weak action using only private seat view + legal-actions.

    This intentionally does not accept spectator state or raw all-hands state.
    """
    if not legal.get("is_my_turn"):
        return {"action": "pass", "cards": [], "reason": "not my turn"}
    groups = legal.get("candidate_groups") if isinstance(legal.get("candidate_groups"), dict) else {}
    for key in ("singles", "pairs", "triples", "triple_with_single", "triple_with_pair", "straights", "consecutive_pairs", "bombs"):
        value = groups.get(key)
        if not value:
            continue
        cards = value[0]
        if isinstance(cards, str):
            cards = [cards]
        return {"action": "play", "cards": list(cards), "reason": f"fair_bot_min_{key}"}
    rocket_cards = groups.get("rocket_cards") if groups.get("rocket") else None
    if rocket_cards:
        return {"action": "play", "cards": list(rocket_cards), "reason": "fair_bot_min_rocket"}
    if legal.get("can_pass"):
        return {"action": "pass", "cards": [], "reason": "fair_bot_no_play"}
    # Fairness rule: never invent an action from my_hand. If legal-actions does not
    # offer a playable group, pass/fail safely instead of submitting a possibly
    # illegal private-hand guess.
    return {"action": "pass", "cards": [], "reason": "fair_bot_no_legal_action"}


def step_room(room_id: str) -> dict[str, Any]:
    room = _room_or_error(room_id)
    state = doudizhu.loads_state(room.raw_state)
    current = doudizhu.current_player(state)
    seat = _seat_index(state, current)
    token = (room.seat_tokens or {}).get(current)
    view = room_view(room_id, seat=seat, token=token)
    legal = legal_actions(room_id, seat=seat, token=token)
    selected = choose_fair_bot_action(view, legal)
    return apply_room_action(
        room_id,
        seat=seat,
        action=selected["action"],
        cards=selected.get("cards") or [],
        source="fair_bot",
        reason=selected.get("reason") or "fair_bot",
        token=token,
    )


def auto_run_room(room_id: str, max_steps: int = 20) -> dict[str, Any]:
    room = _room_or_error(room_id)
    raw, moves = doudizhu.auto_run(room.raw_state, max_steps=max_steps)
    room.raw_state = raw
    with db_connect() as conn:
        _upsert_room(conn, room)
        idx = _next_action_index(conn, room.id)
        for move in moves:
            _record_move(conn, room.id, move, action_index=idx)
            idx += 1
    out = public_room(room)
    state = out["state"]
    out.update(
        {
            "steps_run": len(moves),
            "moves": moves,
            "finished": state.get("phase") == "finished",
            "winner": state.get("winner"),
        }
    )
    return out
