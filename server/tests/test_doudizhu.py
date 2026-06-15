import pytest

from app.games import doudizhu


def test_new_state_deals_54_cards():
    state = doudizhu.new_state(["a", "b", "c"], seed=1)
    data = state.to_dict()
    assert data["game"] == "doudizhu"
    assert data["landlord"] == "a"
    assert len(data["bottom"]) == 3
    assert len(data["hands"]["a"]) == 20
    assert len(data["hands"]["b"]) == 17
    assert len(data["hands"]["c"]) == 17
    all_cards = data["hands"]["a"] + data["hands"]["b"] + data["hands"]["c"]
    assert len(all_cards) == 54
    assert len(set(all_cards)) == 54


def test_state_roundtrip_and_play_turn_rotation():
    state = doudizhu.new_state(["a", "b", "c"], seed=2)
    raw = doudizhu.dumps_state(state)
    first = state.hands["a"][0]
    raw, move = doudizhu.apply_action(raw, "a", f"play:{first}")
    data = doudizhu.loads_state(raw)
    assert move["action"] == "play"
    assert move["cards"] == [first]
    assert move["pattern"]["type"] == "single"
    assert first not in data["hands"]["a"]
    assert data["turn_player"] == "b"


def test_rejects_not_your_turn_and_missing_card():
    state = doudizhu.new_state(["a", "b", "c"], seed=3)
    raw = doudizhu.dumps_state(state)
    with pytest.raises(doudizhu.DouDizhuRuleError, match="not your turn"):
        doudizhu.apply_action(raw, "b", "pass")
    missing = next(card for card in doudizhu.deck() if card not in state.hands["a"])
    with pytest.raises(doudizhu.DouDizhuRuleError, match="card not in hand"):
        doudizhu.apply_action(raw, "a", f"play:{missing}")
    with pytest.raises(doudizhu.DouDizhuRuleError, match="invalid card"):
        doudizhu.apply_action(raw, "a", "play:NOT_A_CARD")


def test_pass_requires_existing_play():
    state = doudizhu.new_state(["a", "b", "c"], seed=4)
    with pytest.raises(doudizhu.DouDizhuRuleError, match="cannot pass"):
        doudizhu.apply_action(doudizhu.dumps_state(state), "a", "pass")


def test_classifies_basic_patterns_and_rejects_unsupported():
    assert doudizhu.classify_cards(["3S"])["type"] == "single"
    assert doudizhu.classify_cards(["4S", "4H"])["type"] == "pair"
    assert doudizhu.classify_cards(["5S", "5H", "5D"])["type"] == "triple"
    assert doudizhu.classify_cards(["5S", "5H", "5D", "3S"])["type"] == "triple_with_single"
    assert doudizhu.classify_cards(["5S", "5H", "5D", "3S", "3H"])["type"] == "triple_with_pair"
    assert doudizhu.classify_cards(["3S", "4S", "5S", "6S", "7S"])["type"] == "straight"
    assert doudizhu.classify_cards(["3S", "3H", "4S", "4H", "5S", "5H"])["type"] == "pair_straight"
    assert doudizhu.classify_cards(["6S", "6H", "6D", "6C"])["type"] == "bomb"
    assert doudizhu.classify_cards(["BJ", "RJ"])["type"] == "rocket"
    with pytest.raises(doudizhu.DouDizhuRuleError, match="unsupported"):
        doudizhu.classify_cards(["10S", "JS", "QS", "KS", "AS", "2S"])
    with pytest.raises(doudizhu.DouDizhuRuleError, match="unsupported"):
        doudizhu.classify_cards(["3S", "3H", "4S", "4H"])


def test_must_beat_last_play_unless_bomb_or_rocket():
    state = doudizhu.new_state(["a", "b", "c"], seed=5).to_dict()
    state["hands"] = {
        "a": ["8S", "10S"],
        "b": ["7S", "9S", "4S", "4H", "4D", "4C"],
        "c": ["BJ", "RJ"],
    }
    raw, _ = doudizhu.apply_action(state, "a", "play:8S")
    with pytest.raises(doudizhu.DouDizhuRuleError, match="does not beat"):
        doudizhu.apply_action(raw, "b", "play:7S")

    raw, move = doudizhu.apply_action(raw, "b", "play:9S")
    assert move["cards"] == ["9S"]

    state = doudizhu.loads_state(raw)
    state["turn_index"] = 1
    state["last_play"] = {"player": "a", "action": "play", "cards": ["2S"], "pattern": {"type": "single", "rank": 12, "length": 1}}
    raw, move = doudizhu.apply_action(state, "b", "play:4S,4H,4D,4C")
    assert move["pattern"]["type"] == "bomb"

    state = doudizhu.loads_state(raw)
    state["turn_index"] = 2
    raw, move = doudizhu.apply_action(state, "c", "play:BJ,RJ")
    assert move["pattern"]["type"] == "rocket"


