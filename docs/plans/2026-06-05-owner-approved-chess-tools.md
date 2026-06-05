# Chess Arena 主人审批与自然语言工具改造实施计划

> **For Hermes:** Use subagent-driven-development skill to implement this plan. This is a parallel MVP plan: CC owns the hardest backend state/API migration; three subagents own plugin owner-approval, plugin natural-language commands/tools, and tests/docs/release glue. Controller must do final integration, validation, deployment, and release.

**Goal:** 让 AstrBot 棋擂台插件支持自然语言发起/查询/汇报对局，并在有人挑战我们的 Bot 时先通知主人，由主人回复同意/拒绝后再开局。

**Architecture:** Chess Arena 网站仍是唯一棋局/挑战状态源；网站扩展挑战审批状态、API、SSE 事件。AstrBot 插件只做自然语言入口、主人通知/确认、调用网站 API、对局结束汇报，不维护独立棋局真相。先实现 MVP：主人确认 + 命令/工具化调用 + 赛后汇报；后续再做“让 Bot 自己决定”。

**Tech Stack:** FastAPI + SQLite + SSE (`/opt/chess-arena/server/app/main.py`), AstrBot plugin (`/opt/astrbot1/data/plugins/astrbot_plugin_chess_arena/` and `/opt/astrbot2/...`), pytest, Python 3.11/3.12, systemd services `chess-arena`, `astrbot1`, `astrbot2`.

---

## Non-negotiable rules

1. **先 git push 当前状态到 GitHub**，再让 CC/子代理改代码，避免老内容丢失。
2. **不要删除/覆盖已有文件**；只做增量修改。需要复制到 bot2 时用 rsync/patch，保留现有 runtime config/token。
3. **后端挑战状态必须向后兼容**：旧的 `pending` challenge 仍能被 accept/reject；新增 `owner_review` 也能被 accept/reject。
4. **忙碌检查不能绕过**：accept/owner_decision 创建 Match 前必须调用现有 `create_match_from_challenge()`，保留 `409 bot_busy` 行为。
5. **插件不直接猜 bot_id**：自然语言只解析对手名字/关键词，实际 Bot 选择必须通过 `/api/bots` 搜索/过滤。
6. **主人通知不要群里乱刷**：MVP 先回复到插件收到命令/事件时配置的 owner target；如果无法可靠主动私聊，就至少实现可配置 `owner_notify_targets`，并把 API/本地逻辑做好。
7. **安全**：发布前 grep，不能提交 token、公网 IP fallback、密码、NewAPI key。

---

## Shared API/Event Contract

### Bot fields
Add to `Bot` dataclass/database/API:

```python
challenge_policy: str = "auto_accept"  # auto_accept | manual_approve | reject_all
owner_review_timeout_sec: int = 180
```

Rationale: keep existing behavior by default (`auto_accept`) so third-party installs are not surprised. Our live bots can switch runtime/website profile to `manual_approve` after plugin support lands.

### Challenge fields
Add to `Challenge` dataclass/database/API:

```python
owner_decision: str | None = None       # accept | reject | None
owner_decision_reason: str | None = None
expires_at: float | None = None
updated_at: float = field(default_factory=time.time)
```

Statuses:

```text
pending        ordinary pending challenge, can be accepted by challenged bot
owner_review   waiting for owner confirmation, can be decided by owner API
accepted       match created
rejected       rejected by bot/owner/policy
expired        timed out before decision
cancelled      reserved for future challenger cancel
```

### Backend endpoints

#### `GET /api/bots/me/challenges/pending`
Auth: current bot token.
Returns challenges where current bot is opponent and status in `pending|owner_review`, plus names and optional expiry.

Expected response:

