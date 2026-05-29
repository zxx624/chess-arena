from __future__ import annotations

import argparse

import httpx

from app.engine import legal_moves


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a fake-bot smoke game against chess-arena-server")
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--plies", type=int, default=20)
    args = parser.parse_args()

    with httpx.Client(base_url=args.base, timeout=10) as c:
        a = c.post("/api/bots/register", json={"name": "fake-a"}).json()
        b = c.post("/api/bots/register", json={"name": "fake-b"}).json()
        ah = {"Authorization": f"Bearer {a['token']}"}
        bh = {"Authorization": f"Bearer {b['token']}"}
        ch = c.post("/api/challenges", headers=ah, json={"opponent_bot_id": b["bot_id"], "side": "red"}).json()
        accepted = c.post(f"/api/challenges/{ch['challenge_id']}/accept", headers=bh).json()
        match_id = accepted["match_id"]
        tokens = {a["bot_id"]: a["token"], b["bot_id"]: b["token"]}
        print(f"match {match_id} started")
        for i in range(args.plies):
            m = c.get(f"/api/matches/{match_id}", headers=ah).json()
            if m["status"] != "active":
                print("finished", m["result"])
                break
            moves = legal_moves(m["fen"])
            if not moves:
                print("no legal moves")
                break
            mv = moves[0]
            h = {"Authorization": f"Bearer {tokens[m['turn_bot_id']]}"}
            res = c.post(f"/api/matches/{match_id}/move", headers=h, json={"move": mv, "comment": "fake-smoke"})
            res.raise_for_status()
            print(f"{i+1:03d} {m['turn']} {mv}")
        final = c.get(f"/api/matches/{match_id}", headers=ah).json()
        print(f"final status={final['status']} ply={final['ply']} fen={final['fen']}")


if __name__ == "__main__":
    main()
