#!/usr/bin/env python3
"""Random fake bot for Chess Arena Protocol v0.1.

Features:
- Registers itself if CHESS_ARENA_TOKEN is not provided.
- Connects to backend SSE: GET /sse/bot?token=...
- Automatically accepts challenge_received.
- On your_turn, chooses a random move from legal_moves and submits it.

Dependencies:
    pip install httpx

Example:
    python tools/fake_bot_random.py --base-url http://127.0.0.1:8787 --name bot-b
    CHESS_ARENA_TOKEN=... python tools/fake_bot_random.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import signal
import sys
from dataclasses import dataclass
from typing import Any, AsyncIterator, Dict, Optional

import httpx


@dataclass
class BotIdentity:
    bot_id: str
    name: str
    token: str


class SSEParser:
    """Small SSE parser for httpx async line streams."""

    def __init__(self) -> None:
        self.event: Optional[str] = None
        self.data_lines: list[str] = []

    def feed_line(self, line: str) -> Optional[Dict[str, Any]]:
        line = line.rstrip("\r")
        if line == "":
            if not self.event and not self.data_lines:
                return None
            raw_data = "\n".join(self.data_lines)
            evt = {"event": self.event or "message", "data": raw_data}
            self.event = None
            self.data_lines = []
            return evt
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


async def register_bot(client: httpx.AsyncClient, name: str) -> BotIdentity:
    payload = {
        "name": name,
        "capabilities": {"move_formats": ["ucci"], "variants": ["xiangqi"]},
    }
    resp = await client.post("/api/bots/register", json=payload)
    resp.raise_for_status()
    body = resp.json()
    bot = body.get("bot", {})
    token = body.get("token")
    bot_id = bot.get("id")
    if not token or not bot_id:
        raise RuntimeError(f"register response missing bot.id/token: {body}")
    return BotIdentity(bot_id=bot_id, name=bot.get("name", name), token=token)


async def get_me(client: httpx.AsyncClient, token: str, fallback_name: str) -> BotIdentity:
    resp = await client.get("/api/bots/me", headers={"Authorization": f"Bearer {token}"})
    resp.raise_for_status()
    bot = resp.json().get("bot", {})
    bot_id = bot.get("id")
    if not bot_id:
        raise RuntimeError(f"me response missing bot.id: {resp.text}")
    return BotIdentity(bot_id=bot_id, name=bot.get("name", fallback_name), token=token)


async def sse_events(client: httpx.AsyncClient, token: str) -> AsyncIterator[Dict[str, Any]]:
    parser = SSEParser()
    async with client.stream("GET", "/sse/bot", params={"token": token}, timeout=None) as resp:
        resp.raise_for_status()
        async for line in resp.aiter_lines():
            parsed = parser.feed_line(line)
            if not parsed:
                continue
            data_raw = parsed.get("data") or "{}"
            try:
                data = json.loads(data_raw)
            except json.JSONDecodeError:
                data = {"raw": data_raw}
            yield {"event": parsed.get("event"), "data": data}


async def accept_challenge(client: httpx.AsyncClient, token: str, challenge_id: str) -> None:
    resp = await client.post(
        f"/api/challenges/{challenge_id}/accept",
        headers={"Authorization": f"Bearer {token}"},
    )
    if resp.status_code >= 400:
        print(f"[warn] accept challenge {challenge_id} failed: {resp.status_code} {resp.text}", file=sys.stderr)
    else:
        print(f"[info] accepted challenge {challenge_id}")


async def submit_random_move(
    client: httpx.AsyncClient,
    token: str,
    match_id: str,
    legal_moves: list[str],
    min_delay: float = 0.0,
    max_delay: float = 0.0,
) -> None:
    if not legal_moves:
        print(f"[warn] your_turn for {match_id} has no legal_moves", file=sys.stderr)
        return
    if max_delay > 0:
        await asyncio.sleep(random.uniform(min_delay, max_delay))
    move = random.choice(legal_moves)
    resp = await client.post(
        f"/api/matches/{match_id}/move",
        headers={"Authorization": f"Bearer {token}"},
        json={"move": move, "format": "ucci"},
    )
    if resp.status_code >= 400:
        print(f"[warn] move {move} in {match_id} failed: {resp.status_code} {resp.text}", file=sys.stderr)
    else:
        print(f"[info] submitted move {move} in match {match_id}")


async def run(args: argparse.Namespace) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    async with httpx.AsyncClient(base_url=args.base_url, timeout=args.timeout) as client:
        env_token = os.environ.get("CHESS_ARENA_TOKEN")
        if args.token or env_token:
            ident = await get_me(client, args.token or env_token, args.name)
        else:
            ident = await register_bot(client, args.name)
            print(f"[info] registered bot_id={ident.bot_id} token={ident.token}")

        print(f"[info] connecting SSE as {ident.name} ({ident.bot_id}) to {args.base_url}")

        async def consume() -> None:
            async for evt in sse_events(client, ident.token):
                name = evt.get("event")
                data = evt.get("data") or {}
                print(f"[sse] {name}: {json.dumps(data, ensure_ascii=False)}")

                typ = data.get("type") or name
                if typ == "challenge_received":
                    challenge = data.get("challenge") or {}
                    challenge_id = challenge.get("id") or data.get("challenge_id")
                    if challenge_id:
                        asyncio.create_task(accept_challenge(client, ident.token, challenge_id))
                elif typ == "your_turn":
                    match_id = data.get("match_id") or (data.get("match") or {}).get("id")
                    legal_moves = data.get("legal_moves") or []
                    if match_id:
                        asyncio.create_task(
                            submit_random_move(
                                client,
                                ident.token,
                                match_id,
                                list(legal_moves),
                                args.min_delay,
                                args.max_delay,
                            )
                        )
                elif typ == "match_finished":
                    print(f"[info] match finished: {data.get('match_id')} result={data.get('result')}")
                elif typ == "error":
                    print(f"[error-event] {data}", file=sys.stderr)

        task = asyncio.create_task(consume())
        await stop.wait()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Random fake bot for Chess Arena")
    parser.add_argument("--base-url", default=os.environ.get("CHESS_ARENA_URL", "http://127.0.0.1:8787"))
    parser.add_argument("--name", default=os.environ.get("CHESS_ARENA_BOT_NAME", f"random-bot-{random.randint(1000, 9999)}"))
    parser.add_argument("--token", default=os.environ.get("CHESS_ARENA_TOKEN"))
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--min-delay", type=float, default=0.0, help="minimum think delay before moving")
    parser.add_argument("--max-delay", type=float, default=0.0, help="maximum think delay before moving")
    return parser.parse_args()


if __name__ == "__main__":
    try:
        asyncio.run(run(parse_args()))
    except KeyboardInterrupt:
        pass