```json
{
  "challenges": [
    {
      "challenge_id": "ch_xxx",
      "challenger_bot_id": "bot_a",
      "challenger_name": "发作FAZHO",
      "opponent_bot_id": "bot_b",
      "opponent_name": "咕噜GULU",
      "challenger_side": "red",
      "status": "owner_review",
      "match_id": null,
      "created_at": 123,
      "updated_at": 124,
      "expires_at": 303
    }
  ]
}
```

#### `POST /api/challenges/{challenge_id}/owner_decision`
Auth: challenged bot token.
Body:

```json
{"decision":"accept", "reason":"主人同意"}
```

or:

```json
{"decision":"reject", "reason":"主人拒绝"}
```

Behavior:
- only opponent bot can decide;
- status must be `owner_review` or `pending` for compatibility;
- `accept` creates match through `create_match_from_challenge()`;
- `reject` sets status `rejected`;
- response includes challenge and match when accepted:

```json
{"challenge": {...}, "match": {...}, "match_url": "https://gulu624.icu/matches/match_xxx"}
```

#### Existing endpoints adjusted
- `POST /api/challenges`: after saving challenge:
  - opponent `challenge_policy=auto_accept`: current behavior may remain pending + event, or backend may auto-create match. For MVP prefer **do not backend auto-accept**; plugin can auto-accept if configured. But if existing website code currently expects plugin auto-accept, keep creation path driven by plugin.
  - `manual_approve`: set `status="owner_review"`, set `expires_at=now + owner_review_timeout_sec`, emit `challenge_received` with `requires_owner_decision=true`.
  - `reject_all`: set `status="rejected"`, emit `challenge_rejected` to challenger, return rejected challenge.
- `POST /api/challenges/{id}/accept`: allow status `pending` and `owner_review` to avoid breaking existing plugin while transition.
- `POST /api/challenges/{id}/reject`: allow status `pending` and `owner_review`.

### SSE events
Emit to bot SSE subscribers:

```text
challenge_received
challenge_accepted
challenge_rejected
match_started
match_finished
```

`challenge_received` payload must include:

```json
{
  "type":"challenge_received",
  "challenge_id":"ch_xxx",
  "challenger_bot_id":"bot_a",
  "challenger_name":"发作FAZHO",
  "opponent_bot_id":"bot_b",
  "opponent_name":"咕噜GULU",
  "challenger_side":"red",
  "status":"owner_review",
  "requires_owner_decision": true,
  "expires_at": 1234567890
}
```

`match_finished` should be emitted from every match-finish path eventually. MVP: add helper `emit_match_finished(m)` and call in the main move/checkmate/draw/stop paths if feasible; if too risky, plugin can provide pull-based `棋擂台最近` first and final match-finished event becomes second patch.

### Plugin command/tool contract

Commands to add/upgrade:

```text
棋擂台挑战 <对手名字或bot_id> [红|黑|随机]
棋擂台找对手 [强一点|弱一点|随机|在线]
棋擂台当前
棋擂台最近
棋擂台同意 [challenge_id可选]
棋擂台拒绝 [challenge_id可选]
```

Natural-language aliases can be added as regex handlers if AstrBot command tooling supports it, but MVP should at least support these stable commands. Later can register provider tools with AstrBot’s tool API if available.

Config additions:

```json
"challenge_decision_mode": "auto_accept|owner_approve|ignore",
"owner_notify_enabled": true,
"owner_notify_targets": "",
"owner_decision_timeout_sec": 180,
"match_report_enabled": true
```

**Owner target rule (user explicitly requested):** plugin config must make it clear whose chat receives approval requests. Add `owner_notify_targets` to `_conf_schema.json` with a practical hint such as: `挑战审批消息发给谁；填写 QQ号/微信ID/平台会话ID，多个用英文逗号分隔。留空则只在当前会话命令里显示待确认，不主动私聊。` The plugin should parse this as a list and include the current Bot name/id in every approval message so the owner knows which Bot is being challenged. If AstrBot proactive send API cannot send to a target, log the exact target and keep the challenge available via `棋擂台待确认`.

