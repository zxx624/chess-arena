from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SERVER_DIR = Path(__file__).resolve().parents[1]
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from app.games import go9
from app.main import app
from test_api import auth, reset_state


GO9_ANALYZE_PATH = "/api/go9/analyze"


def _register_go_bot(client: TestClient, name: str = "go9-adapter-bot") -> dict:
    res = client.post("/api/bots/register", json={"name": name, "game": "go"})
    assert res.status_code in {200, 201}, res.text
    body = res.json()
    assert body.get("token"), body
    return body


def _extract_move(body: dict) -> str | None:
    """Accept either the preferred Go adapter field or legacy-compatible aliases."""
    move = body.get("move") or body.get("best_move") or body.get("action")
    return move.strip().lower() if isinstance(move, str) else None


def _post_go9_analyze(
    client: TestClient,
    token: str,
    state: str | dict,
    legal_moves: list[str] | None = None,
):
    payload = {"state": state, "legal_moves": legal_moves or go9.legal_moves(state), "depth": 1}
    return client.post(GO9_ANALYZE_PATH, headers=auth(token), json=payload)


def test_go9_analyze_returns_a_legal_move_for_initial_position():
    reset_state()
    client = TestClient(app)
    bot = _register_go_bot(client)
    state = go9.initial_state_json()
    legal_moves = go9.legal_moves(state)

    res = _post_go9_analyze(client, bot["token"], state, legal_moves)

    assert res.status_code == 200, res.text
    body = res.json()
    move = _extract_move(body)
    assert move in legal_moves, body
    assert go9.is_legal_point(state, move) or move == go9.PASS
    assert body.get("game") in (None, "go", "go9")


def test_go9_analyze_falls_back_to_pass_when_pass_is_only_legal_move():
    reset_state()
    client = TestClient(app)
    bot = _register_go_bot(client, "go9-pass-fallback-bot")
    state = go9.initial_state_json()

    res = _post_go9_analyze(client, bot["token"], state, [go9.PASS])

    assert res.status_code == 200, res.text
    body = res.json()
    assert _extract_move(body) == go9.PASS
    assert body.get("fallback") in (None, True, "pass")


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"state": "not-json", "legal_moves": ["d4", "pass"]},
        {"state": go9.initial_state_json(), "legal_moves": ["z9"]},
        {"state": go9.initial_state_json(), "legal_moves": []},
    ],
)
def test_go9_analyze_rejects_bad_input(payload: dict):
    reset_state()
    client = TestClient(app)
    bot = _register_go_bot(client, "go9-bad-input-bot")

    res = client.post(GO9_ANALYZE_PATH, headers=auth(bot["token"]), json=payload)

    assert res.status_code in {400, 422}, res.text


def test_go9_analyze_requires_bot_token():
    reset_state()
    client = TestClient(app)
    state = go9.initial_state_json()

    res = client.post(
        GO9_ANALYZE_PATH,
        json={"state": state, "legal_moves": go9.legal_moves(state), "depth": 1},
    )

    assert res.status_code in {401, 403}, res.text


def test_existing_match_go_flow_still_accepts_move_and_pass():
    reset_state()
    client = TestClient(app)
    black = _register_go_bot(client, "go9-flow-black")
    white = _register_go_bot(client, "go9-flow-white")
    challenge = client.post(
        "/api/challenges",
        headers=auth(black["token"]),
        json={"opponent_bot_id": white["bot_id"], "side": "black", "game": "go"},
    )
    assert challenge.status_code == 200, challenge.text
    accepted = client.post(
        f"/api/challenges/{challenge.json()['challenge_id']}/accept",
        headers=auth(white["token"]),
    )
    assert accepted.status_code == 200, accepted.text
    match = accepted.json()["match"]
    assert match["game"] == "go"
    assert match["turn_bot_id"] == black["bot_id"]
    assert go9.PASS in match.get("legal_moves", [])

    first = client.post(
        f"/api/matches/{accepted.json()['match_id']}/move",
        headers=auth(black["token"]),
        json={"move": "d4"},
    )
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert first_body["match"]["game"] == "go"
    assert first_body["move"]["move"] == "d4"
    assert first_body["match"]["turn_bot_id"] == white["bot_id"]

    second = client.post(
        f"/api/matches/{accepted.json()['match_id']}/move",
        headers=auth(white["token"]),
        json={"move": go9.PASS},
    )
    assert second.status_code == 200, second.text
    second_body = second.json()
    assert second_body["match"]["game"] == "go"
    assert second_body["move"]["move"] == go9.PASS
    assert second_body["match"]["turn_bot_id"] == black["bot_id"]
