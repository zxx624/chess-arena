"""Integration test skeleton for Chess Arena Protocol v0.1.

These tests assume a backend is already running at CHESS_ARENA_URL
(default: http://127.0.0.1:8787). They exercise the MVP flow:

1. Register two bots.
2. Connect both bots to SSE.
3. Bot A challenges Bot B.
4. Bot B receives challenge_received and accepts.
5. Both bots observe match_started.
6. The side to move receives your_turn with legal_moves.
7. Submit a legal UCCI move.
8. Observe move_made and next your_turn or match_finished.

Run:
    pip install pytest pytest-asyncio httpx
    pytest -q tests/test_match_flow.py

Optional env:
    CHESS_ARENA_URL=http://127.0.0.1:8787
    CHESS_ARENA_REQUIRE_BACKEND=1   # fail instead of skip if backend is down
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Optional

import httpx
import pytest

BASE_URL = os.environ.get("CHESS_ARENA_URL", "http://127.0.0.1:8787")
REQUIRE_BACKEND = os.environ.get("CHESS_ARENA_REQUIRE_BACKEND") == "1"

pytestmark = pytest.mark.asyncio


@dataclass
class Bot:
    id: str
    name: str
    token: str


class SSEParser:
    def __init__(self) -> None:
        self.event: Optional[str] = None
        self.data_lines: list[str] = []

    def feed_line(self, line: str) -> Optional[dict[str, str]]:
        line = line.rstrip("\r")
        if line == "":
            if not self.event and not self.data_lines:
                return None
            event = {"event": self.event or "message", "data": "\n".join(self.data_lines)}
            self.event = None
            self.data_lines = []
            return event
        if line.startswith(":"):
            return None
        field, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if field == "event":
            self.event = value
        elif field == "data":
            self.data_lines.append(value)
        return None


async def backend_available() -> bool:
    try:
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=1.0) as client:
            # /api/bots is protected, but any HTTP response proves the server is there.
            resp = await client.get("/api/bots")
            return resp.status_code in {200, 401, 403}
    except Exception:
        return False


@pytest.fixture(autouse=True)
def require_backend() -> None:
    try:
        resp = httpx.get(f"{BASE_URL}/health", timeout=1.0)
        available = resp.status_code in {200, 401, 403}
    except Exception:
        available = False
    if not available:
        msg = f"Chess Arena backend is not running at {BASE_URL}"
        if REQUIRE_BACKEND:
            pytest.fail(msg)
        pytest.skip(msg)


async def register_bot(client: httpx.AsyncClient, name: str) -> Bot:
    resp = await client.post(
        "/api/bots/register",
        json={
            "name": name,
            "capabilities": {"move_formats": ["ucci"], "variants": ["xiangqi"]},
        },
    )
    assert resp.status_code in {200, 201}, resp.text
    body = resp.json()
    bot_obj = body.get("bot") or body
    bot_id = bot_obj.get("id") or bot_obj.get("bot_id") or body.get("bot_id")
    assert bot_id and body.get("token"), body
    return Bot(id=bot_id, name=bot_obj.get("name", name), token=body["token"])


async def sse_stream(client: httpx.AsyncClient, token: str) -> AsyncIterator[dict[str, Any]]:
    parser = SSEParser()
    async with client.stream("GET", "/sse/bot", params={"token": token}, timeout=None) as resp:
        assert resp.status_code == 200, await resp.aread()
        async for line in resp.aiter_lines():
            evt = parser.feed_line(line)
            if not evt:
                continue
            raw = evt.get("data") or "{}"
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                data = {"raw": raw}
            yield {"event": evt.get("event"), "data": data}


class EventCollector:
    def __init__(self, client: httpx.AsyncClient, bot: Bot):
        self.client = client
        self.bot = bot
        self.events: list[dict[str, Any]] = []
        self._task: Optional[asyncio.Task[None]] = None

    async def __aenter__(self) -> "EventCollector":
        self._task = asyncio.create_task(self._run())
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _run(self) -> None:
        async for evt in sse_stream(self.client, self.bot.token):
            data = evt.get("data") or {}
            evt["type"] = data.get("type") or evt.get("event")
            self.events.append(evt)

    async def wait_for(self, event_type: str, timeout: float = 5.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        seen = 0
        while time.monotonic() < deadline:
            for evt in self.events[seen:]:
                if evt.get("type") == event_type:
                    return evt
            seen = len(self.events)
            await asyncio.sleep(0.05)
        raise AssertionError(f"timed out waiting for {event_type}; seen={[e.get('type') for e in self.events]}")

    async def wait_for_predicate(self, predicate, label: str, timeout: float = 5.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        seen = 0
        while time.monotonic() < deadline:
            for evt in self.events[seen:]:
                if predicate(evt):
                    return evt
            seen = len(self.events)
            await asyncio.sleep(0.05)
        raise AssertionError(f"timed out waiting for {label}; seen={[e.get('type') for e in self.events]}")


async def create_challenge(client: httpx.AsyncClient, challenger: Bot, opponent: Bot) -> str:
    resp = await client.post(
        "/api/challenges",
        headers={"Authorization": f"Bearer {challenger.token}"},
        json={
            "opponent_bot_id": opponent.id,
            "variant": "xiangqi",
            "initial_fen": None,
            "time_control": {"base_ms": 300000, "increment_ms": 0},
        },
    )
    assert resp.status_code in {200, 201}, resp.text
    body = resp.json()
    challenge = body.get("challenge") or body
    challenge_id = challenge.get("id") or challenge.get("challenge_id") or body.get("challenge_id")
    assert challenge_id, body
    return challenge_id


async def accept_challenge(client: httpx.AsyncClient, bot: Bot, challenge_id: str) -> dict[str, Any]:
    resp = await client.post(
        f"/api/challenges/{challenge_id}/accept",
        headers={"Authorization": f"Bearer {bot.token}"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def submit_move(client: httpx.AsyncClient, bot: Bot, match_id: str, move: str) -> dict[str, Any]:
    resp = await client.post(
        f"/api/matches/{match_id}/move",
        headers={"Authorization": f"Bearer {bot.token}"},
        json={"move": move, "format": "ucci"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_register_me_and_list_bots() -> None:
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as client:
        bot = await register_bot(client, f"pytest-me-{int(time.time())}")

        me = await client.get("/api/bots/me", headers={"Authorization": f"Bearer {bot.token}"})
        assert me.status_code == 200, me.text
        me_body = me.json()
        me_bot = me_body.get("bot") or me_body
        assert (me_bot.get("id") or me_bot.get("bot_id")) == bot.id

        bots = await client.get("/api/bots", headers={"Authorization": f"Bearer {bot.token}"})
        assert bots.status_code == 200, bots.text
        bots_body = bots.json()
        bot_items = bots_body.get("bots") if isinstance(bots_body, dict) else bots_body
        assert any((item.get("id") or item.get("bot_id")) == bot.id for item in bot_items)


async def test_challenge_accept_start_and_one_move_flow() -> None:
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as client:
        suffix = int(time.time())
        bot_a = await register_bot(client, f"pytest-a-{suffix}")
        bot_b = await register_bot(client, f"pytest-b-{suffix}")

        async with EventCollector(client, bot_a) as sse_a, EventCollector(client, bot_b) as sse_b:
            await sse_a.wait_for("connected")
            await sse_b.wait_for("connected")

            challenge_id = await create_challenge(client, bot_a, bot_b)

            challenge_evt = await sse_b.wait_for("challenge_received")
            challenge = challenge_evt["data"].get("challenge") or challenge_evt["data"]
            assert (challenge.get("id") or challenge.get("challenge_id")) == challenge_id
            assert challenge.get("challenger_bot_id") == bot_a.id
            assert challenge.get("opponent_bot_id") == bot_b.id

            accept_body = await accept_challenge(client, bot_b, challenge_id)
            match = accept_body.get("match") or accept_body
            match_id = match.get("id") or match.get("match_id") or accept_body.get("match_id")
            assert match_id

            started_a = await sse_a.wait_for("match_started")
            started_b = await sse_b.wait_for("match_started")
            match_a = started_a["data"].get("match") or started_a["data"]
            match_b = started_b["data"].get("match") or started_b["data"]
            assert (match_a.get("id") or match_a.get("match_id")) == match_id
            assert (match_b.get("id") or match_b.get("match_id")) == match_id

            turn_evt = await asyncio.wait_for(
                _wait_for_first_turn(sse_a, sse_b, match_id),
                timeout=5.0,
            )
            turn_bot = bot_a if turn_evt["collector"] == "a" else bot_b
            legal_moves = turn_evt["event"]["data"].get("legal_moves") or []
            assert legal_moves, "your_turn must include non-empty legal_moves"
            move = legal_moves[0]

            move_body = await submit_move(client, turn_bot, match_id, move)
            assert (move_body.get("move") or {}).get("move") == move or "match" in move_body

            await sse_a.wait_for("move_made")
            await sse_b.wait_for("move_made")

            # After one legal move, backend should either continue and notify next side,
            # or finish immediately in toy implementations.
            await _wait_for_next_turn_or_finish(sse_a, sse_b, match_id)


async def _wait_for_first_turn(sse_a: EventCollector, sse_b: EventCollector, match_id: str) -> dict[str, Any]:
    async def wait_a():
        evt = await sse_a.wait_for_predicate(
            lambda e: e.get("type") == "your_turn" and e["data"].get("match_id") == match_id,
            "A your_turn",
        )
        return {"collector": "a", "event": evt}

    async def wait_b():
        evt = await sse_b.wait_for_predicate(
            lambda e: e.get("type") == "your_turn" and e["data"].get("match_id") == match_id,
            "B your_turn",
        )
        return {"collector": "b", "event": evt}

    tasks = [asyncio.create_task(wait_a()), asyncio.create_task(wait_b())]
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    return next(iter(done)).result()


async def _wait_for_next_turn_or_finish(sse_a: EventCollector, sse_b: EventCollector, match_id: str) -> dict[str, Any]:
    def pred(evt: dict[str, Any]) -> bool:
        typ = evt.get("type")
        data = evt.get("data") or {}
        return data.get("match_id") == match_id and typ in {"your_turn", "match_finished"}

    async def wait_a():
        return await sse_a.wait_for_predicate(pred, "A next turn or finish")

    async def wait_b():
        return await sse_b.wait_for_predicate(pred, "B next turn or finish")

    tasks = [asyncio.create_task(wait_a()), asyncio.create_task(wait_b())]
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    return next(iter(done)).result()