Backward compatibility:
- Existing `auto_accept_challenges=true` maps to `challenge_decision_mode="auto_accept"`.
- Existing `auto_accept_challenges=false` maps to `challenge_decision_mode="ignore"` unless user config explicitly sets owner approval.

---

## Parallel work assignment

## CC — hardest part: backend state/API/SSE migration

**Owner:** Claude Code on A server, model already configured to `gpt-5.5` via `http://154.201.73.203:8317`.

**Working directory:** `/opt/chess-arena`

**Files:**
- Modify: `server/app/main.py`
- Modify/add tests under: `server/tests/`
- Avoid frontend UI unless necessary.

**Task:** Implement the Shared API/Event Contract backend parts.

**Steps for CC:**
1. Inspect `server/app/main.py` existing `Bot`, `Challenge`, DB schema, `save_*`, `load_state_from_db`, challenge endpoints, emit helpers.
2. Add bot/challenge fields with SQLite migration using the project’s existing `ensure_columns` pattern.
3. Update `save_bot`, `load_state_from_db`, `bot_public`, `save_challenge`, `challenge_public`.
4. Add request model `OwnerDecisionReq`.
5. Add helper functions:
   - `challenge_is_actionable(ch)`
   - `challenge_payload(ch)` if needed
   - `accept_challenge_locked(ch)` to DRY accept and owner_decision while preserving `create_match_from_challenge()` busy checks.
6. Add endpoint `GET /api/bots/me/challenges/pending`.
7. Add endpoint `POST /api/challenges/{challenge_id}/owner_decision`.
8. Update create/accept/reject challenge endpoints to support `owner_review` and policy behavior.
9. Emit enriched `challenge_received` payload and `challenge_accepted/rejected` events.
10. Add/adjust tests:
    - manual policy creates `owner_review`, no match yet;
    - pending endpoint returns owner-review challenge with names;
    - owner accept creates match and emits/returns match;
    - owner reject sets rejected and no match;
    - non-opponent gets 403;
    - stale owner_review with busy bot returns 409 bot_busy;
    - old pending challenge can still be accepted.
11. Run:
    ```bash
    cd /opt/chess-arena
    python3 -m py_compile server/app/main.py server/app/engine.py
    PYTHONPATH=/opt/chess-arena/server pytest -q
    ```

**CC prompt file:** `/tmp/cc-chess-owner-approval-backend.txt`

**CC run command:**

```bash
cd /opt/chess-arena
claude -p "$(cat /tmp/cc-chess-owner-approval-backend.txt)" \
  --permission-mode acceptEdits \
  --allowedTools "Read,Edit,Write,Bash" \
  --max-turns 35 \
  --output-format json | tee /tmp/cc-chess-owner-approval-backend.json
```

**Success criteria:** pytest passes; backend supports owner_review flow without breaking existing challenge accept/reject.

---

## Subagent 1 — plugin owner approval + notifications

**Working directories:**
- Primary: `/opt/astrbot1/data/plugins/astrbot_plugin_chess_arena/`
- Mirror after review: `/opt/astrbot2/data/plugins/astrbot_plugin_chess_arena/`

**Files:**
- Modify: `main.py`
- Modify: `_conf_schema.json`

**Task:** Change plugin challenge handling from only auto-accept/ignore to `auto_accept|owner_approve|ignore`, keep existing config compatible, and add owner decision commands.

**Implementation notes:**
1. In `__init__`, add:
   - `self.challenge_decision_mode`
   - `self.owner_notify_enabled`
   - `self.owner_notify_targets`
   - `self.owner_decision_timeout_sec`
   - `self.pending_owner_challenges: dict[str, dict[str, Any]] = {}`
2. Compatibility mapping:
   - if config has `challenge_decision_mode`, use it;
   - else if `auto_accept_challenges` true -> `auto_accept`;
   - else -> `ignore`.
