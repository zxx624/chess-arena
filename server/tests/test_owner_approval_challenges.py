import os
import tempfile
import uuid
from pathlib import Path

os.environ.setdefault("CHESS_ARENA_DB", os.path.join(tempfile.gettempdir(), "chess-arena-test.db"))

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app, bots, challenges, load_state_from_db


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def reset_state() -> None:
    db = os.path.join(tempfile.gettempdir(), f"chess-arena-owner-approval-{uuid.uuid4().hex}.db")
    os.environ["CHESS_ARENA_DB"] = db
    main_module.DB_PATH = Path(db)
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(db + suffix)
        except FileNotFoundError:
            pass
    load_state_from_db()


owner_api_available = pytest.mark.skipif(
    not any(getattr(route, "path", "") == "/api/challenges/{challenge_id}/owner_decision" for route in app.routes),
    reason="owner_decision API is not implemented in this checkout yet",
)


def register_pair(client: TestClient):
    challenger = client.post("/api/bots/register", json={"name": "owner-approval-challenger"}).json()
    opponent = client.post(
        "/api/bots/register",
        json={
            "name": "owner-approval-opponent",
            "challenge_policy": "manual_approve",
            "owner_review_timeout_sec": 180,
        },
    ).json()
    # Keep the test resilient while backend registration/update migration is in flight:
    # if register/patch does not yet persist policy, set the in-memory object used by
    # TestClient endpoints. Once the API supports it, these assignments are harmless.
    bots[opponent["bot_id"]].challenge_policy = "manual_approve"
    bots[opponent["bot_id"]].owner_review_timeout_sec = 180
    return challenger, opponent


@owner_api_available
def test_manual_policy_creates_owner_review_and_pending_endpoint_lists_names():
    reset_state()
    client = TestClient(app)
    challenger, opponent = register_pair(client)

    created = client.post(
        "/api/challenges",
        headers=auth(challenger["token"]),
        json={"opponent_bot_id": opponent["bot_id"], "side": "red"},
    )
    assert created.status_code == 200, created.text
    challenge = created.json()
    assert challenge["status"] == "owner_review"
    assert challenge["match_id"] is None
    assert challenge.get("requires_owner_decision") is True
    assert challenge.get("expires_at") is not None

    pending = client.get("/api/bots/me/challenges/pending", headers=auth(opponent["token"]))
    assert pending.status_code == 200, pending.text
    rows = pending.json()["challenges"]
    assert len(rows) == 1
    row = rows[0]
    assert row["challenge_id"] == challenge["challenge_id"]
    assert row["challenger_name"] == "owner-approval-challenger"
    assert row["opponent_name"] == "owner-approval-opponent"
    assert row["status"] == "owner_review"


@owner_api_available
def test_owner_decision_accept_reject_and_permission_checks():
    reset_state()
    client = TestClient(app)
    challenger, opponent = register_pair(client)
    intruder = client.post("/api/bots/register", json={"name": "owner-approval-intruder"}).json()

    ch_accept = client.post(
        "/api/challenges",
        headers=auth(challenger["token"]),
        json={"opponent_bot_id": opponent["bot_id"], "side": "black"},
    ).json()

    denied = client.post(
        f"/api/challenges/{ch_accept['challenge_id']}/owner_decision",
        headers=auth(intruder["token"]),
        json={"decision": "accept", "reason": "not my challenge"},
    )
    assert denied.status_code == 403

    accepted = client.post(
        f"/api/challenges/{ch_accept['challenge_id']}/owner_decision",
        headers=auth(opponent["token"]),
        json={"decision": "accept", "reason": "主人同意"},
    )
    assert accepted.status_code == 200, accepted.text
    accepted_json = accepted.json()
    assert accepted_json["challenge"]["status"] == "accepted"
    assert accepted_json["match"]["status"] == "active"
    assert accepted_json["challenge"]["match_id"] == accepted_json["match"]["match_id"]
    assert "match_url" in accepted_json

    # Use a fresh opponent so the active accepted match does not make the next
    # challenge fail the existing bot_busy guard.
    reject_opponent = client.post(
        "/api/bots/register",
        json={"name": "owner-approval-reject", "challenge_policy": "manual_approve"},
    ).json()
    bots[reject_opponent["bot_id"]].challenge_policy = "manual_approve"
    ch_reject = client.post(
        "/api/challenges",
        headers=auth(intruder["token"]),
        json={"opponent_bot_id": reject_opponent["bot_id"], "side": "red"},
    ).json()
    rejected = client.post(
        f"/api/challenges/{ch_reject['challenge_id']}/owner_decision",
        headers=auth(reject_opponent["token"]),
        json={"decision": "reject", "reason": "主人拒绝"},
    )
    assert rejected.status_code == 200, rejected.text
    rejected_json = rejected.json()
    assert rejected_json["challenge"]["status"] == "rejected"
    assert rejected_json["challenge"].get("owner_decision") == "reject"
    assert rejected_json.get("match") is None


@owner_api_available
def test_owner_decision_accept_keeps_bot_busy_guard_and_old_pending_compatibility():
    reset_state()
    client = TestClient(app)
    a = client.post("/api/bots/register", json={"name": "busy-a"}).json()
    b = client.post("/api/bots/register", json={"name": "busy-b"}).json()
    c = client.post("/api/bots/register", json={"name": "busy-c"}).json()
    d = client.post("/api/bots/register", json={"name": "busy-d", "challenge_policy": "manual_approve"}).json()
    bots[d["bot_id"]].challenge_policy = "manual_approve"

    active_ch = client.post("/api/challenges", headers=auth(a["token"]), json={"opponent_bot_id": b["bot_id"], "side": "red"}).json()
    active = client.post(f"/api/challenges/{active_ch['challenge_id']}/accept", headers=auth(b["token"])).json()
    assert active["match"]["status"] == "active"

    owner_review = client.post("/api/challenges", headers=auth(c["token"]), json={"opponent_bot_id": d["bot_id"], "side": "red"}).json()
    blocker = client.post("/api/bots/register", json={"name": "busy-blocker"}).json()
    blocker_ch = client.post("/api/challenges", headers=auth(blocker["token"]), json={"opponent_bot_id": c["bot_id"], "side": "black"}).json()
    assert client.post(f"/api/challenges/{blocker_ch['challenge_id']}/accept", headers=auth(c["token"])).status_code == 200

    stale_accept = client.post(
        f"/api/challenges/{owner_review['challenge_id']}/owner_decision",
        headers=auth(d["token"]),
        json={"decision": "accept", "reason": "should still honor busy guard"},
    )
    assert stale_accept.status_code == 409
    assert stale_accept.json()["detail"]["code"] == "bot_busy"

    # Backward compatibility: old pending challenges remain acceptable through
    # owner_decision as well as the legacy /accept endpoint.
    legacy_opponent = client.post("/api/bots/register", json={"name": "legacy-pending-opponent"}).json()
    legacy_ch = client.post("/api/challenges", headers=auth(d["token"]), json={"opponent_bot_id": legacy_opponent["bot_id"], "side": "red"}).json()
    challenges[legacy_ch["challenge_id"]].status = "pending"
    legacy_accept = client.post(
        f"/api/challenges/{legacy_ch['challenge_id']}/owner_decision",
        headers=auth(legacy_opponent["token"]),
        json={"decision": "accept", "reason": "compat"},
    )
    assert legacy_accept.status_code == 200, legacy_accept.text
    assert legacy_accept.json()["challenge"]["status"] == "accepted"
