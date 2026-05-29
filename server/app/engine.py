from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

FILES = "abcdefghi"
RANKS = "0123456789"
RED = "red"
BLACK = "black"
INITIAL_FEN = "rheakaehr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RHEAKAEHR r - - 0 1"

# Internal board coordinates: row 0 is black home rank (UCCI rank 9), row 9 is red home rank (UCCI rank 0).
# UCCI file a-i maps col 0-8; rank 0-9 maps row 9-rank.


@dataclass(frozen=True)
class Move:
    fr: int
    fc: int
    tr: int
    tc: int


class RuleError(ValueError):
    pass


def side_of(piece: str) -> str:
    return RED if piece.isupper() else BLACK


def opponent(side: str) -> str:
    return BLACK if side == RED else RED


def parse_ucci(move: str) -> Move:
    if len(move) != 4:
        raise RuleError("move must be 4 chars UCCI, e.g. h2e2")
    f1, r1, f2, r2 = move[0], move[1], move[2], move[3]
    if f1 not in FILES or f2 not in FILES or r1 not in RANKS or r2 not in RANKS:
        raise RuleError("invalid UCCI coordinate")
    return Move(fr=9 - int(r1), fc=FILES.index(f1), tr=9 - int(r2), tc=FILES.index(f2))


def move_to_ucci(m: Move) -> str:
    return f"{FILES[m.fc]}{9-m.fr}{FILES[m.tc]}{9-m.tr}"


def parse_fen(fen: str) -> tuple[list[list[str | None]], str, int, int]:
    parts = fen.split()
    if len(parts) < 2:
        raise RuleError("invalid FEN")
    rows = parts[0].split("/")
    if len(rows) != 10:
        raise RuleError("invalid FEN rows")
    board: list[list[str | None]] = []
    for row in rows:
        out: list[str | None] = []
        for ch in row:
            if ch.isdigit():
                out.extend([None] * int(ch))
            else:
                out.append(ch)
        if len(out) != 9:
            raise RuleError("invalid FEN row width")
        board.append(out)
    side = RED if parts[1] == "r" else BLACK if parts[1] == "b" else None
    if side is None:
        raise RuleError("invalid FEN side")
    halfmove = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 0
    fullmove = int(parts[5]) if len(parts) > 5 and parts[5].isdigit() else 1
    return board, side, halfmove, fullmove


def board_to_fen(board: list[list[str | None]], side: str, halfmove: int = 0, fullmove: int = 1) -> str:
    rows = []
    for r in range(10):
        row = ""
        empty = 0
        for c in range(9):
            p = board[r][c]
            if p is None:
                empty += 1
            else:
                if empty:
                    row += str(empty)
                    empty = 0
                row += p
        if empty:
            row += str(empty)
        rows.append(row)
    return "/".join(rows) + f" {'r' if side == RED else 'b'} - - {halfmove} {fullmove}"


def in_bounds(r: int, c: int) -> bool:
    return 0 <= r < 10 and 0 <= c < 9


def in_palace(side: str, r: int, c: int) -> bool:
    if c < 3 or c > 5:
        return False
    return 7 <= r <= 9 if side == RED else 0 <= r <= 2


def crossed_river(side: str, r: int) -> bool:
    return r <= 4 if side == RED else r >= 5


def path_clear(board: list[list[str | None]], fr: int, fc: int, tr: int, tc: int) -> bool:
    if fr == tr:
        step = 1 if tc > fc else -1
        return all(board[fr][c] is None for c in range(fc + step, tc, step))
    if fc == tc:
        step = 1 if tr > fr else -1
        return all(board[r][fc] is None for r in range(fr + step, tr, step))
    return False


def count_between(board: list[list[str | None]], fr: int, fc: int, tr: int, tc: int) -> int:
    if fr == tr:
        step = 1 if tc > fc else -1
        return sum(board[fr][c] is not None for c in range(fc + step, tc, step))
    if fc == tc:
        step = 1 if tr > fr else -1
        return sum(board[r][fc] is not None for r in range(fr + step, tr, step))
    return 999