3. Update `_handle_challenge_received`:
   - `auto_accept`: current POST `/accept` behavior, but parse JSON and log/report match link.
   - `ignore`: log ignored.
   - `owner_approve`: store challenge payload in `pending_owner_challenges`, send owner notification if possible, otherwise log clearly and make `棋擂台待确认` command useful.
4. Add commands:
   - `棋擂台待确认`: list pending owner challenges.
   - `棋擂台同意 [challenge_id]`: choose given id or latest pending; POST `/api/challenges/{id}/owner_decision` with `{decision:"accept"}`; reply match URL if returned.
   - `棋擂台拒绝 [challenge_id]`: POST owner_decision reject.
5. Owner notification delivery:
   - Investigate AstrBot’s context/event send API in existing plugins or docs if needed.
   - If proactive DM is nontrivial, implement a safe internal method `_notify_owner(text)` that tries available context send APIs and logs fallback; do not break startup if sending fails.
6. Update `_conf_schema.json` with new fields, keep `auto_accept_challenges` but mark as legacy/backward-compatible or leave it hidden only if safe.
7. Update `棋擂台状态` output to show challenge mode and pending owner challenge count.

**Verification:**
```bash
python3 -m py_compile /opt/astrbot1/data/plugins/astrbot_plugin_chess_arena/main.py
python3 -m json.tool /opt/astrbot1/data/plugins/astrbot_plugin_chess_arena/_conf_schema.json >/dev/null
```

**Success criteria:** receiving `challenge_received` in owner_approve mode does not auto-accept; pending is stored; commands can accept/reject via owner_decision API.

---

## Subagent 2 — plugin natural-language chess commands/tools

**Working directory:** `/opt/astrbot1/data/plugins/astrbot_plugin_chess_arena/`

**Files:**
- Modify: `main.py`
- Maybe update README snippets if time.

**Task:** Upgrade “棋擂台挑战” from raw bot_id to human-friendly opponent name search, and add commands for finding opponent/current match/recent report.

**Implementation notes:**
1. Add helper `_api_json(method, path, ...) -> tuple[base,status,data,text]` wrapping `_request_text_with_fallback` JSON parsing.
2. Add helper `_find_bot(query)`:
   - fetch `/api/bots?q=<query>` if backend supports q, else `/api/bots` and filter locally;
   - exact match by `bot_id`, then exact name, then substring name;
   - exclude self where needed;
   - prefer online/enabled/public;
   - return clear ambiguity message if multiple good matches.
3. Upgrade command:
   ```text
   棋擂台挑战 <名字或bot_id> [红|黑|随机]
   ```
   - parse side words;
   - call `/api/challenges`;
   - if response status `owner_review`, say waiting for opponent owner; if accepted/match present, include match URL.
4. Add command:
   ```text
   棋擂台找对手 [强一点|弱一点|随机|在线]
   ```
   - fetch bots/rankings if available;
   - choose online non-self non-busy best candidate;
   - call challenge.
5. Add command:
   ```text
   棋擂台当前
   ```
   - fetch `/api/admin/matches?limit=20` or new public endpoint if available;
   - find active match involving self;
   - report players, side, ply, turn, URL.
6. Add command:
   ```text
   棋擂台最近
   ```
   - fetch recent matches involving self;
   - report last result, opponent, side, ply, finish reason, URL.
7. Keep replies short and Chinese.
8. Do not add brittle LLM parsing yet; command aliases are enough for MVP. If AstrBot supports regex/natural-language filters cleanly, add aliases like `去挑战(.+)` but do not risk plugin startup.

**Verification:**
```bash
python3 -m py_compile main.py
python3 - <<'PY'
# optional static import smoke if AstrBot deps are importable in env
PY
```

**Success criteria:** user can say `棋擂台挑战 发作`, `棋擂台找对手`, `棋擂台当前`, `棋擂台最近` and get useful API-backed replies.

---

## Subagent 3 — tests, docs, release hygiene, deployment checklist

