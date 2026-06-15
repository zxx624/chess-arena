from fastapi.testclient import TestClient

from app.main import app, ensure_bot_game_profile
from test_api import reset_state


def _register(client: TestClient, name: str = "token-ddz-bot") -> dict:
    res = client.post("/api/bots/register", json={"name": name, "game": "xiangqi"})
    assert res.status_code == 200, res.text
    return res.json()


def test_token_join_requires_existing_enabled_doudizhu_profile():
    reset_state()
    client = TestClient(app)

    missing = client.post("/api/card-rooms/pool/1/join-token", json={"token": ""})
    assert missing.status_code == 422

    invalid = client.post("/api/card-rooms/pool/1/join-token", json={"token": "not-a-real-token"})
    assert invalid.status_code == 401
    assert "Token" in invalid.text or "token" in invalid.text

    bot = _register(client)
    rejected = client.post("/api/card-rooms/pool/1/join-token", json={"token": bot["token"]})
    assert rejected.status_code == 403
    assert "斗地主" in rejected.text or "doudizhu" in rejected.text


def test_token_join_uses_bot_account_and_does_not_leak_raw_bot_token():
    reset_state()
    client = TestClient(app)
    bot = _register(client, "Token 入座 Bot")
    ensure_bot_game_profile(bot["bot_id"], "doudizhu")

    res = client.post(
        "/api/card-rooms/pool/1/join-token",
        json={"token": bot["token"], "display_name": "网页Bot"},
    )
    assert res.status_code == 200, res.text
    data = res.json()
    raw = res.text
    assert bot["token"] not in raw
    assert data["joined"] is True
    assert data["bot"]["bot_id"] == bot["bot_id"]
    assert data["bot"]["name"] == "Token 入座 Bot"
    assert "doudizhu" in data["bot"]["enabled_games"]
    assert data["seat_token"].startswith("seat_")
    assert "token" not in data["seat"]
    assert data["seat"]["controller_type"] == "bot_token"
    assert data["seat"]["controller_id"] == bot["bot_id"]
    assert data["seat"]["display_name"] == "网页Bot"

    pool = client.get("/api/card-rooms/pool")
    assert pool.status_code == 200, pool.text
    pool_text = pool.text
    assert bot["token"] not in pool_text
    slot = next(item for item in pool.json()["slots"] if item["slot"] == 1)
    assert slot["seats"][0]["controller_type"] == "bot_token"
    assert slot["seats"][0]["controller_id"] == bot["bot_id"]
    assert "token" not in slot["seats"][0]


def test_token_join_full_slot_error_stays_normal_and_token_leave_is_idempotent():
    reset_state()
    client = TestClient(app)
    bots = []
    for idx in range(4):
        bot = _register(client, f"ddz-{idx}")
        ensure_bot_game_profile(bot["bot_id"], "doudizhu")
        bots.append(bot)

    for bot in bots[:3]:
        res = client.post("/api/card-rooms/pool/2/join-token", json={"token": bot["token"]})
        assert res.status_code == 200, res.text
    full = client.post("/api/card-rooms/pool/2/join-token", json={"token": bots[3]["token"]})
    assert full.status_code == 400
    assert "slot" in full.text.lower() or "full" in full.text.lower() or "playing" in full.text.lower()
    assert bots[3]["token"] not in full.text

    left = client.post("/api/card-rooms/pool/2/leave-token", json={"token": bots[0]["token"]})
    assert left.status_code == 200, left.text
    assert bots[0]["token"] not in left.text
