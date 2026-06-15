import json

from fastapi.testclient import TestClient

from app.main import app
from test_api import auth, reset_state


def create_go_match(client: TestClient):
    black = client.post("/api/bots/register", json={"name": "go-black", "game": "go"}).json()
    white = client.post("/api/bots/register", json={"name": "go-white", "game": "go"}).json()
    ch = client.post(
        "/api/challenges",
        headers=auth(black["token"]),
        json={"opponent_bot_id": white["bot_id"], "side": "black", "game": "go"},
    ).json()
    accepted = client.post(f"/api/challenges/{ch['challenge_id']}/accept", headers=auth(white["token"])).json()
    return black, white, accepted["match_id"], accepted["match"]


def point(match, coord):
    col = ord(coord[0]) - ord("a")
    row = 9 - int(coord[1])
    return match["board"][row][col]


def post_move(client, match_id, bot, move):
    return client.post(f"/api/matches/{match_id}/move", headers=auth(bot["token"]), json={"move": move})


def test_go_initial_state_is_9x9():
    reset_state()
    client = TestClient(app)
    black, white, match_id, match = create_go_match(client)
    assert match["game"] == "go"
    assert match["turn"] == "black"
    assert match["turn_bot_id"] == black["bot_id"]
    assert match["white_bot_id"] == white["bot_id"]
    assert len(match["board"]) == 9
    assert all(len(row) == 9 for row in match["board"])
    assert all(cell is None for row in match["board"] for cell in row)
    state = json.loads(match["state_json"])
    assert state["size"] == 9


def test_go_black_first_move_can_place_stone():
    reset_state()
    client = TestClient(app)
    black, white, match_id, match = create_go_match(client)
    res = post_move(client, match_id, black, "d4")
    assert res.status_code == 200, res.text
    match = res.json()["match"]
    assert point(match, "d4") == "black"
    assert match["turn"] == "white"
    assert match["turn_bot_id"] == white["bot_id"]
    assert match["ply"] == 1


def test_go_cannot_place_on_occupied_point():
    reset_state()
    client = TestClient(app)
    black, white, match_id, match = create_go_match(client)
    assert post_move(client, match_id, black, "d4").status_code == 200
    res = post_move(client, match_id, white, "d4")
    assert res.status_code == 400
    match = client.get(f"/api/admin/matches/{match_id}").json()
    assert match["ply"] == 1
    assert point(match, "d4") == "black"


def test_go_can_pass_turn():
    reset_state()
    client = TestClient(app)
    black, white, match_id, match = create_go_match(client)
    res = post_move(client, match_id, black, "pass")
    assert res.status_code == 200, res.text
    match = res.json()["match"]
    assert match["passes"] == 1
    assert match["turn"] == "white"
    assert match["status"] == "active"
    assert all(cell is None for row in match["board"] for cell in row)


def test_go_two_consecutive_passes_finish_match():
    reset_state()
    client = TestClient(app)
    black, white, match_id, match = create_go_match(client)
    assert post_move(client, match_id, black, "pass").status_code == 200
    res = post_move(client, match_id, white, "pass")
    assert res.status_code == 200, res.text
    match = res.json()["match"]
    assert match["status"] == "finished"
    assert match["finish_reason"] == "double_pass"
    assert match["result"] in {"black_win", "red_win", "draw"}
    assert post_move(client, match_id, black, "a1").status_code == 400


def test_go_simple_capture_removes_single_stone():
    reset_state()
    client = TestClient(app)
    black, white, match_id, match = create_go_match(client)
    sequence = [
        (black, "b1"),
        (white, "a1"),
        (black, "pass"),
        (white, "b2"),
        (black, "pass"),
    ]
    for bot, move in sequence:
        res = post_move(client, match_id, bot, move)
        assert res.status_code == 200, res.text
    res = post_move(client, match_id, white, "c1")
    assert res.status_code == 200, res.text
    data = res.json()
    match = data["match"]
    assert point(match, "b1") is None
    assert point(match, "a1") == "white"
    assert point(match, "b2") == "white"
    assert point(match, "c1") == "white"
    assert data["move"]["captured_count"] == 1
    assert data["move"]["captured"] == ["b1"]
    assert match["captures"]["white"] == 1


