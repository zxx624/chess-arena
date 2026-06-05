# Owner Approval Chess Tools — Release Checklist

Scope: Chess Arena backend owner-approved challenge API plus AstrBot plugin command/tooling updates.

## Versioning suggestion

- `chess-arena`: new API/state feature, use a **minor** SemVer bump, e.g. next `v2.5.0` unless current tags indicate another next minor.
- `astrbot_plugin_chess_arena`: new commands/config behavior, use a **minor** SemVer bump, e.g. next `v3.3.0` unless current tags indicate another next minor.
- Do not tag/release until the checks below pass on the integrated tree.

## Static validation

```bash
cd /opt/chess-arena
python3 -m py_compile server/app/main.py server/app/engine.py
node --check server/app/static/arena.js
node --check server/app/static/match.js
PYTHONPATH=/opt/chess-arena/server pytest -q
PYTHONPATH=/opt/chess-arena/server pytest -q server/tests/test_owner_approval_challenges.py

python3 -m py_compile /opt/astrbot1/data/plugins/astrbot_plugin_chess_arena/main.py
python3 -m py_compile /opt/astrbot2/data/plugins/astrbot_plugin_chess_arena/main.py
python3 -m json.tool /opt/astrbot1/data/plugins/astrbot_plugin_chess_arena/_conf_schema.json >/dev/null
python3 -m json.tool /opt/astrbot2/data/plugins/astrbot_plugin_chess_arena/_conf_schema.json >/dev/null
```

## Smoke test

Local/live API smoke without real tokens:

```bash
cd /opt/chess-arena
python3 tools/smoke_owner_approval.py --base http://localhost:8787
# Optional cleanup if an admin token is available in the environment:
CHESS_ARENA_ADMIN_TOKEN='<set only in shell, do not commit>' python3 tools/smoke_owner_approval.py --base http://localhost:8787 --stop-match
```

Expected output includes `pending_count=... contains_created=True`, `match_id=...`, and `SMOKE_OK`.

## Secret/leak grep

Public docs/code must not contain real bot tokens, passwords, public-IP fallbacks, or NewAPI keys.

```bash
PATTERN='sk-[A-Za-z0-9_-]{20,}|AKID[A-Za-z0-9]|141728'"'SBZHOUXX|I87'"'htcq|H92'"'KKH|IK18'"'v|http://[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+'
! grep -RInE "$PATTERN" \
  /opt/chess-arena/README.md /opt/chess-arena/docs /opt/chess-arena/server/app/static /opt/chess-arena/server/app/templates \
  /opt/astrbot1/data/plugins/astrbot_plugin_chess_arena/main.py /opt/astrbot1/data/plugins/astrbot_plugin_chess_arena/_conf_schema.json /opt/astrbot1/data/plugins/astrbot_plugin_chess_arena/README.md \
  /opt/astrbot2/data/plugins/astrbot_plugin_chess_arena/main.py /opt/astrbot2/data/plugins/astrbot_plugin_chess_arena/_conf_schema.json /opt/astrbot2/data/plugins/astrbot_plugin_chess_arena/README.md
```

## Deployment commands for controller

Do not run these during subagent validation; controller should run them after integration approval.

```bash
sudo systemctl restart chess-arena
curl -fsS http://localhost:8787/health
sudo journalctl -u chess-arena --since '2 minutes ago' --no-pager | tail -120

sudo find /opt/astrbot1/data/plugins/astrbot_plugin_chess_arena /opt/astrbot2/data/plugins/astrbot_plugin_chess_arena -type d -name __pycache__ -prune -exec rm -rf {} +
sudo systemctl restart astrbot1 astrbot2
sleep 10
sudo journalctl -u astrbot1 -u astrbot2 --since '2 minutes ago' --no-pager | grep -Ei 'ChessArena|SSE|启动流程失败|Traceback|SyntaxError|ERROR|Exception' | tail -180
```

## Manual chat smoke

After AstrBot restart, send in a controlled owner chat/group:

```text
棋擂台状态
棋擂台待确认
棋擂台挑战 <对手名字或bot_id> 红
棋擂台当前
棋擂台最近
```

For owner approval mode, create or receive a challenge and verify:

```text
棋擂台待确认
棋擂台同意 <challenge_id>
# or
棋擂台拒绝 <challenge_id>
```

## Release notes draft

- Backend: added owner-review challenge state, pending challenge listing endpoint, and owner decision accept/reject endpoint.
- Plugin: added owner approval mode, owner notification target config, pending/accept/reject commands, and friendlier challenge/current/recent commands.
- Compatibility: legacy pending challenges remain actionable; existing auto-accept deployments remain default-compatible.
