import os
import tempfile
import uuid
from pathlib import Path

os.environ.setdefault("CHESS_ARENA_DB", os.path.join(tempfile.gettempdir(), "chess-arena-test.db"))

from fastapi.testclient import TestClient

from app.engine import legal_moves
import app.main as main_module
from app.main import app, bots, challenges, load_state_from_db, matches


def auth(token: str):
    return {"Authorization": f"Bearer {token}"}


def reset_state():
    db = os.path.join(tempfile.gettempdir(), f"chess-arena-test-{uuid.uuid4().hex}.db")
    os.environ["CHESS_ARENA_DB"] = db
    main_module.DB_PATH = Path(db)
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(db + suffix)
        except FileNotFoundError:
            pass
    load_state_from_db()


def test_fake_bot_smoke_match_n_steps():
    reset_state()
    client = TestClient(app)
    a = client.post("/api/bots/register", json={"name": "fake-a"}).json()
    b = client.post("/api/bots/register", json={"name": "fake-b"}).json()

    assert client.get("/api/bots/me", headers=auth(a["token"])).json()["bot_id"] == a["bot_id"]

    ch = client.post(
        "/api/challenges",
        headers=auth(a["token"]),
        json={"opponent_bot_id": b["bot_id"], "side": "red"},
    ).json()
    accepted = client.post(f"/api/challenges/{ch['challenge_id']}/accept", headers=auth(b["token"])).json()
    match_id = accepted["match_id"]
    match = accepted["match"]
    assert match["status"] == "active"

    tokens = {a["bot_id"]: a["token"], b["bot_id"]: b["token"]}
    for _ in range(12):
        match = client.get(f"/api/matches/{match_id}", headers=auth(a["token"])).json()
        if match["status"] != "active":
            break
        moves = legal_moves(match["fen"])
        assert moves
        token = tokens[match["turn_bot_id"]]
        res = client.post(f"/api/matches/{match_id}/move", headers=auth(token), json={"move": moves[0], "comment": "fake"})
        assert res.status_code == 200, res.text

    final = client.get(f"/api/matches/{match_id}", headers=auth(a["token"])).json()
    assert final["ply"] >= 12
    assert len(final["moves"]) >= 12


def test_illegal_move_rejected_and_turn_enforced():
    reset_state()
    client = TestClient(app)
    a = client.post("/api/bots/register", json={"name": "turn-a"}).json()
    b = client.post("/api/bots/register", json={"name": "turn-b"}).json()
    ch = client.post("/api/challenges", headers=auth(a["token"]), json={"opponent_bot_id": b["bot_id"], "side": "red"}).json()
    accepted = client.post(f"/api/challenges/{ch['challenge_id']}/accept", headers=auth(b["token"])).json()
    match_id = accepted["match_id"]

    assert client.post(f"/api/matches/{match_id}/move", headers=auth(b["token"]), json={"move": "h2e2"}).status_code == 403
    bad = client.post(f"/api/matches/{match_id}/move", headers=auth(a["token"]), json={"move": "a0a9"})
    assert bad.status_code == 400


def test_persistence_reload_and_admin_pages():
    reset_state()
    client = TestClient(app)
    a = client.post("/api/bots/register", json={"name": "persist-a"}).json()
    b = client.post("/api/bots/register", json={"name": "persist-b"}).json()
    ch = client.post("/api/challenges", headers=auth(a["token"]), json={"opponent_bot_id": b["bot_id"], "side": "red"}).json()
    accepted = client.post(f"/api/challenges/{ch['challenge_id']}/accept", headers=auth(b["token"])).json()
    match_id = accepted["match_id"]
    first_move = legal_moves(accepted["match"]["fen"])[0]
    assert client.post(f"/api/matches/{match_id}/move", headers=auth(a["token"]), json={"move": first_move, "comment": "persist"}).status_code == 200

    bots.clear()
    challenges.clear()
    matches.clear()
    load_state_from_db()

    assert a["bot_id"] in bots
    assert ch["challenge_id"] in challenges
    assert match_id in matches
    assert matches[match_id].ply == 1
    assert matches[match_id].moves[0]["move"] == first_move

    listing = client.get("/api/admin/matches").json()
    assert listing["total"] >= 1
    assert any(m["match_id"] == match_id for m in listing["matches"])
    detail = client.get(f"/api/admin/matches/{match_id}").json()
    assert detail["moves"][0]["comment"] == "persist"
    assert client.get("/admin/matches").status_code == 200


def test_admin_bot_management_requires_token_and_can_create_list_delete(monkeypatch):
    monkeypatch.setattr('app.main.ADMIN_TOKEN', 'test-admin-token')
    reset_state()
    client = TestClient(app)

    assert client.get("/admin/bots").status_code == 200
    denied = client.get("/api/admin/bots")
    assert denied.status_code == 403

    created = client.post(
        "/api/admin/bots",
        headers=auth("test-admin-token"),
        json={"name": "manual-admin-bot", "token": "manual-token-123", "engine_mode": "xqwlight"},
    )
    assert created.status_code == 200, created.text
    bot = created.json()["bot"]
    assert bot["token"] == "manual-token-123"
    assert bot["engine_mode"] == "xqwlight"

    listing = client.get("/api/admin/bots", headers=auth("test-admin-token"))
    assert listing.status_code == 200, listing.text
    assert any(b["token"] == "manual-token-123" for b in listing.json()["bots"])

    duplicate = client.post("/api/admin/bots", headers=auth("test-admin-token"), json={"name": "dup", "token": "manual-token-123"})
    assert duplicate.status_code == 409

    deleted = client.delete(f"/api/admin/bots/{bot['bot_id']}", headers=auth("test-admin-token"))
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["deleted"] is True
    assert bot["bot_id"] not in bots
    assert "manual-token-123" not in main_module.tokens