def test_go_legal_moves_initial_include_points_and_pass():
    from app.games import go9

    state = go9.initial_state()
    moves = go9.legal_moves(state)
    assert len(moves) == 82
    assert "a1" in moves
    assert "i9" in moves
    assert "pass" in moves


def test_go_legal_moves_exclude_occupied_points():
    from app.games import go9

    state_json, _ = go9.apply_move(go9.initial_state(), "d4")
    moves = go9.legal_moves(state_json)
    assert "d4" not in moves
    assert "pass" in moves


def test_go_legal_moves_exclude_simple_suicide():
    from app.games import go9

    state = go9.initial_state()
    board = state["board"]
    # It is black to move; a1 is surrounded by white stones at a2/b1.
    board[go9.SIZE - 2][0] = go9.WHITE  # a2
    board[go9.SIZE - 1][1] = go9.WHITE  # b1
    moves = go9.legal_moves(state)
    assert "a1" not in moves
    assert "pass" in moves


def test_go_match_public_includes_legal_moves():
    reset_state()
    client = TestClient(app)
    black, white, match_id, match = create_go_match(client)
    assert match["game"] == "go"
    assert match["turn_bot_id"] == black["bot_id"]
    assert "legal_moves" in match
    assert "a1" in match["legal_moves"]
    assert "pass" in match["legal_moves"]


def test_go_move_response_updates_legal_moves_after_occupied_point():
    reset_state()
    client = TestClient(app)
    black, white, match_id, match = create_go_match(client)
    res = post_move(client, match_id, black, "d4")
    assert res.status_code == 200, res.text
    match = res.json()["match"]
    assert match["game"] == "go"
    assert match["turn_bot_id"] == white["bot_id"]
    assert "legal_moves" in match
    assert "d4" not in match["legal_moves"]
    assert "pass" in match["legal_moves"]


def test_go_bot_runner_can_play_black_and_white_from_legal_moves():
    reset_state()
    client = TestClient(app)
    black, white, match_id, match = create_go_match(client)
    tokens = {black["bot_id"]: black["token"], white["bot_id"]: white["token"]}
    played = []
    for _ in range(2):
        match = client.get(f"/api/matches/{match_id}", headers=auth(black["token"])).json()
        assert match["status"] == "active"
        assert match["game"] == "go"
        move = next((m for m in match.get("legal_moves", []) if m != "pass"), "pass")
        bot_id = match["turn_bot_id"]
        res = client.post(f"/api/matches/{match_id}/move", headers=auth(tokens[bot_id]), json={"move": move})
        assert res.status_code == 200, res.text
        played.append(res.json()["move"]["side"])
    assert played == ["black", "white"]
    final_match = client.get(f"/api/matches/{match_id}", headers=auth(black["token"])).json()
    stones = [cell for row in final_match["board"] for cell in row if cell]
    assert "black" in stones and "white" in stones


def test_go_pending_turn_payload_includes_legal_moves(monkeypatch):
    import asyncio
    import app.main as main_module

    reset_state()
    client = TestClient(app)
    black, white, match_id, match = create_go_match(client)
    emitted = []

    async def fake_emit(bot_id, event, data):
        emitted.append((bot_id, event, data))

    monkeypatch.setattr(main_module, "emit", fake_emit)
    count = asyncio.run(main_module.emit_pending_turns_for_bot(black["bot_id"]))
    assert count == 1
    bot_id, event, data = emitted[-1]
    assert bot_id == black["bot_id"]
    assert event == "your_turn"
    assert data["game"] == "go"
    assert data["turn_bot_id"] == black["bot_id"]
    assert "legal_moves" in data
    assert "pass" in data["legal_moves"]
