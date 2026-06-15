"""Lightweight DouDizhu rules for the 9191 sandbox demo.

This module is intentionally independent from the production two-player Match
flow. It is a small serialisable state machine that can drive a future
three-seat card-room API/UI without pulling in RLCard/DouZero yet.

Supported MVP patterns:
- single
- pair
- triple
- triple_with_single
- triple_with_pair
- straight (5+ consecutive singles, excluding 2/jokers)
- pair_straight (3+ consecutive pairs, excluding 2/jokers)
- bomb
- rocket (BJ+RJ)

Planes/four-with-two are still intentionally unsupported. The demo bot plays
the smallest legal response it can find and prefers useful triple attachments
before naked triples when starting a round.
"""

from __future__ import annotations

import json
import random
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

SUITS = ["S", "H", "D", "C"]
RANKS = ["3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A", "2"]
JOKERS = ["BJ", "RJ"]
PLAYER_COUNT = 3
HAND_SIZE = 17
BOTTOM_SIZE = 3
ROCKET_RANK = len(RANKS) + 2
MAX_SEQUENCE_RANK = RANKS.index("A")  # 2 and jokers cannot appear in straights.


class DouDizhuRuleError(ValueError):
    """Raised when an action is illegal for the current MVP state."""


@dataclass
class DouDizhuState:
    players: list[str]
    hands: dict[str, list[str]]
    bottom: list[str]
    turn_index: int = 0
    landlord: str | None = None
    phase: str = "playing"
    last_play: dict[str, Any] | None = None
    passes: int = 0
    history: list[dict[str, Any]] = field(default_factory=list)
    winner: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "game": "doudizhu",
            "players": self.players,
            "hands": self.hands,
            "bottom": self.bottom,
            "turn_index": self.turn_index,
            "turn_player": self.players[self.turn_index] if self.players else None,
            "landlord": self.landlord,
            "phase": self.phase,
            "last_play": self.last_play,
            "passes": self.passes,
            "history": self.history,
            "winner": self.winner,
        }


def deck() -> list[str]:
    return [f"{rank}{suit}" for rank in RANKS for suit in SUITS] + JOKERS


def new_state(players: list[str], seed: int | None = None, landlord_index: int = 0) -> DouDizhuState:
    if len(players) != PLAYER_COUNT:
        raise DouDizhuRuleError("doudizhu requires exactly 3 players")
    if len(set(players)) != PLAYER_COUNT:
        raise DouDizhuRuleError("players must be unique")
    if not 0 <= landlord_index < PLAYER_COUNT:
        raise DouDizhuRuleError("invalid landlord index")

    cards = deck()
    rng = random.Random(seed)
    rng.shuffle(cards)
    hands = {
        player: sort_cards(cards[idx * HAND_SIZE : (idx + 1) * HAND_SIZE])
        for idx, player in enumerate(players)
    }
    bottom = sort_cards(cards[PLAYER_COUNT * HAND_SIZE :])
    if len(bottom) != BOTTOM_SIZE:
        raise DouDizhuRuleError("invalid deal")

    # MVP: fixed/random landlord is decided by caller; bidding/robbing comes later.
    landlord = players[landlord_index]
    hands[landlord] = sort_cards(hands[landlord] + bottom)
    return DouDizhuState(players=list(players), hands=hands, bottom=bottom, landlord=landlord, turn_index=landlord_index)


