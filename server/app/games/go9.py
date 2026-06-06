from __future__ import annotations

import copy
import json
import re
from typing import Any

SIZE = 9
BLACK = "black"
WHITE = "white"
PASS = "pass"
_COORD_RE = re.compile(r"^([a-i])([1-9])$", re.IGNORECASE)


class GoRuleError(ValueError):
    pass


def initial_state() -> dict[str, Any]:
    return {
        "game": "go",
        "size": SIZE,
        "board": [[None for _ in range(SIZE)] for _ in range(SIZE)],
        "turn": BLACK,
        "passes": 0,
        "captures": {BLACK: 0, WHITE: 0},
        "moves": [],
    }


def dumps_state(state: dict[str, Any]) -> str:
    return json.dumps(state, ensure_ascii=False, separators=(",", ":"))


def initial_state_json() -> str:
    return dumps_state(initial_state())


def loads_state(raw: str | dict[str, Any] | None) -> dict[str, Any]:
    if isinstance(raw, dict):
        state = copy.deepcopy(raw)
    elif raw:
        try:
            state = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise GoRuleError("invalid go state") from exc
    else:
        state = initial_state()
    if state.get("game") != "go" or state.get("size") != SIZE:
        raise GoRuleError("invalid go state")
    board = state.get("board")
    if not isinstance(board, list) or len(board) != SIZE or any(not isinstance(r, list) or len(r) != SIZE for r in board):
        raise GoRuleError("invalid go board")
    state.setdefault("turn", BLACK)
    state.setdefault("passes", 0)
    state.setdefault("captures", {BLACK: 0, WHITE: 0})
    state.setdefault("moves", [])
    return state


def coord_to_rc(coord: str) -> tuple[int, int]:
    m = _COORD_RE.match((coord or "").strip())
    if not m:
        raise GoRuleError("invalid go coordinate")
    col = ord(m.group(1).lower()) - ord("a")
    # a1 is bottom-left; board[0] is top row / rank 9.
    row = SIZE - int(m.group(2))
    return row, col


def rc_to_coord(row: int, col: int) -> str:
    if not (0 <= row < SIZE and 0 <= col < SIZE):
        raise GoRuleError("coordinate out of board")
    return f"{chr(ord('a') + col)}{SIZE - row}"


def other(color: str) -> str:
    return WHITE if color == BLACK else BLACK


def neighbors(row: int, col: int):
    for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        nr, nc = row + dr, col + dc
        if 0 <= nr < SIZE and 0 <= nc < SIZE:
            yield nr, nc


def group_and_liberties(board: list[list[str | None]], row: int, col: int) -> tuple[set[tuple[int, int]], set[tuple[int, int]]]:
    color = board[row][col]
    if color not in (BLACK, WHITE):
        return set(), set()
    group: set[tuple[int, int]] = set()
    liberties: set[tuple[int, int]] = set()
    stack = [(row, col)]
    while stack:
        p = stack.pop()
        if p in group:
            continue
        group.add(p)
        r, c = p
        for nr, nc in neighbors(r, c):
            v = board[nr][nc]
            if v is None:
                liberties.add((nr, nc))
            elif v == color and (nr, nc) not in group:
                stack.append((nr, nc))
    return group, liberties


def score(state: dict[str, Any]) -> dict[str, int]:
    board = state["board"]
    captures = state.get("captures", {})
    black_stones = sum(1 for row in board for v in row if v == BLACK)
    white_stones = sum(1 for row in board for v in row if v == WHITE)
    return {
        BLACK: black_stones + int(captures.get(BLACK, 0)),
        WHITE: white_stones + int(captures.get(WHITE, 0)),
    }


def result_from_score(state: dict[str, Any]) -> tuple[str, str | None]:
    s = score(state)
    if s[BLACK] > s[WHITE]:
        return "black_win", BLACK
    if s[WHITE] > s[BLACK]:
        # Existing arena result vocabulary uses red_win for the non-black side.
        return "red_win", WHITE
    return "draw", None


def apply_move(state_or_json: str | dict[str, Any], move: str) -> tuple[str, dict[str, Any]]:
    state = loads_state(state_or_json)
    if state.get("finished"):
        raise GoRuleError("game already finished")
    move = (move or "").strip().lower()
    color = state.get("turn", BLACK)
    if color not in (BLACK, WHITE):
        raise GoRuleError("invalid turn")

    if move == PASS:
        state["passes"] = int(state.get("passes", 0)) + 1
        finished = state["passes"] >= 2
        state["turn"] = other(color)
        info = {"move": PASS, "side": color, "captured": [], "finished": finished, "pass": True}
        if finished:
            result, winner = result_from_score(state)
            state["finished"] = True
            state["result"] = result
            state["winner"] = winner
            state["finish_reason"] = "double_pass"
            state["score"] = score(state)
        state.setdefault("moves", []).append({"move": PASS, "side": color})
        return dumps_state(state), info

    row, col = coord_to_rc(move)
    board = copy.deepcopy(state["board"])
    if board[row][col] is not None:
        raise GoRuleError("point already occupied")
    board[row][col] = color
    enemy = other(color)
    captured: list[str] = []
    checked_enemy_groups: set[tuple[int, int]] = set()
    for nr, nc in neighbors(row, col):
        if board[nr][nc] != enemy or (nr, nc) in checked_enemy_groups:
            continue
        group, libs = group_and_liberties(board, nr, nc)
        checked_enemy_groups.update(group)
        if not libs:
            for gr, gc in group:
                board[gr][gc] = None
                captured.append(rc_to_coord(gr, gc))

    own_group, own_libs = group_and_liberties(board, row, col)
    if not own_libs:
        raise GoRuleError("suicide move is not allowed")

    state["board"] = board
    state["passes"] = 0
    state["turn"] = enemy
    state.setdefault("captures", {BLACK: 0, WHITE: 0})
    state["captures"][color] = int(state["captures"].get(color, 0)) + len(captured)
    state.setdefault("moves", []).append({"move": move, "side": color, "captured": captured})
    info = {"move": move, "side": color, "captured": captured, "finished": False, "pass": False}
    return dumps_state(state), info
