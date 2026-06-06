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