def dumps_state(state: DouDizhuState | dict[str, Any]) -> str:
    data = state.to_dict() if isinstance(state, DouDizhuState) else state
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def loads_state(raw: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(raw, dict):
        data = raw
    else:
        data = json.loads(raw)
    if not isinstance(data, dict) or data.get("game") != "doudizhu":
        raise DouDizhuRuleError("invalid doudizhu state")
    players = data.get("players") or []
    if len(players) != PLAYER_COUNT:
        raise DouDizhuRuleError("invalid players")
    hands = data.get("hands") or {}
    for player in players:
        hands[player] = sort_cards(list(hands.get(player, [])))
    data["hands"] = hands
    return data


def card_rank(card: str) -> int:
    if card == "BJ":
        return len(RANKS)
    if card == "RJ":
        return len(RANKS) + 1
    rank = card[:-1]
    if rank not in RANKS or card[-1] not in SUITS:
        raise DouDizhuRuleError(f"invalid card: {card}")
    return RANKS.index(rank)


def card_sort_key(card: str) -> tuple[int, int]:
    if card == "BJ":
        return (len(RANKS), 0)
    if card == "RJ":
        return (len(RANKS) + 1, 0)
    rank = card[:-1]
    suit = card[-1]
    if rank not in RANKS or suit not in SUITS:
        raise DouDizhuRuleError(f"invalid card: {card}")
    return (RANKS.index(rank), SUITS.index(suit))


def sort_cards(cards: list[str]) -> list[str]:
    return sorted(cards, key=card_sort_key)


def current_player(state: dict[str, Any]) -> str:
    players = state.get("players") or []
    if len(players) != PLAYER_COUNT:
        raise DouDizhuRuleError("invalid players")
    return players[int(state.get("turn_index") or 0) % PLAYER_COUNT]


def _is_consecutive(ranks: list[int]) -> bool:
    return all(a + 1 == b for a, b in zip(ranks, ranks[1:]))


def _sequence_descriptor(pattern_type: str, ranks: list[int], card_count: int) -> dict[str, Any]:
    return {"type": pattern_type, "rank": max(ranks), "length": card_count, "seq_len": len(ranks)}


def classify_cards(cards: list[str]) -> dict[str, Any]:
    """Return a supported MVP pattern descriptor for cards."""
    cards = sort_cards(cards)
    if cards == ["BJ", "RJ"]:
        return {"type": "rocket", "rank": ROCKET_RANK, "length": 2}

    ranks = [card_rank(c) for c in cards]
    counts = Counter(ranks)
    count_values = sorted(counts.values())
    if len(cards) == 1:
        return {"type": "single", "rank": ranks[0], "length": 1}
    if len(cards) == 2 and len(counts) == 1:
        return {"type": "pair", "rank": ranks[0], "length": 2}
    if len(cards) == 3 and len(counts) == 1:
        return {"type": "triple", "rank": ranks[0], "length": 3}
    if len(cards) == 4 and len(counts) == 1:
        return {"type": "bomb", "rank": ranks[0], "length": 4}
    if len(cards) == 4 and count_values == [1, 3]:
        triple_rank = next(rank for rank, count in counts.items() if count == 3)
        return {"type": "triple_with_single", "rank": triple_rank, "length": 4}
    if len(cards) == 5 and count_values == [2, 3]:
        triple_rank = next(rank for rank, count in counts.items() if count == 3)
        return {"type": "triple_with_pair", "rank": triple_rank, "length": 5}

    unique_ranks = sorted(counts)
    if len(cards) >= 5 and len(counts) == len(cards):
        if unique_ranks[-1] <= MAX_SEQUENCE_RANK and _is_consecutive(unique_ranks):
            return _sequence_descriptor("straight", unique_ranks, len(cards))
    if len(cards) >= 6 and len(cards) % 2 == 0 and all(count == 2 for count in counts.values()):
        if len(unique_ranks) >= 3 and unique_ranks[-1] <= MAX_SEQUENCE_RANK and _is_consecutive(unique_ranks):
            return _sequence_descriptor("pair_straight", unique_ranks, len(cards))
    raise DouDizhuRuleError("unsupported card pattern")


def can_beat(candidate: dict[str, Any], target: dict[str, Any] | None) -> bool:
    if not target:
        return True
    ctype = candidate["type"]
    ttype = target["type"]
    if ctype == "rocket":
        return ttype != "rocket"
    if ttype == "rocket":
        return False
    if ctype == "bomb" and ttype != "bomb":
        return True
    if ctype != ttype:
        return False
    if ctype in {"straight", "pair_straight"} and int(candidate.get("seq_len") or 0) != int(target.get("seq_len") or 0):
        return False
    if int(candidate.get("length") or 0) != int(target.get("length") or 0):
        return False
    return int(candidate["rank"]) > int(target["rank"])


def _remove_cards(hand: list[str], cards: list[str]) -> list[str]:
    rest = list(hand)
    for card in cards:
        if card not in rest:
            raise DouDizhuRuleError(f"card not in hand: {card}")
        rest.remove(card)
    return sort_cards(rest)


def _normalise_last_play(last_play: dict[str, Any] | None) -> dict[str, Any] | None:
    if not last_play or last_play.get("action") != "play":
        return None
    pattern = last_play.get("pattern")
    if not pattern:
        pattern = classify_cards(list(last_play.get("cards") or []))
        last_play["pattern"] = pattern
    return last_play


def apply_action(raw_state: str | dict[str, Any], player: str, action: str) -> tuple[str, dict[str, Any]]:
    state = loads_state(raw_state)
    if state.get("phase") != "playing":
        raise DouDizhuRuleError("room is not playing")
    if player != current_player(state):
        raise DouDizhuRuleError("not your turn")

    action = (action or "").strip()
    last_play = _normalise_last_play(state.get("last_play"))

    if action == "pass":
        if not last_play:
            raise DouDizhuRuleError("cannot pass before any play")
        if last_play.get("player") == player:
            raise DouDizhuRuleError("cannot pass against yourself")
        move = {"player": player, "action": "pass", "cards": []}
        state["passes"] = int(state.get("passes") or 0) + 1
        if state["passes"] >= PLAYER_COUNT - 1:
            state["last_play"] = None
            state["passes"] = 0
            move["round_reset"] = True
    elif action.startswith("play:"):
        cards = [c.strip() for c in action.split(":", 1)[1].split(",") if c.strip()]
        if not cards:
            raise DouDizhuRuleError("play action needs cards")
        cards = sort_cards(cards)
        hand = _remove_cards(list(state.get("hands", {}).get(player, [])), cards)
        pattern = classify_cards(cards)
        if not can_beat(pattern, last_play.get("pattern") if last_play else None):
            raise DouDizhuRuleError("play does not beat last play")

        state["hands"][player] = hand
        move = {"player": player, "action": "play", "cards": cards, "pattern": pattern}
        state["last_play"] = move
        state["passes"] = 0
        if not hand:
            state["phase"] = "finished"
            state["winner"] = player
    else:
        raise DouDizhuRuleError("unknown action")

    state.setdefault("history", []).append(move)
    if state.get("phase") == "playing":
        state["turn_index"] = (int(state.get("turn_index") or 0) + 1) % PLAYER_COUNT
        state["turn_player"] = current_player(state)
        move["next_player"] = state["turn_player"]
    else:
        state["turn_player"] = None
    return dumps_state(state), move


def _rank_groups(hand: list[str]) -> dict[int, list[str]]:
    groups: dict[int, list[str]] = {}
    for card in sort_cards(hand):
        groups.setdefault(card_rank(card), []).append(card)
    return groups


def _first_attachment(groups: dict[int, list[str]], exclude_rank: int, size: int) -> list[str] | None:
    for rank in sorted(groups):
        if rank == exclude_rank:
            continue
        cards = sort_cards(groups[rank])
        if len(cards) >= size:
            return cards[:size]
    return None


def _sequence_candidates(groups: dict[int, list[str]], min_len: int, per_rank: int) -> list[list[str]]:
    available = [rank for rank, cards in groups.items() if rank <= MAX_SEQUENCE_RANK and len(cards) >= per_rank]
    available = sorted(available)
    runs: list[list[int]] = []
    current: list[int] = []
    for rank in available:
        if not current or current[-1] + 1 == rank:
            current.append(rank)
        else:
            if len(current) >= min_len:
                runs.append(current)
            current = [rank]
    if len(current) >= min_len:
        runs.append(current)

    candidates: list[list[str]] = []
    for run in runs:
        for length in range(min_len, len(run) + 1):
            for start in range(0, len(run) - length + 1):
                ranks = run[start : start + length]
                cards: list[str] = []
                for rank in ranks:
                    cards.extend(sort_cards(groups[rank])[:per_rank])
                candidates.append(sort_cards(cards))
    return candidates


def candidate_plays(hand: list[str]) -> list[list[str]]:
    """Generate supported MVP candidate plays, sorted small-to-large."""
    hand = sort_cards(hand)
    groups = _rank_groups(hand)

    candidates: list[list[str]] = []
    for rank in sorted(groups):
        cards = sort_cards(groups[rank])
        candidates.append(cards[:1])
        if len(cards) >= 2:
            candidates.append(cards[:2])
        if len(cards) >= 3:
            single = _first_attachment(groups, rank, 1)
            pair = _first_attachment(groups, rank, 2)
            if single:
                candidates.append(sort_cards(cards[:3] + single))
            if pair:
                candidates.append(sort_cards(cards[:3] + pair))
            candidates.append(cards[:3])
        if len(cards) == 4:
            candidates.append(cards[:4])
    candidates.extend(_sequence_candidates(groups, min_len=5, per_rank=1))
    candidates.extend(_sequence_candidates(groups, min_len=3, per_rank=2))
    if "BJ" in hand and "RJ" in hand:
        candidates.append(["BJ", "RJ"])

    def key(cards: list[str]) -> tuple[int, int, int, tuple[int, ...]]:
        pattern = classify_cards(cards)
        type_order = {
            "single": 0,
            "pair": 1,
            "triple_with_single": 2,
            "triple_with_pair": 3,
            "triple": 4,
            "straight": 5,
            "pair_straight": 6,
            "bomb": 7,
            "rocket": 8,
        }
        ranks = tuple(card_rank(card) for card in cards)
        return (type_order[pattern["type"]], int(pattern["rank"]), len(cards), ranks)

    seen: set[tuple[str, ...]] = set()
    unique = []
    for cards in sorted(candidates, key=key):
        marker = tuple(cards)
        if marker not in seen:
            seen.add(marker)
            unique.append(cards)
    return unique


def choose_auto_action(raw_state: str | dict[str, Any], player: str | None = None) -> str:
    """Return a deterministic weak-bot action for the current player."""
    state = loads_state(raw_state)
    player = player or current_player(state)
    if player != current_player(state):
        raise DouDizhuRuleError("not your turn")
    hand = list(state.get("hands", {}).get(player, []))
    last_play = _normalise_last_play(state.get("last_play"))
    target = last_play.get("pattern") if last_play else None
    if target and last_play and last_play.get("player") == player:
        target = None

    for cards in candidate_plays(hand):
        pattern = classify_cards(cards)
        if can_beat(pattern, target):
            return "play:" + ",".join(cards)
    return "pass"


def auto_step(raw_state: str | dict[str, Any]) -> tuple[str, dict[str, Any]]:
    state = loads_state(raw_state)
    player = current_player(state)
    action = choose_auto_action(state, player)
    return apply_action(state, player, action)


def auto_run(raw_state: str | dict[str, Any], max_steps: int = 20) -> tuple[str, list[dict[str, Any]]]:
    if max_steps < 1:
        raise DouDizhuRuleError("max_steps must be positive")
    raw: str | dict[str, Any] = raw_state
    moves: list[dict[str, Any]] = []
    for _ in range(max_steps):
        state = loads_state(raw)
        if state.get("phase") != "playing":
            break
        raw, move = auto_step(state)
        moves.append(move)
    return dumps_state(loads_state(raw)), moves
