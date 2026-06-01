from __future__ import annotations

import asyncio

from fastapi import HTTPException

from app.engine import INITIAL_FEN, RED, BLACK, apply_ucci, parse_fen
from app.main import (
    Bot,
    Challenge,
    Match,
    bots,
    challenges,
    emit_pending_challenges_for_bot,
    match_sse_payload,
    subscribers,
    tokens,
    ucci_to_chinese,
    x_bot_token,
)


def reset_state() -> None:
    bots.clear()
    tokens.clear()
    challenges.clear()
    subscribers.clear()


def test_regression_fen_uses_xiangqi_h_and_side_r_b():
    assert INITIAL_FEN.startswith("rheakaehr/")
    board, turn, _, _ = parse_fen(INITIAL_FEN)
    assert turn == RED
    assert board[9][1] == "H"
    fen_after, _, _ = apply_ucci(INITIAL_FEN, "h2e2")
    assert " b " in fen_after
    assert parse_fen(fen_after)[0][7][4] == "C"


def test_regression_chinese_notation_direction_and_steps():
    assert ucci_to_chinese("h2e2", INITIAL_FEN) == "炮二平五"
    assert ucci_to_chinese("a3a4", INITIAL_FEN) == "兵九进一"
    fen_after, _, _ = apply_ucci(INITIAL_FEN, "h2e2")
    assert ucci_to_chinese("a6a5", fen_after) == "卒1进1"


def test_regression_match_sse_payload_includes_turn_and_full_public_fields():
    reset_state()
    bots["red"] = Bot(id="red", name="红", token="tr")
    bots["black"] = Bot(id="black", name="黑", token="tb")
    m = Match(id="m1", red_bot_id="red", black_bot_id="black")
    payload = match_sse_payload(m)
    assert payload["turn"] == RED
    assert payload["turn_bot_id"] == "red"
    assert payload["red_bot_name"] == "红"
    assert payload["last_move"] is None


def test_regression_x_bot_token_accepts_bearer_authorization():
    reset_state()
    bots["bot"] = Bot(id="bot", name="Bot", token="secret")
    tokens["secret"] = "bot"
    assert asyncio.run(x_bot_token(x_bot_token=None, authorization="Bearer secret")) == "bot"
    assert asyncio.run(x_bot_token(x_bot_token="secret", authorization=None)) == "bot"
    try:
        asyncio.run(x_bot_token(x_bot_token=None, authorization=None))
    except HTTPException as exc:
        assert exc.status_code == 401
    else:
        raise AssertionError("missing token should fail")


def test_regression_pending_challenge_replayed_on_reconnect():
    reset_state()
    bots["challenger"] = Bot(id="challenger", name="A", token="ta")
    bots["opponent"] = Bot(id="opponent", name="B", token="tb")
    ch = Challenge(id="ch1", challenger_bot_id="challenger", opponent_bot_id="opponent", challenger_side=RED)
    challenges[ch.id] = ch
    q = asyncio.Queue()
    subscribers.setdefault("opponent", set()).add(q)
    emitted = asyncio.run(emit_pending_challenges_for_bot("opponent"))
    assert emitted == 1
    item = q.get_nowait()
    assert item["event"] == "challenge_received"
    assert item["data"]["challenge_id"] == "ch1"