def pseudo_legal_piece(board: list[list[str | None]], m: Move, side: str) -> bool:
    if not in_bounds(m.fr, m.fc) or not in_bounds(m.tr, m.tc):
        return False
    piece = board[m.fr][m.fc]
    if piece is None or side_of(piece) != side:
        return False
    target = board[m.tr][m.tc]
    if target is not None and side_of(target) == side:
        return False
    dr, dc = m.tr - m.fr, m.tc - m.fc
    adr, adc = abs(dr), abs(dc)
    p = piece.upper()

    if p == "K":
        # Normal palace king move. Flying-general capture is handled as legal attack/move along file.
        if m.fc == m.tc and target is not None and target.upper() == "K" and path_clear(board, m.fr, m.fc, m.tr, m.tc):
            return True
        return adr + adc == 1 and in_palace(side, m.tr, m.tc)
    if p == "A":
        return adr == 1 and adc == 1 and in_palace(side, m.tr, m.tc)
    if p == "E":
        eye_r, eye_c = (m.fr + m.tr) // 2, (m.fc + m.tc) // 2
        if not (adr == 2 and adc == 2 and board[eye_r][eye_c] is None):
            return False
        return m.tr >= 5 if side == RED else m.tr <= 4
    if p == "H":
        if not ((adr, adc) in ((2, 1), (1, 2))):
            return False
        leg_r = m.fr + (dr // 2 if adr == 2 else 0)
        leg_c = m.fc + (dc // 2 if adc == 2 else 0)
        return board[leg_r][leg_c] is None
    if p == "R":
        return (m.fr == m.tr or m.fc == m.tc) and path_clear(board, m.fr, m.fc, m.tr, m.tc)
    if p == "C":
        if not (m.fr == m.tr or m.fc == m.tc):
            return False
        screens = count_between(board, m.fr, m.fc, m.tr, m.tc)
        return screens == (1 if target is not None else 0)
    if p == "P":
        forward = -1 if side == RED else 1
        if dr == forward and dc == 0:
            return True
        return crossed_river(side, m.fr) and dr == 0 and adc == 1
    return False


def find_king(board: list[list[str | None]], side: str) -> tuple[int, int] | None:
    k = "K" if side == RED else "k"
    for r in range(10):
        for c in range(9):
            if board[r][c] == k:
                return r, c
    return None


def kings_face(board: list[list[str | None]]) -> bool:
    red_k = find_king(board, RED)
    black_k = find_king(board, BLACK)
    if not red_k or not black_k or red_k[1] != black_k[1]:
        return False
    return path_clear(board, red_k[0], red_k[1], black_k[0], black_k[1])


def is_in_check(board: list[list[str | None]], side: str) -> bool:
    king = find_king(board, side)
    if king is None:
        return True
    if kings_face(board):
        return True
    attacker = opponent(side)
    kr, kc = king
    for r in range(10):
        for c in range(9):
            p = board[r][c]
            if p and side_of(p) == attacker:
                if pseudo_legal_piece(board, Move(r, c, kr, kc), attacker):
                    return True
    return False


def copy_board(board: list[list[str | None]]) -> list[list[str | None]]:
    return [row[:] for row in board]


def apply_move_to_board(board: list[list[str | None]], m: Move) -> list[list[str | None]]:
    nb = copy_board(board)
    nb[m.tr][m.tc] = nb[m.fr][m.fc]
    nb[m.fr][m.fc] = None
    return nb


def legal_moves_from_board(board: list[list[str | None]], side: str) -> list[str]:
    moves: list[str] = []
    for fr in range(10):
        for fc in range(9):
            p = board[fr][fc]
            if p is None or side_of(p) != side:
                continue
            for tr in range(10):
                for tc in range(9):
                    m = Move(fr, fc, tr, tc)
                    if pseudo_legal_piece(board, m, side):
                        nb = apply_move_to_board(board, m)
                        if not is_in_check(nb, side):
                            moves.append(move_to_ucci(m))
    return moves


def legal_moves(fen: str) -> list[str]:
    board, side, _, _ = parse_fen(fen)
    return legal_moves_from_board(board, side)


def apply_ucci(fen: str, move: str) -> tuple[str, str | None, bool]:
    board, side, halfmove, fullmove = parse_fen(fen)
    m = parse_ucci(move)
    piece = board[m.fr][m.fc] if in_bounds(m.fr, m.fc) else None
    if piece is None:
        raise RuleError("no piece at source")
    if side_of(piece) != side:
        raise RuleError("not this side's piece")
    if not pseudo_legal_piece(board, m, side):
        raise RuleError("illegal piece move")
    captured = board[m.tr][m.tc]
    nb = apply_move_to_board(board, m)
    if is_in_check(nb, side):
        raise RuleError("move leaves king in check")
    next_side = opponent(side)
    next_fullmove = fullmove + (1 if side == BLACK else 0)
    next_halfmove = 0 if captured is not None or piece.upper() == "P" else halfmove + 1
    finished = find_king(nb, next_side) is None or len(legal_moves_from_board(nb, next_side)) == 0
    return board_to_fen(nb, next_side, next_halfmove, next_fullmove), captured, finished
