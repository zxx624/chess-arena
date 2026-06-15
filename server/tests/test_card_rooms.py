import os
import sqlite3
import tempfile
import uuid
from pathlib import Path

os.environ.setdefault("CHESS_ARENA_DB", os.path.join(tempfile.gettempdir(), "chess-arena-test.db"))

from fastapi.testclient import TestClient

import app.main as main_module
from app import card_rooms
from app.games import doudizhu
from app.main import app, doudizhu_demo_rooms, load_state_from_db


def reset_state():
    db = os.path.join(tempfile.gettempdir(), f"chess-arena-test-cardroom-{uuid.uuid4().hex}.db")
    os.environ["CHESS_ARENA_DB"] = db
    main_module.DB_PATH = Path(db)
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(db + suffix)
        except FileNotFoundError:
            pass
    doudizhu_demo_rooms.clear()
    card_rooms.card_rooms.clear()
    load_state_from_db()
    return db


def db_count(db: str, table: str, where: str = "", params: tuple = ()) -> int:
    with sqlite3.connect(db) as conn:
        sql = f"SELECT COUNT(*) FROM {table}"
        if where:
            sql += f" WHERE {where}"
        return int(conn.execute(sql, params).fetchone()[0])


def db_room_state(db: str, room_id: str) -> str:
    with sqlite3.connect(db) as conn:
        row = conn.execute("SELECT state_json FROM card_rooms WHERE id = ?", (room_id,)).fetchone()
    assert row is not None
    return row[0]


