from app.engine import INITIAL_FEN, apply_ucci, legal_moves, parse_fen


def test_initial_legal_moves_include_common_opening():
    moves = legal_moves(INITIAL_FEN)
    assert "h2e2" in moves
    assert "b0c2" in moves
    assert len(moves) > 20


def test_apply_move_switches_turn():
    fen, captured, finished = apply_ucci(INITIAL_FEN, "h2e2")
    _, side, _, _ = parse_fen(fen)
    assert captured is None
    assert finished is False
    assert side == "black"
