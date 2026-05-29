#!/usr/bin/env python3
"""Fake match runner for Chess Arena Protocol v0.1.

This tool registers two bots, opens SSE streams for both, creates a challenge from A
against B, auto-accepts on B, and then auto-plays random legal_moves on every
`your_turn` event until a match finishes or max plies is reached.

It is intentionally lightweight and can be used as a smoke test against a running
backend at http://127.0.0.1:8787.

Dependencies:
    pip install httpx

Example:
    python tools/fake_match_runner.py --base-url http://127.0.0.1:8787 --max-plies 20
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, Optional

import httpx


@dataclass
class Bot:
    id: str
    name: str
    token: str


@dataclass
class RunnerState:
    challenge_id: Optional[str] = None
    match_id: Optional[str] = None
    plies: int = 0
    finished: bool = False
    events: list[dict[str, Any]] = field(default_factory=list)


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


async def register(client: httpx.AsyncClient, name: str) -> Bot:
    resp = await client.post(
        "/api/bots/register",
        json={
            "name": name,
            "capabilities": {"move_formats": ["ucci"], "variants": ["xiangqi"]},
        },
    )
    resp.raise_for_status()
    body = resp.json()
    bot_obj = body.get("bot") or body
    bot_id = bot_obj.get("id") or bot_obj.get("bot_id") or body.get("bot_id")
    token = body.get("token")
    if not bot_id or not token:
        raise KeyError(f"register response missing bot id/token: {body}")
    return Bot(id=bot_id, name=bot_obj.get("name", name), token=token)


async def iter_sse(client: httpx.AsyncClient, token: str) -> AsyncIterator[dict[str, Any]]:
    parser = SSEParser()
    async with client.stream("GET", "/sse/bot", params={"token": token}, timeout=None) as resp:
        resp.raise_for_status()
        async for line in resp.aiter_lines():
            evt = parser.feed_line(line)
            if not evt:
                continue
            try:
                data = json.loads(evt.get("data") or "{}")
            except json.JSONDecodeError:
                data = {"raw": evt.get("data")}
            yield {"event": evt.get("event"), "data": data}


async def create_challenge(client: httpx.AsyncClient, bot_a: Bot, bot_b: Bot) -> str:
    resp = await client.post(
        "/api/challenges",
        headers={"Authorization": f"Bearer {bot_a.token}"},
        json={
            "opponent_bot_id": bot_b.id,
            "variant": "xiangqi",
            "initial_fen": None,
            "time_control": {"base_ms": 300000, "increment_ms": 0},
        },
    )
    resp.raise_for_status()
    body = resp.json()
    challenge = body.get("challenge") or body
    challenge_id = challenge.get("id") or challenge.get("challenge_id") or body.get("challenge_id")
    if not challenge_id:
        raise KeyError(f"challenge response missing id: {body}")
    return challenge_id


async def accept_challenge(client: httpx.AsyncClient, bot: Bot, challenge_id: str) -> None:
    resp = await client.post(
        f"/api/challenges/{challenge_id}/accept",
        headers={"Authorization": f"Bearer {bot.token}"},
    )
    resp.raise_for_status()


async def submit_move(client: httpx.AsyncClient, bot: Bot, match_id: str, move: str) -> dict[str, Any]:
    resp = await client.post(
        f"/api/matches/{match_id}/move",
        headers={"Authorization": f"Bearer {bot.token}"},
        json={"move": move, "format": "ucci"},
    )
    resp.raise_for_status()
    return resp.json()


async def consume_bot(
    label: str,
    client: httpx.AsyncClient,
    bot: Bot,
    state: RunnerState,
    max_plies: int,
    stop: asyncio.Event,
) -> None:
    async for evt in iter_sse(client, bot.token):
        data = evt.get("data") or {}
        typ = data.get("type") or evt.get("event")
        state.events.append({"bot": label, "event": typ, "data": data})
        print(f"[{label}] {typ}: {json.dumps(data, ensure_ascii=False)}")

        if typ == "challenge_received":
            challenge = data.get("challenge") or {}
            challenge_id = challenge.get("id") or data.get("challenge_id")
            if challenge_id:
                state.challenge_id = state.challenge_id or challenge_id
                await accept_challenge(client, bot, challenge_id)

        elif typ == "challenge_accepted":
            state.match_id = state.match_id or data.get("match_id")

        elif typ == "match_started":
            match = data.get("match") or {}
            state.match_id = state.match_id or match.get("id") or data.get("match_id")

        elif typ == "your_turn":
            match_id = data.get("match_id") or state.match_id
            legal_moves = data.get("legal_moves") or []
            if not match_id or not legal_moves:
                continue
            if state.plies >= max_plies:
                print(f"[runner] max plies {max_plies} reached; stopping without abort API")
                stop.set()
                return
            move = random.choice(list(legal_moves))
            await submit_move(client, bot, match_id, move)
            state.plies += 1
            print(f"[{label}] move {state.plies}: {move}")

        elif typ == "match_finished":
            state.finished = True
            state.match_id = state.match_id or data.get("match_id")
            stop.set()
            return

        elif typ == "error":
            print(f"[{label}] error event: {data}")

        if stop.is_set():
            return


async def run(args: argparse.Namespace) -> RunnerState:
    state = RunnerState()
    stop = asyncio.Event()
    suffix = int(time.time())

    async with httpx.AsyncClient(base_url=args.base_url, timeout=args.timeout) as client:
        bot_a = await register(client, f"runner-a-{suffix}")
        bot_b = await register(client, f"runner-b-{suffix}")
        print(f"[runner] registered A={bot_a.id} B={bot_b.id}")

        task_a = asyncio.create_task(consume_bot("A", client, bot_a, state, args.max_plies, stop))
        task_b = asyncio.create_task(consume_bot("B", client, bot_b, state, args.max_plies, stop))

        # Give SSE connections a brief chance to establish and receive connected.
        await asyncio.sleep(args.connect_delay)
        state.challenge_id = await create_challenge(client, bot_a, bot_b)
        print(f"[runner] challenge created: {state.challenge_id}")

        try:
            await asyncio.wait_for(stop.wait(), timeout=args.duration)
        finally:
            task_a.cancel()
            task_b.cancel()
            await asyncio.gather(task_a, task_b, return_exceptions=True)

    print(
        f"[runner] done challenge={state.challenge_id} match={state.match_id} "
        f"plies={state.plies} finished={state.finished} events={len(state.events)}"
    )
    return state


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a fake random-vs-random match")
    parser.add_argument("--base-url", default="http://127.0.0.1:8787")
    parser.add_argument("--duration", type=float, default=30.0, help="maximum seconds to run")
    parser.add_argument("--timeout", type=float, default=20.0, help="HTTP request timeout")
    parser.add_argument("--connect-delay", type=float, default=0.5, help="delay after opening SSE before challenge")
    parser.add_argument("--max-plies", type=int, default=40)
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
