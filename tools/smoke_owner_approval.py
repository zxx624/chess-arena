#!/usr/bin/env python3
"""Live smoke for Chess Arena owner-approval challenge flow.

Safe defaults:
- Talks to http://localhost:8787 by default.
- Registers throwaway bots with random names; does not contain or require real bot tokens.
- Uses bot self PATCH to set manual_approve if the deployed API supports it.
- Optionally stops the created match when --stop-match is passed and --admin-token is provided.

Example:
  python3 tools/smoke_owner_approval.py --base http://localhost:8787
  CHESS_ARENA_ADMIN_TOKEN=... python3 tools/smoke_owner_approval.py --stop-match
"""
from __future__ import annotations

import argparse
import os
import secrets
import sys
from typing import Any
from urllib.parse import urljoin

import requests


def api(base: str, method: str, path: str, *, token: str | None = None, admin_token: str | None = None, **kwargs: Any) -> requests.Response:
    headers = kwargs.pop("headers", {}) or {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if admin_token:
        headers["Authorization"] = f"Bearer {admin_token}"
    return requests.request(method, urljoin(base.rstrip("/") + "/", path.lstrip("/")), headers=headers, timeout=15, **kwargs)


def expect(resp: requests.Response, status: int, label: str) -> dict[str, Any]:
    if resp.status_code != status:
        raise SystemExit(f"{label} failed: HTTP {resp.status_code}: {resp.text[:1000]}")
    try:
        return resp.json()
    except ValueError as exc:
        raise SystemExit(f"{label} returned non-JSON: {resp.text[:1000]}") from exc


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=os.environ.get("CHESS_ARENA_BASE", "http://localhost:8787"), help="Chess Arena base URL")
    parser.add_argument("--admin-token", default=os.environ.get("CHESS_ARENA_ADMIN_TOKEN", ""), help="optional admin token for cleanup")
    parser.add_argument("--stop-match", action="store_true", help="stop created match at the end; requires admin token or participant control support")
    args = parser.parse_args()

    suffix = secrets.token_hex(4)
    base = args.base.rstrip("/")
    print(f"base={base}")

    health = api(base, "GET", "/health")
    if health.status_code >= 400:
        raise SystemExit(f"health failed: HTTP {health.status_code}: {health.text[:500]}")

    challenger = expect(api(base, "POST", "/api/bots/register", json={"name": f"smoke-owner-challenger-{suffix}"}), 200, "register challenger")
    opponent = expect(api(base, "POST", "/api/bots/register", json={"name": f"smoke-owner-opponent-{suffix}"}), 200, "register opponent")
    print(f"challenger_bot_id={challenger['bot_id']}")
    print(f"opponent_bot_id={opponent['bot_id']}")

    patch = api(
        base,
        "PATCH",
        "/api/bots/me",
        token=opponent["token"],
        json={"challenge_policy": "manual_approve", "owner_review_timeout_sec": 180},
    )
    if patch.status_code == 200:
        updated = patch.json()
        print(f"opponent_policy={updated.get('challenge_policy')} timeout={updated.get('owner_review_timeout_sec')}")
    else:
        print(f"WARN: PATCH /api/bots/me policy unsupported/failed ({patch.status_code}); continuing, challenge may stay pending")

    created = expect(
        api(base, "POST", "/api/challenges", token=challenger["token"], json={"opponent_bot_id": opponent["bot_id"], "side": "red"}),
        200,
        "create challenge",
    )
    challenge_id = created["challenge_id"]
    print(f"challenge_id={challenge_id} status={created.get('status')} requires_owner_decision={created.get('requires_owner_decision')}")

    pending_resp = api(base, "GET", "/api/bots/me/challenges/pending", token=opponent["token"])
    pending = expect(pending_resp, 200, "pending challenges")
    ids = [c.get("challenge_id") for c in pending.get("challenges", [])]
    print(f"pending_count={len(ids)} contains_created={challenge_id in ids}")
    if challenge_id not in ids:
        raise SystemExit(f"pending endpoint did not include created challenge {challenge_id}: {pending}")

    accepted = expect(
        api(base, "POST", f"/api/challenges/{challenge_id}/owner_decision", token=opponent["token"], json={"decision": "accept", "reason": "live smoke accept"}),
        200,
        "owner_decision accept",
    )
    challenge = accepted.get("challenge", accepted)
    match = accepted.get("match") or {}
    match_id = challenge.get("match_id") or match.get("match_id")
    print(f"accepted_status={challenge.get('status')} match_id={match_id} match_url={accepted.get('match_url', '')}")
    if not match_id:
        raise SystemExit(f"owner_decision did not create match: {accepted}")

    if args.stop_match:
        stop_token = args.admin_token or opponent["token"]
        stopped = api(base, "POST", f"/api/matches/{match_id}/stop", token=None if args.admin_token else stop_token, admin_token=args.admin_token or None)
        print(f"cleanup_stop_http={stopped.status_code} body={stopped.text[:500]}")
    else:
        print("cleanup: match left active intentionally. Re-run with --stop-match and admin token, or stop it from admin UI/API if needed.")

    print("SMOKE_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