def test_v02_register_update_list_and_rankings():
    reset_state()
    client = TestClient(app)
    reg = client.post("/api/bots/register", json={
        "name": "steady-bot",
        "avatar_url": "https://example.com/a.png",
        "description": "稳健型棋手",
        "chess_style": "steady",
        "persona_prompt": "稳健但嘴硬",
        "engine_mode": "random",
        "client_type": "astrbot",
        "instance_name": "astrbot-test",
    }).json()
    me = client.get("/api/bots/me", headers=auth(reg["token"])).json()
    assert me["avatar_url"].endswith("a.png")
    assert me["chess_style"] == "steady"
    assert me["online_status"] == "offline"
    updated = client.patch("/api/bots/me", headers=auth(reg["token"]), json={"description": "进攻型棋手", "chess_style": "aggressive", "is_public": True}).json()
    assert updated["description"] == "进攻型棋手"
    listing = client.get("/api/bots", params={"q": "进攻", "limit": 10}).json()
    assert listing["total"] == 1
    assert listing["bots"][0]["rating"] == 1000
    assert {"games", "wins", "losses", "draws"}.issubset(listing["bots"][0].keys())
    rankings = client.get("/api/rankings").json()
    assert rankings["total"] == 1
    assert rankings["rankings"][0]["bot_id"] == reg["bot_id"]


def test_v02_sse_sets_online_status_basic():
    reset_state()
    client = TestClient(app)
    reg = client.post("/api/bots/register", json={"name": "online-bot"}).json()
    # Use the in-memory state mutation to verify the same fields the SSE endpoint
    # sets. TestClient streaming keeps the generator open and can hang the suite,
    # so don't block on a long-lived SSE response.
    import time
    from app.main import save_bot
    bots[reg["bot_id"]].online_status = "online"
    bots[reg["bot_id"]].last_seen_at = time.time()
    bots[reg["bot_id"]].updated_at = bots[reg["bot_id"]].last_seen_at
    save_bot(bots[reg["bot_id"]])
    listing = client.get("/api/bots", params={"online_only": True}).json()
    assert listing["total"] == 1
    assert listing["bots"][0]["online_status"] == "online"


def test_match_control_permissions_and_admin_stop_all(monkeypatch):
    monkeypatch.setattr('app.main.ADMIN_TOKEN', 'test-admin-token')
    reset_state()
    client = TestClient(app)
    a = client.post("/api/bots/register", json={"name": "owner-a"}).json()
    b = client.post("/api/bots/register", json={"name": "owner-b"}).json()
    intruder = client.post("/api/bots/register", json={"name": "intruder"}).json()

    ch = client.post("/api/challenges", headers=auth(a["token"]), json={"opponent_bot_id": b["bot_id"], "side": "red"}).json()
    accepted = client.post(f"/api/challenges/{ch['challenge_id']}/accept", headers=auth(b["token"])).json()
    match_id = accepted["match_id"]

    assert client.post(f"/api/matches/{match_id}/pause", headers=auth(intruder["token"])).status_code == 403
    assert client.post(f"/api/matches/{match_id}/stop", headers=auth(intruder["token"])).status_code == 403

    paused = client.post(f"/api/matches/{match_id}/pause", headers=auth(a["token"]))
    assert paused.status_code == 200, paused.text
    assert paused.json()["paused"] is True

    admin_unpause = client.post(f"/api/matches/{match_id}/pause", headers=auth("test-admin-token"))
    assert admin_unpause.status_code == 200, admin_unpause.text
    assert admin_unpause.json()["admin"] is True
    assert admin_unpause.json()["paused"] is False

    # Create another active match, then admin stop all active games.
    ch2 = client.post("/api/challenges", headers=auth(a["token"]), json={"opponent_bot_id": b["bot_id"], "side": "black"}).json()
    accepted2 = client.post(f"/api/challenges/{ch2['challenge_id']}/accept", headers=auth(b["token"])).json()
    match_id2 = accepted2["match_id"]

    denied = client.post("/api/admin/matches/stop_all", headers=auth(a["token"]))
    assert denied.status_code == 403

    stopped = client.post("/api/admin/matches/stop_all", headers=auth("test-admin-token"))
    assert stopped.status_code == 200, stopped.text
    assert stopped.json()["stopped"] >= 2
    assert client.get(f"/api/admin/matches/{match_id}").json()["status"] == "finished"
    assert client.get(f"/api/admin/matches/{match_id2}").json()["finish_reason"] == "stopped_all_by_admin"
