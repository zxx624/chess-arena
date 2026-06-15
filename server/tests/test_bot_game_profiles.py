from fastapi.testclient import TestClient

from app.main import (
    app,
    bots,
    bot_supports_game,
    enabled_games_for_bot,
    ensure_bot_game_profile,
)
from test_api import auth, reset_state


def test_register_creates_default_profile_and_keeps_legacy_game():
    reset_state()
    client = TestClient(app)
    res = client.post('/api/bots/register', json={'name': 'profile-xiangqi'})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body['game'] == 'xiangqi'
    assert body['enabled_games'] == ['xiangqi']

    me = client.get('/api/bots/me', headers=auth(body['token']))
    assert me.status_code == 200, me.text
    data = me.json()
    assert data['game'] == 'xiangqi'
    assert data['enabled_games'] == ['xiangqi']
    assert bot_supports_game(body['bot_id'], 'xiangqi')


def test_one_bot_can_have_xiangqi_go_doudizhu_profiles_and_filter_lists():
    reset_state()
    client = TestClient(app)
    body = client.post('/api/bots/register', json={'name': 'multi-profile', 'game': 'xiangqi'}).json()
    bot_id = body['bot_id']
    ensure_bot_game_profile(bot_id, 'go')
    ensure_bot_game_profile(bot_id, 'doudizhu')

    assert enabled_games_for_bot(bot_id) == ['xiangqi', 'go', 'doudizhu']
    xiangqi = client.get('/api/bots?game=xiangqi').json()['bots']
    go = client.get('/api/bots?game=go').json()['bots']
    assert any(b['bot_id'] == bot_id and b['game'] == 'xiangqi' and 'enabled_games' in b for b in xiangqi)
    assert any(b['bot_id'] == bot_id and b['game'] == 'xiangqi' and 'enabled_games' in b for b in go)


def test_patch_game_switches_default_without_losing_old_profile():
    reset_state()
    client = TestClient(app)
    body = client.post('/api/bots/register', json={'name': 'patch-game', 'game': 'xiangqi'}).json()
    res = client.patch('/api/bots/me', headers=auth(body['token']), json={'game': 'go'})
    assert res.status_code == 200, res.text
    data = res.json()
    assert data['game'] == 'go'
    assert data['enabled_games'] == ['xiangqi', 'go']
    assert bot_supports_game(body['bot_id'], 'xiangqi')
    assert bot_supports_game(body['bot_id'], 'go')


def test_go_challenge_uses_profiles_not_legacy_default_game():
    reset_state()
    client = TestClient(app)
    a = client.post('/api/bots/register', json={'name': 'a', 'game': 'xiangqi'}).json()
    b = client.post('/api/bots/register', json={'name': 'b', 'game': 'xiangqi'}).json()
    ensure_bot_game_profile(a['bot_id'], 'go')
    ensure_bot_game_profile(b['bot_id'], 'go')

    ch = client.post('/api/challenges', headers=auth(a['token']), json={'opponent_bot_id': b['bot_id'], 'game': 'go'})
    assert ch.status_code == 200, ch.text
    assert ch.json()['game'] == 'go'


def test_doudizhu_profile_does_not_enter_match_supported_games():
    reset_state()
    client = TestClient(app)
    a = client.post('/api/bots/register', json={'name': 'a', 'game': 'xiangqi'}).json()
    b = client.post('/api/bots/register', json={'name': 'b', 'game': 'xiangqi'}).json()
    ensure_bot_game_profile(a['bot_id'], 'doudizhu')
    ensure_bot_game_profile(b['bot_id'], 'doudizhu')

    ch = client.post('/api/challenges', headers=auth(a['token']), json={'opponent_bot_id': b['bot_id'], 'game': 'doudizhu'})
    assert ch.status_code == 400
    assert 'unsupported game' in ch.text