def test_combo_and_sequence_must_match_type_and_length():
    assert doudizhu.can_beat(
        doudizhu.classify_cards(["6S", "6H", "6D", "3S"]),
        doudizhu.classify_cards(["5S", "5H", "5D", "4S"]),
    )
    assert not doudizhu.can_beat(
        doudizhu.classify_cards(["6S", "6H", "6D", "3S"]),
        doudizhu.classify_cards(["5S", "5H", "5D", "4S", "4H"]),
    )
    assert doudizhu.can_beat(
        doudizhu.classify_cards(["4S", "5S", "6S", "7S", "8S"]),
        doudizhu.classify_cards(["3S", "4H", "5H", "6H", "7H"]),
    )
    assert not doudizhu.can_beat(
        doudizhu.classify_cards(["4S", "5S", "6S", "7S", "8S", "9S"]),
        doudizhu.classify_cards(["3S", "4H", "5H", "6H", "7H"]),
    )


def test_two_passes_reset_round():
    state = doudizhu.new_state(["a", "b", "c"], seed=6).to_dict()
    state["hands"] = {"a": ["8S", "8H"], "b": ["3S"], "c": ["4S"]}
    raw, _ = doudizhu.apply_action(state, "a", "play:8S")
    raw, move_b = doudizhu.apply_action(raw, "b", "pass")
    assert not move_b.get("round_reset")
    raw, move_c = doudizhu.apply_action(raw, "c", "pass")
    data = doudizhu.loads_state(raw)
    assert move_c["round_reset"] is True
    assert data["last_play"] is None
    assert data["passes"] == 0
    assert data["turn_player"] == "a"


def test_choose_auto_action_plays_minimal_response_or_passes():
    state = doudizhu.new_state(["a", "b", "c"], seed=7).to_dict()
    state["hands"] = {"a": ["8S", "10S"], "b": ["3S", "9S"], "c": ["4S"]}
    raw, _ = doudizhu.apply_action(state, "a", "play:8S")
    assert doudizhu.choose_auto_action(raw, "b") == "play:9S"
    raw, _ = doudizhu.apply_action(raw, "b", "play:9S")
    assert doudizhu.choose_auto_action(raw, "c") == "pass"


def test_choose_auto_action_uses_combo_and_sequence_responses():
    state = doudizhu.new_state(["a", "b", "c"], seed=70).to_dict()
    state["hands"] = {
        "a": ["5S", "5H", "5D", "3S", "10S"],
        "b": ["6S", "6H", "6D", "4S", "9S"],
        "c": ["3H"],
    }
    raw, _ = doudizhu.apply_action(state, "a", "play:5S,5H,5D,3S")
    assert doudizhu.choose_auto_action(raw, "b") == "play:4S,6S,6H,6D"

    state = doudizhu.new_state(["a", "b", "c"], seed=71).to_dict()
    state["hands"] = {
        "a": ["3S", "4S", "5S", "6S", "7S", "10S"],
        "b": ["4H", "5H", "6H", "7H", "8H", "9H"],
        "c": ["3H"],
    }
    raw, _ = doudizhu.apply_action(state, "a", "play:3S,4S,5S,6S,7S")
    assert doudizhu.choose_auto_action(raw, "b") == "play:4H,5H,6H,7H,8H"


def test_auto_run_finishes_a_small_custom_game():
    state = doudizhu.new_state(["a", "b", "c"], seed=8).to_dict()
    state["hands"] = {"a": ["3S"], "b": ["4S"], "c": ["5S"]}
    raw, moves = doudizhu.auto_run(state, max_steps=6)
    data = doudizhu.loads_state(raw)
    assert moves
    assert data["phase"] == "finished"
    assert data["winner"] in {"a", "b", "c"}