**Working directories:**
- `/opt/chess-arena`
- `/opt/astrbot1/data/plugins/astrbot_plugin_chess_arena/`

**Files:**
- Add/modify backend tests: `server/tests/test_owner_approval_challenges.py` or existing tests.
- Update plugin README docs if repository copy available.
- Prepare release notes/checklist.

**Task:** Build integration tests/smoke scripts and docs around the new flow.

**Implementation notes:**
1. Write pytest tests that exercise backend owner approval flow via FastAPI TestClient.
2. Write a small smoke script under `/tmp` or `tools/` that can run against live local 8787:
   - create/register two test bots;
   - set opponent policy manual via PATCH/admin if endpoint supports it;
   - challenger creates challenge;
   - opponent pending endpoint sees it;
   - owner_decision accept creates match;
   - stop match/admin cleanup if needed.
3. Document plugin commands and config fields:
   - challenge_decision_mode;
   - owner notification;
   - commands list.
4. Prepare final verification commands:
   ```bash
   cd /opt/chess-arena
   python3 -m py_compile server/app/main.py server/app/engine.py
   PYTHONPATH=/opt/chess-arena/server pytest -q
   python3 -m py_compile /opt/astrbot1/data/plugins/astrbot_plugin_chess_arena/main.py
   python3 -m py_compile /opt/astrbot2/data/plugins/astrbot_plugin_chess_arena/main.py
   python3 -m json.tool /opt/astrbot1/data/plugins/astrbot_plugin_chess_arena/_conf_schema.json >/dev/null
   python3 -m json.tool /opt/astrbot2/data/plugins/astrbot_plugin_chess_arena/_conf_schema.json >/dev/null
   ```
5. Prepare leak grep commands for both website and plugin public files.
6. Suggest SemVer bumps:
   - chess-arena: minor if new API feature, e.g. next `v2.5.0` unless current tags differ.
   - astrbot_plugin_chess_arena: minor feature, e.g. next `v3.3.0` unless current tags differ.

**Success criteria:** controller has repeatable tests/smoke commands and concise release notes; no secret leak patterns in public files.

---

## Controller orchestration steps

### Step 0 — backup/git state

```bash
cd /opt/chess-arena
git status --short
git add -A && git commit -m "chore: checkpoint before owner-approved chess tools" || true
# Push through B server if direct GitHub unavailable, following chess-arena-platform skill.
```

Also back up plugin live dirs:

```bash
TS=$(date +%Y%m%d%H%M%S)
sudo mkdir -p /opt/chess-arena-backups/plugin-owner-tools-$TS
sudo rsync -a /opt/astrbot1/data/plugins/astrbot_plugin_chess_arena/ /opt/chess-arena-backups/plugin-owner-tools-$TS/astrbot1/
sudo rsync -a /opt/astrbot2/data/plugins/astrbot_plugin_chess_arena/ /opt/chess-arena-backups/plugin-owner-tools-$TS/astrbot2/
```

### Step 1 — launch CC and three subagents in parallel

- Start CC backend task in background with notify-on-complete.
- Dispatch Subagent 1/2/3 with their task text above and the Shared API/Event Contract.

### Step 2 — integrate

1. Wait for CC and subagents.
2. Read diffs from:
   - `/opt/chess-arena/server/app/main.py`
   - `/opt/chess-arena/server/tests/`
   - `/opt/astrbot1/data/plugins/astrbot_plugin_chess_arena/main.py`
   - `_conf_schema.json`
3. Resolve contract drift manually:
   - `challenge_id` vs `id`;
   - `challenge` wrapper vs flat response;
   - status strings;
   - match URL construction.
4. Mirror plugin changes to astrbot2 only after astrbot1 validates:
   ```bash
   sudo rsync -a --exclude='__pycache__/' /opt/astrbot1/data/plugins/astrbot_plugin_chess_arena/ /opt/astrbot2/data/plugins/astrbot_plugin_chess_arena/
   ```
   Do **not** copy runtime config files from astrbot1 to astrbot2.

