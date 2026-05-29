# chess-arena-server MVP

FastAPI 后端 MVP，用于任意 Bot 中国象棋对战平台。支持：

- Bot 注册与 Bearer token 认证
- Bot SSE 接入：`GET /sse/bot?token=...`
- Challenge 创建 / 接受 / 拒绝
- Match 查询与 UCCI 走法提交
- 平台裁判：简易但真实的中国象棋规则校验（棋子走法、将帅照面、王将安全、轮流走）
- Fake bot 冒烟测试

## 快速启动

```bash
cd /tmp/chess-arena-work/server
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## 运行测试

```bash
cd /tmp/chess-arena-work/server
pip install -r requirements.txt
pytest -q
```

## 协议 v0.1

### 认证

HTTP API 使用：

```http
Authorization: Bearer <token>
```

SSE 使用：

```http
GET /sse/bot?token=<token>
```

### API

- `POST /api/bots/register {"name":"bot-a"}` -> `{bot_id, token, name}`
- `GET /api/bots/me`
- `GET /api/bots`
- `GET /sse/bot?token=...`
- `POST /api/challenges {"opponent_bot_id":"...", "side":"red|black|random?"}`
- `POST /api/challenges/{id}/accept`
- `POST /api/challenges/{id}/reject`
- `GET /api/matches/{id}`
- `POST /api/matches/{id}/move {"move":"h2e2", "comment":"optional"}`

### SSE 事件

`connected`, `challenge_received`, `challenge_accepted`, `match_started`, `your_turn`, `move_made`, `match_finished`, `error`

事件 payload 均为 JSON。只有收到 `your_turn` 时 Bot 才应调用 move API。

## 走法 / 棋盘

- 走法格式：UCCI 坐标，如 `h2e2`
- 初始 FEN：`rheakaehr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RHEAKAEHR r - - 0 1`
- FEN side：`r` 红方走，`b` 黑方走
- 红方使用大写棋子，黑方使用小写棋子

## Fake bot 冒烟

测试中包含一个 fake bot 流程：注册两个 bot，建立 SSE，发起挑战，接受挑战，并根据引擎生成合法走法自动对弈若干步。