def test_card_room_create_and_get_persists_to_db():
    db = reset_state()
    client = TestClient(app)

    resp = client.post(
        "/api/card-rooms",
        json={"game": "doudizhu", "players": ["seat0", "seat1", "seat2"], "seed": 624, "landlord_index": 2},
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    room_id = payload["room_id"]
    assert room_id.startswith("cardroom_")
    assert payload["game"] == "doudizhu"

    state = payload["state"]
    assert state["room_id"] == room_id
    assert state["game"] == "doudizhu"
    assert state["phase"] == "playing"
    assert state["status"] == "active"
    assert state["landlord_seat"] == "seat2"
    assert state["current_seat"] == "seat2"
    assert [seat["id"] for seat in state["seats"]] == ["seat0", "seat1", "seat2"]
    assert state["hands_count"] == {"seat0": 17, "seat1": 17, "seat2": 20}
    assert len(state["bottom_cards"]) == 3
    assert state["pass_count"] == 0
    assert state["action_history"] == []
    assert db_count(db, "card_rooms", "id = ?", (room_id,)) == 1

    fetched = client.get(f"/api/card-rooms/{room_id}")
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["state"]["hands_count"] == state["hands_count"]


def test_card_room_step_and_auto_run_persist_state_and_actions():
    db = reset_state()
    client = TestClient(app)
    room_id = client.post("/api/card-rooms", json={"game": "doudizhu", "seed": 42}).json()["room_id"]
    before_state = db_room_state(db, room_id)

    stepped = client.post(f"/api/card-rooms/{room_id}/step")
    assert stepped.status_code == 200, stepped.text
    step_payload = stepped.json()
    assert step_payload["room_id"] == room_id
    assert step_payload["game"] == "doudizhu"
    assert step_payload["move"]["player"] == "seat0"
    assert step_payload["finished"] is False
    assert step_payload["winner"] is None
    assert len(step_payload["state"]["action_history"]) == 1
    assert db_room_state(db, room_id) != before_state
    assert db_count(db, "card_room_actions", "room_id = ?", (room_id,)) == 1

    auto = client.post(f"/api/card-rooms/{room_id}/auto-run", json={"max_steps": 20})
    assert auto.status_code == 200, auto.text
    auto_payload = auto.json()
    assert auto_payload["room_id"] == room_id
    assert auto_payload["game"] == "doudizhu"
    assert 1 <= auto_payload["steps_run"] <= 20
    assert len(auto_payload["moves"]) == auto_payload["steps_run"]
    assert auto_payload["finished"] == (auto_payload["state"]["phase"] == "finished")
    assert auto_payload["winner"] == auto_payload["state"].get("winner")
    if auto_payload["finished"]:
        assert auto_payload["winner"]
    assert len(auto_payload["state"]["action_history"]) >= 2
    assert db_count(db, "card_room_actions", "room_id = ?", (room_id,)) == 1 + auto_payload["steps_run"]


def test_card_room_spectator_exposes_all_hands_but_seat_view_stays_private():
    reset_state()
    client = TestClient(app)
    room_id = client.post(
        "/api/card-rooms",
        json={"game": "doudizhu", "players": ["seat0", "seat1", "seat2"], "seed": 624, "landlord_index": 2},
    ).json()["room_id"]

    spectator = client.get(f"/api/card-rooms/{room_id}/spectator")
    assert spectator.status_code == 200, spectator.text
    spec = spectator.json()
    assert spec["room_id"] == room_id
    assert spec["game"] == "doudizhu"
    assert spec["phase"] == "playing"
    assert spec["current_seat"] == "seat2"
    assert spec["current_player"] == "seat2"
    assert len(spec["players"]) == 3
    for player in spec["players"]:
        assert set(["seat", "seat_id", "role", "is_landlord", "is_current", "hand", "hand_count"]).issubset(player)
        assert isinstance(player["hand"], list)
        assert player["hand"]
        assert player["hand_count"] == len(player["hand"])
    assert {p["seat_id"]: p["hand_count"] for p in spec["players"]} == {"seat0": 17, "seat1": 17, "seat2": 20}
    assert len(spec["bottom_cards"]) == 3
    assert spec["recent_history"] == []

    view = client.get(f"/api/card-rooms/{room_id}/view", params={"seat": 0})
    assert view.status_code == 200, view.text
    seat_view = view.json()
    assert seat_view["my_seat"] == 0
    assert len(seat_view["my_hand"]) == 17
    assert "hands" not in seat_view
    assert "hand" not in seat_view
    assert all("hand" not in player and "cards" not in player for player in seat_view["players"])
    assert any(player["seat"] == 1 and player["hand_count"] == 17 for player in seat_view["players"])
    assert any(player["seat"] == 2 and player["hand_count"] == 20 for player in seat_view["players"])


def test_card_room_list_returns_recent_rooms_and_survives_cache_clear():
    reset_state()
    client = TestClient(app)
    first = client.post("/api/card-rooms", json={"game": "doudizhu", "seed": 11, "landlord_index": 0}).json()["room_id"]
    second = client.post("/api/card-rooms", json={"game": "doudizhu", "seed": 12, "landlord_index": 1}).json()["room_id"]
    client.post(f"/api/card-rooms/{first}/step")

    listed = client.get("/api/card-rooms")
    assert listed.status_code == 200, listed.text
    payload = listed.json()
    assert payload["total"] == 2
    ids = [room["room_id"] for room in payload["rooms"]]
    assert first in ids
    assert second in ids
    first_summary = next(room for room in payload["rooms"] if room["room_id"] == first)
    assert first_summary["game"] == "doudizhu"
    assert first_summary["phase"] in {"playing", "finished"}
    assert first_summary["landlord_seat"] == "seat0"
    assert "created_at" in first_summary and "updated_at" in first_summary

    card_rooms.card_rooms.clear()
    restored_spec = client.get(f"/api/card-rooms/{second}/spectator")
    assert restored_spec.status_code == 200, restored_spec.text
    assert len(restored_spec.json()["players"]) == 3
    restored_list = client.get("/api/card-rooms", params={"limit": 1})
    assert restored_list.status_code == 200, restored_list.text
    assert restored_list.json()["total"] == 2
    assert len(restored_list.json()["rooms"]) == 1


def test_card_room_get_survives_cache_clear():
    reset_state()
    client = TestClient(app)
    room_id = client.post("/api/card-rooms", json={"game": "doudizhu", "seed": 99}).json()["room_id"]
    client.post(f"/api/card-rooms/{room_id}/auto-run", json={"max_steps": 3})

    card_rooms.card_rooms.clear()
    fetched = client.get(f"/api/card-rooms/{room_id}")
    assert fetched.status_code == 200, fetched.text
    payload = fetched.json()
    assert payload["room_id"] == room_id
    assert payload["state"]["room_id"] == room_id
    assert len(payload["state"]["action_history"]) == 3
    assert room_id in card_rooms.card_rooms


def test_card_room_validation_and_not_found():
    reset_state()
    client = TestClient(app)

    assert client.get("/api/card-rooms/not-found").status_code == 404
    assert client.post("/api/card-rooms/not-found/step").status_code == 404
    assert client.post("/api/card-rooms/not-found/auto-run", json={"max_steps": 1}).status_code == 404

    bad_game = client.post("/api/card-rooms", json={"game": "poker", "players": ["a", "b", "c"]})
    assert bad_game.status_code == 400

    duplicate = client.post("/api/card-rooms", json={"game": "doudizhu", "players": ["a", "a", "b"]})
    assert duplicate.status_code == 400


def test_card_room_does_not_break_legacy_doudizhu_demo_api():
    reset_state()
    client = TestClient(app)

    legacy = client.post("/api/cards/doudizhu/demo/new", json={"players": ["seat0", "seat1", "seat2"], "seed": 7})
    assert legacy.status_code == 200, legacy.text
    legacy_room_id = legacy.json()["room_id"]
    assert legacy_room_id in doudizhu_demo_rooms
    assert legacy_room_id not in card_rooms.card_rooms

    room = client.post("/api/card-rooms", json={"game": "doudizhu", "seed": 7})
    assert room.status_code == 200, room.text
    card_room_id = room.json()["room_id"]
    assert card_room_id in card_rooms.card_rooms
    assert card_room_id not in doudizhu_demo_rooms


def _replace_room_state(room_id: str, state: dict) -> None:
    room = card_rooms._room_or_error(room_id)
    room.raw_state = doudizhu.dumps_state(state)
    with card_rooms.db_connect() as conn:
        card_rooms._upsert_room(conn, room)


def _custom_playing_state(*, turn_index: int = 0) -> dict:
    players = ["seat0", "seat1", "seat2"]
    return {
        "game": "doudizhu",
        "players": players,
        "hands": {
            "seat0": doudizhu.sort_cards(["3S", "3H", "4S", "4H", "5S", "5H", "6S"]),
            "seat1": doudizhu.sort_cards(["7S", "7H", "8S", "8H", "9S", "9H", "10S"]),
            "seat2": doudizhu.sort_cards(["JS", "JH", "QS", "QH", "KS", "KH", "AS"]),
        },
        "bottom": ["BJ", "RJ", "2S"],
        "turn_index": turn_index,
        "turn_player": players[turn_index],
        "landlord": "seat0",
        "phase": "playing",
        "last_play": None,
        "passes": 0,
        "history": [],
        "winner": None,
    }


def test_card_room_llm_action_play_pass_view_and_legal_actions():
    db = reset_state()
    client = TestClient(app)
    room_id = client.post("/api/card-rooms", json={"game": "doudizhu", "seed": 123}).json()["room_id"]

    view = client.get(f"/api/card-rooms/{room_id}/view", params={"seat": 0})
    assert view.status_code == 200, view.text
    view_payload = view.json()
    assert view_payload["my_seat"] == 0
    assert view_payload["my_hand"]
    assert "hands" not in view_payload
    assert all("hand" not in p and "cards" not in p for p in view_payload["players"])
    assert any(p["seat"] == 1 and p["hand_count"] > 0 for p in view_payload["players"])

    legal = client.get(f"/api/card-rooms/{room_id}/legal-actions", params={"seat": 0})
    assert legal.status_code == 200, legal.text
    legal_payload = legal.json()
    assert legal_payload["seat"] == 0
    assert "candidate_groups" in legal_payload
    assert set(["singles", "pairs", "triples", "triple_with_single", "triple_with_pair", "straights", "consecutive_pairs", "bombs", "rocket"]).issubset(legal_payload["candidate_groups"])
    first_single = legal_payload["candidate_groups"]["singles"][0]

    acted = client.post(
        f"/api/card-rooms/{room_id}/actions",
        json={"seat": 0, "action": "play", "cards": [first_single], "source": "manual_llm_demo", "reason": "先出一张小牌"},
    )
    assert acted.status_code == 200, acted.text
    payload = acted.json()
    assert payload["move"]["action"] == "play"
    assert payload["move"]["source"] == "manual_llm_demo"
    assert payload["move"]["reason"] == "先出一张小牌"
    assert payload["state"]["action_history"][-1]["source"] == "manual_llm_demo"
    assert db_count(db, "card_room_actions", "room_id = ?", (room_id,)) == 1

    passed = client.post(f"/api/card-rooms/{room_id}/actions", json={"seat": 1, "action": "pass", "cards": [], "source": "llm"})
    assert passed.status_code == 200, passed.text
    assert passed.json()["move"]["action"] == "pass"
    assert db_count(db, "card_room_actions", "room_id = ?", (room_id,)) == 2

    card_rooms.card_rooms.clear()
    fetched = client.get(f"/api/card-rooms/{room_id}")
    assert fetched.status_code == 200, fetched.text
    assert len(fetched.json()["state"]["action_history"]) == 2


def test_card_room_llm_action_rejects_illegal_actions_structured():
    reset_state()
    client = TestClient(app)
    room_id = client.post("/api/card-rooms", json={"game": "doudizhu", "seed": 321}).json()["room_id"]

    seat1_view = client.get(f"/api/card-rooms/{room_id}/view", params={"seat": 1}).json()
    not_turn = client.post(
        f"/api/card-rooms/{room_id}/actions",
        json={"seat": 1, "action": "play", "cards": [seat1_view["my_hand"][0]], "source": "llm"},
    )
    assert not_turn.status_code == 400
    assert not_turn.json()["detail"]["code"] == "not_your_turn"

    missing = next(card for card in doudizhu.deck() if card not in client.get(f"/api/card-rooms/{room_id}/view", params={"seat": 0}).json()["my_hand"])
    bad_card = client.post(f"/api/card-rooms/{room_id}/actions", json={"seat": 0, "action": "play", "cards": [missing]})
    assert bad_card.status_code == 400
    assert bad_card.json()["detail"]["code"] == "card_not_in_hand"


def test_card_room_llm_action_rejects_single_against_pair_and_unsupported_pattern():
    reset_state()
    client = TestClient(app)
    room_id = client.post("/api/card-rooms", json={"game": "doudizhu", "seed": 888}).json()["room_id"]
    state = _custom_playing_state(turn_index=1)
    state["last_play"] = {"player": "seat0", "action": "play", "cards": ["3S", "3H"], "pattern": {"type": "pair", "rank": doudizhu.card_rank("3S"), "length": 2}}
    state["history"] = [state["last_play"]]
    _replace_room_state(room_id, state)

    single = client.post(f"/api/card-rooms/{room_id}/actions", json={"seat": 1, "action": "play", "cards": ["7S"]})
    assert single.status_code == 400
    assert single.json()["detail"]["code"] == "cannot_beat_last_play"
    assert "legal_hint" in single.json()["detail"]

    state = _custom_playing_state(turn_index=0)
    _replace_room_state(room_id, state)
    unsupported = client.post(f"/api/card-rooms/{room_id}/actions", json={"seat": "seat0", "action": "play", "cards": ["3S", "3H", "4S", "4H"]})
    assert unsupported.status_code == 400
    detail = unsupported.json()["detail"]
    assert detail["code"] == "unsupported_pattern"
    assert "单张" in detail["legal_hint"]



def test_card_room_seat_tokens_isolate_private_views_and_actions(monkeypatch):
    reset_state()
    monkeypatch.setenv("CARDROOM_REQUIRE_SEAT_TOKEN", "1")
    client = TestClient(app)
    created = client.post("/api/card-rooms", json={"game": "doudizhu", "seed": 246}).json()
    room_id = created["room_id"]
    tokens = created["seat_tokens"]
    assert set(tokens) == {"seat0", "seat1", "seat2"}

    missing = client.get(f"/api/card-rooms/{room_id}/view", params={"seat": 0})
    assert missing.status_code == 403
    bad = client.get(f"/api/card-rooms/{room_id}/view", params={"seat": 0, "token": tokens["seat1"]})
    assert bad.status_code == 403

    ok = client.get(f"/api/card-rooms/{room_id}/view", params={"seat": 0, "token": tokens["seat0"]})
    assert ok.status_code == 200, ok.text
    payload = ok.json()
    assert payload["my_seat"] == 0
    assert "hands" not in payload
    assert all("hand" not in player for player in payload["players"])

    legal = client.get(f"/api/card-rooms/{room_id}/legal-actions", params={"seat": 0, "token": tokens["seat0"]})
    assert legal.status_code == 200, legal.text
    first_single = legal.json()["candidate_groups"]["singles"][0]
    wrong_action = client.post(
        f"/api/card-rooms/{room_id}/actions",
        json={"seat": 0, "token": tokens["seat1"], "action": "play", "cards": [first_single]},
    )
    assert wrong_action.status_code == 403
    right_action = client.post(
        f"/api/card-rooms/{room_id}/actions",
        json={"seat": 0, "token": tokens["seat0"], "action": "play", "cards": [first_single], "source": "bot"},
    )
    assert right_action.status_code == 200, right_action.text


def test_card_room_fair_bot_runner_uses_private_contract_and_rotates_three_seats():
    reset_state()
    client = TestClient(app)
    room_id = client.post("/api/card-rooms", json={"game": "doudizhu", "seed": 135, "landlord_index": 0}).json()["room_id"]

    actors = []
    for _ in range(3):
        stepped = client.post(f"/api/card-rooms/{room_id}/step")
        assert stepped.status_code == 200, stepped.text
        actors.append(stepped.json()["move"]["player"])

    assert actors == ["seat0", "seat1", "seat2"]
    spec = client.get(f"/api/card-rooms/{room_id}/spectator").json()
    assert len(spec["recent_history"]) == 3
    assert all(move.get("source") == "fair_bot" for move in spec["recent_history"])


def test_choose_fair_bot_action_does_not_require_spectator_hands():
    view = {"my_hand": ["3S", "4S"], "players": [{"seat": 1, "hand_count": 17}]}
    legal = {
        "is_my_turn": True,
        "can_pass": False,
        "candidate_groups": {
            "singles": ["3S"],
            "pairs": [],
            "triples": [],
            "triple_with_single": [],
            "triple_with_pair": [],
            "straights": [],
            "consecutive_pairs": [],
            "bombs": [],
            "rocket": False,
        },
    }
    assert card_rooms.choose_fair_bot_action(view, legal) == {"action": "play", "cards": ["3S"], "reason": "fair_bot_min_singles"}



def test_card_room_pool_initializes_five_slots_and_auto_starts_full_slot(monkeypatch):
    reset_state()
    monkeypatch.delenv("CARDROOM_REQUIRE_SEAT_TOKEN", raising=False)
    client = TestClient(app)

    initial = client.get("/api/card-rooms/pool")
    assert initial.status_code == 200, initial.text
    payload = initial.json()
    assert payload["slot_count"] == 5
    assert payload["capacity"] == 3
    assert [slot["slot"] for slot in payload["slots"]] == [1, 2, 3, 4, 5]
    assert all(slot["status"] == "waiting" for slot in payload["slots"])
    assert all(slot["occupied"] == 0 for slot in payload["slots"])

    first = client.post(
        "/api/card-rooms/pool/1/join",
        json={"controller_type": "web", "controller_id": "u1", "display_name": "玩家1"},
    )
    assert first.status_code == 200, first.text
    first_payload = first.json()
    assert first_payload["joined"] is True
    assert first_payload["auto_started"] is False
    assert first_payload["seat"]["seat"] == 0
    assert first_payload["seat_token"].startswith("seat_")
    assert first_payload["room_id"] is None

    duplicate = client.post(
        "/api/card-rooms/pool/1/join",
        json={"controller_type": "web", "controller_id": "u1", "display_name": "玩家1"},
    )
    assert duplicate.status_code == 200, duplicate.text
    assert duplicate.json()["already_joined"] is True
    assert duplicate.json()["seat"]["seat"] == 0

    second = client.post(
        "/api/card-rooms/pool/1/join",
        json={"controller_type": "web", "controller_id": "u2", "display_name": "玩家2"},
    )
    assert second.status_code == 200, second.text
    assert second.json()["slot"]["occupied"] == 2

    third = client.post(
        "/api/card-rooms/pool/1/join",
        json={"controller_type": "astrbot", "controller_id": "bot-a", "display_name": "咕噜"},
    )
    assert third.status_code == 200, third.text
    third_payload = third.json()
    assert third_payload["joined"] is True
    assert third_payload["auto_started"] is True
    assert third_payload["room_id"].startswith("cardroom_")
    assert third_payload["slot"]["status"] == "playing"
    assert third_payload["slot"]["occupied"] == 3
    assert "token" not in third_payload["slot"]["seats"][0]
    assert third_payload["seat"]["token"] == third_payload["seat_token"]

    room_id = third_payload["room_id"]
    listed = client.get("/api/card-rooms/pool")
    assert listed.status_code == 200, listed.text
    slot1 = listed.json()["slots"][0]
    assert slot1["room_id"] == room_id
    assert slot1["room_url"] == f"/doudizhu?room_id={room_id}"
    assert slot1["can_join"] is False
    assert all("token" not in seat for seat in slot1["seats"])

    view = client.get(f"/api/card-rooms/{room_id}/view", params={"seat": 2, "token": third_payload["seat_token"]})
    assert view.status_code == 200, view.text
    assert view.json()["my_seat"] == 2
    assert "hands" not in view.json()

    extra = client.post(
        "/api/card-rooms/pool/1/join",
        json={"controller_type": "web", "controller_id": "u4", "display_name": "玩家4"},
    )
    assert extra.status_code == 400
    assert extra.json()["detail"]["code"] == "slot_not_waiting"

    reset = client.post("/api/card-rooms/pool/1/reset")
    assert reset.status_code == 200, reset.text
    reset_slot = reset.json()["slot"]
    assert reset_slot["status"] == "waiting"
    assert reset_slot["room_id"] is None
    assert reset_slot["occupied"] == 0


def test_card_room_pool_leave_before_start_reindexes_waiting_seats():
    reset_state()
    client = TestClient(app)
    for idx in range(2):
        resp = client.post(
            "/api/card-rooms/pool/2/join",
            json={"controller_type": "web", "controller_id": f"u{idx}", "display_name": f"玩家{idx}"},
        )
        assert resp.status_code == 200, resp.text

    left = client.post(
        "/api/card-rooms/pool/2/leave",
        json={"controller_type": "web", "controller_id": "u0", "display_name": "玩家0"},
    )
    assert left.status_code == 200, left.text
    slot = left.json()["slot"]
    assert slot["occupied"] == 1
    assert slot["seats"][0]["controller_id"] == "u1"
    assert slot["seats"][0]["seat"] == 0
    assert slot["seats"][0]["seat_id"] == "seat0"



def test_card_room_prompt_packet_uses_private_view_and_legal_actions_only():
    reset_state()
    client = TestClient(app)
    room_id = client.post("/api/card-rooms", json={"game": "doudizhu", "seed": 909, "landlord_index": 0}).json()["room_id"]

    prompt = client.get(f"/api/card-rooms/{room_id}/prompt", params={"seat": 0})
    assert prompt.status_code == 200, prompt.text
    payload = prompt.json()
    assert payload["contract"] == ["private_view", "legal_actions"]
    assert "private_view" in payload
    assert "legal_actions" in payload
    assert payload["private_view"]["my_hand"]
    assert "hands" not in payload["private_view"]
    assert all("hand" not in player and "cards" not in player for player in payload["private_view"]["players"])
    assert "candidate_groups" in payload["legal_actions"]
    prompt_text = payload["prompt"]
    assert "禁止猜测或使用对手手牌" in prompt_text
    assert "spectator" not in prompt_text.lower()


def test_card_room_review_action_rejects_illegal_without_applying():
    db = reset_state()
    client = TestClient(app)
    room_id = client.post("/api/card-rooms", json={"game": "doudizhu", "seed": 910, "landlord_index": 0}).json()["room_id"]
    before = db_room_state(db, room_id)

    reviewed = client.post(
        f"/api/card-rooms/{room_id}/review-action",
        json={"seat": 0, "action": "play", "cards": ["BJ", "RJ"], "speech": "王炸走你", "reason": "非法试探"},
    )
    assert reviewed.status_code == 200, reviewed.text
    payload = reviewed.json()
    assert payload["accepted"] is False
    assert payload["failure"]["code"] == "illegal_cards"
    assert payload["failure"]["legal_actions"]["candidate_groups"]
    assert db_room_state(db, room_id) == before
    assert db_count(db, "card_room_actions", "room_id = ?", (room_id,)) == 0


def test_card_room_prompt_decision_accepts_legal_candidate_and_records_speech():
    db = reset_state()
    client = TestClient(app)
    room_id = client.post("/api/card-rooms", json={"game": "doudizhu", "seed": 911, "landlord_index": 0}).json()["room_id"]
    legal = client.get(f"/api/card-rooms/{room_id}/legal-actions", params={"seat": 0}).json()
    first_single = legal["candidate_groups"]["singles"][0]

    decided = client.post(
        f"/api/card-rooms/{room_id}/prompt-decision",
        json={
            "seat": 0,
            "max_retries": 5,
            "candidates": [{"action": "play", "cards": [first_single], "speech": "先探一张。", "reason": "最小单张"}],
        },
    )
    assert decided.status_code == 200, decided.text
    payload = decided.json()
    assert payload["prompt_review"]["accepted"] is True
    assert payload["prompt_review"]["fallback"] is False
    assert payload["prompt_review"]["retries"] == 0
    assert payload["move"]["source"] == "astrbot_prompt"
    assert payload["move"]["speech"] == "先探一张。"
    assert payload["state"]["action_history"][-1]["speech"] == "先探一张。"
    assert db_count(db, "card_room_actions", "room_id = ?", (room_id,)) == 1


def test_card_room_prompt_decision_retries_then_builtin_fallback_pass_first():
    db = reset_state()
    client = TestClient(app)
    room_id = client.post("/api/card-rooms", json={"game": "doudizhu", "seed": 912, "landlord_index": 0}).json()["room_id"]
    state = _custom_playing_state(turn_index=1)
    state["last_play"] = {"player": "seat0", "action": "play", "cards": ["3S"], "pattern": {"type": "single", "rank": doudizhu.card_rank("3S"), "length": 1}}
    state["history"] = [state["last_play"]]
    _replace_room_state(room_id, state)

    decided = client.post(
        f"/api/card-rooms/{room_id}/prompt-decision",
        json={
            "seat": 1,
            "max_retries": 2,
            "candidates": [
                {"action": "play", "cards": ["7S", "7H"], "speech": "乱出对子", "reason": "非法1"},
                {"action": "play", "cards": ["9S", "9H"], "speech": "继续乱出", "reason": "非法2"},
                {"action": "play", "cards": ["10S"], "speech": "不应处理到第三次", "reason": "超过重试"},
            ],
        },
    )
    assert decided.status_code == 200, decided.text
    payload = decided.json()
    assert payload["prompt_review"]["accepted"] is False
    assert payload["prompt_review"]["fallback"] is True
    assert payload["prompt_review"]["retries"] == 2
    assert [failure["attempt"] for failure in payload["prompt_review"]["failures"]] == [1, 2]
    assert payload["move"]["source"] == "builtin_fallback"
    assert payload["move"]["action"] == "pass"
    assert payload["move"]["retries"] == 2
    assert payload["move"]["speech"] == "先不出。"
    assert db_count(db, "card_room_actions", "room_id = ?", (room_id,)) == 1


def test_card_room_prompt_decision_fallback_smallest_play_when_pass_illegal():
    reset_state()
    client = TestClient(app)
    room_id = client.post("/api/card-rooms", json={"game": "doudizhu", "seed": 913, "landlord_index": 0}).json()["room_id"]
    legal = client.get(f"/api/card-rooms/{room_id}/legal-actions", params={"seat": 0}).json()
    expected = legal["candidate_groups"]["singles"][0]

    decided = client.post(
        f"/api/card-rooms/{room_id}/prompt-decision",
        json={"seat": 0, "max_retries": 1, "candidates": [{"action": "pass", "cards": [], "reason": "新轮次不能pass"}]},
    )
    assert decided.status_code == 200, decided.text
    payload = decided.json()
    assert payload["prompt_review"]["fallback"] is True
    assert payload["prompt_review"]["failures"][0]["code"] == "cannot_pass"
    assert payload["move"]["source"] == "builtin_fallback"
    assert payload["move"]["action"] == "play"
    assert payload["move"]["cards"] == [expected]