### Step 3 — validation

```bash
cd /opt/chess-arena
python3 -m py_compile server/app/main.py server/app/engine.py
node --check server/app/static/arena.js
node --check server/app/static/match.js
PYTHONPATH=/opt/chess-arena/server pytest -q
python3 -m py_compile /opt/astrbot1/data/plugins/astrbot_plugin_chess_arena/main.py
python3 -m py_compile /opt/astrbot2/data/plugins/astrbot_plugin_chess_arena/main.py
python3 -m json.tool /opt/astrbot1/data/plugins/astrbot_plugin_chess_arena/_conf_schema.json >/dev/null
python3 -m json.tool /opt/astrbot2/data/plugins/astrbot_plugin_chess_arena/_conf_schema.json >/dev/null
```

Leak grep:

```bash
! grep -RInE 'sk-[A-Za-z0-9_-]{20,}|AKID[A-Za-z0-9]|141728SBZHOUXX|I87htcq|H92KKH|IK18v|http://[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+' \
  /opt/chess-arena/README.md /opt/chess-arena/docs /opt/chess-arena/server/app/static /opt/chess-arena/server/app/templates \
  /opt/astrbot1/data/plugins/astrbot_plugin_chess_arena/main.py /opt/astrbot1/data/plugins/astrbot_plugin_chess_arena/_conf_schema.json /opt/astrbot1/data/plugins/astrbot_plugin_chess_arena/README.md \
  /opt/astrbot2/data/plugins/astrbot_plugin_chess_arena/main.py /opt/astrbot2/data/plugins/astrbot_plugin_chess_arena/_conf_schema.json /opt/astrbot2/data/plugins/astrbot_plugin_chess_arena/README.md
```

### Step 4 — deploy live

Restart chess arena using force pattern if needed:

```bash
PID=$(systemctl show chess-arena -p MainPID --value); [ "$PID" != "0" ] && sudo kill -9 "$PID" || true
sudo systemctl reset-failed chess-arena
sudo systemctl start chess-arena
curl -fsS http://127.0.0.1:8787/health
```

Restart AstrBot:

```bash
sudo find /opt/astrbot1/data/plugins/astrbot_plugin_chess_arena /opt/astrbot2/data/plugins/astrbot_plugin_chess_arena -type d -name __pycache__ -prune -exec rm -rf {} +
sudo systemctl restart astrbot1 astrbot2
sleep 10
sudo journalctl -u astrbot1 -u astrbot2 --since '2 minutes ago' --no-pager | grep -Ei 'ChessArena|SSE|启动流程失败|Traceback|SyntaxError|ERROR|Exception' | tail -180
```

### Step 5 — live smoke

Minimum live API smoke:

1. Register two throwaway bots or use existing test bots.
2. Set one to manual challenge policy if endpoint/UI exists.
3. Create challenge.
4. Confirm pending endpoint sees it.
5. Accept through owner_decision.
6. Confirm match created and `your_turn` event moves continue.
7. Run plugin command smoke in chat if possible:
   - `棋擂台状态`
   - `棋擂台待确认`
   - `棋擂台挑战 发作`
   - `棋擂台当前`

### Step 6 — release

After validation:

- Commit website changes, tag new immutable version, push through B server if needed, create GitHub Release.
- Commit plugin changes to `zxx624/astrbot_plugin_chess_arena`, tag/release separately.
- Do not delete old releases/tags.

---

## Deferred v2 features

These are intentionally out of MVP unless time remains:

1. “让她自己决定” LLM accept/reject decision.
2. Fully proactive cross-platform DM to owner if AstrBot send API requires deeper adapter work.
3. Website settings UI for challenge policy; MVP can expose API/config first, then UI next patch.
4. Challenge cancel endpoint.
5. Rich match-finished tactical summary from棋谱.
