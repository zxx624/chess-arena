# Chess Arena Protocol v0.1

本文档定义“任意 Bot 象棋对战平台”MVP 的 HTTP + SSE 接入协议。目标是让后端、插件与外部 Bot 可以用同一套契约进行注册、挑战、对局与走子。

## 1. 基本约定

- Base URL 示例：`http://127.0.0.1:8787`
- 认证方式：`Authorization: Bearer <token>`
- SSE 连接：`GET /sse/bot?token=<token>`
- 数据格式：HTTP 请求/响应与 SSE `data` 均为 JSON。
- 走法格式 v0.1：仅支持 **UCCI**（例如 `h2e2`、`b0c2`）。
- 棋种：MVP 默认中国象棋（Xiangqi）。
- 时间字段：ISO-8601 UTC 字符串，例如 `2026-01-01T00:00:00Z`。
- ID：字符串，由后端生成。

## 2. 认证

### 2.1 HTTP API

所有受保护 API 必须携带：

```http
Authorization: Bearer <token>
```

认证失败：

```json
{
  "error": {
    "code": "unauthorized",
    "message": "invalid or missing token"
  }
}
```

### 2.2 SSE

SSE 使用 query token，便于简单客户端接入：

```http
GET /sse/bot?token=<token>
Accept: text/event-stream
```

服务端可同时支持 `Authorization` header，但 v0.1 只要求 query token。

## 3. 统一错误格式

```json
{
  "error": {
    "code": "string_machine_readable_code",
    "message": "human readable message",
    "details": {}
  }
}
```

常见 code：

- `bad_request`
- `unauthorized`
- `forbidden`
- `not_found`
- `conflict`
- `invalid_state`
- `invalid_move`
- `rate_limited`
- `internal_error`

## 4. Bot 模型

```json
{
  "id": "bot_123",
  "name": "random-bot",
  "status": "online",
  "capabilities": {
    "move_formats": ["ucci"],
    "variants": ["xiangqi"]
  },
  "created_at": "2026-01-01T00:00:00Z"
}
```

`status` 可选值：

- `offline`
- `online`
- `in_match`

## 5. Challenge 模型

```json
{
  "id": "chal_123",
  "challenger_bot_id": "bot_a",
  "opponent_bot_id": "bot_b",
  "status": "pending",
  "variant": "xiangqi",
  "initial_fen": null,
  "time_control": {
    "base_ms": 300000,
    "increment_ms": 0
  },
  "created_at": "2026-01-01T00:00:00Z"
}
```

`status` 可选值：

- `pending`
- `accepted`
- `rejected`
- `expired`
- `cancelled`

## 6. Match 模型

```json
{
  "id": "match_123",
  "challenge_id": "chal_123",
  "status": "playing",
  "variant": "xiangqi",
  "red_bot_id": "bot_a",
  "black_bot_id": "bot_b",
  "side_to_move": "red",
  "fen": "...",
  "legal_moves": ["h2e2", "b0c2"],
  "moves": [
    {
      "ply": 1,
      "bot_id": "bot_a",
      "side": "red",
      "move": "h2e2",
      "format": "ucci",
      "created_at": "2026-01-01T00:00:01Z"
    }
  ],
  "result": null,
  "created_at": "2026-01-01T00:00:00Z",
  "updated_at": "2026-01-01T00:00:01Z"
}
```

`match.status` 可选值：

- `created`
- `playing`
- `finished`
- `aborted`

`result` 示例：

```json
{
  "winner_bot_id": "bot_a",
  "reason": "checkmate",
  "detail": "black is checkmated"
}
```

`reason` 建议值：`checkmate`, `resign`, `timeout`, `draw`, `illegal_move`, `aborted`。

## 7. HTTP API

### 7.1 注册 Bot

```http
POST /api/bots/register
Content-Type: application/json
```

请求：

```json
{
  "name": "random-bot",
  "capabilities": {
    "move_formats": ["ucci"],
    "variants": ["xiangqi"]
  }
}
```

响应 `201 Created`：

```json
{
  "bot": {
    "id": "bot_123",
    "name": "random-bot",
    "status": "offline",
    "capabilities": {
      "move_formats": ["ucci"],
      "variants": ["xiangqi"]
    },
    "created_at": "2026-01-01T00:00:00Z"
  },
  "token": "secret-token"
}
```

### 7.2 获取当前 Bot

```http
GET /api/bots/me
Authorization: Bearer <token>
```

响应：

```json
{
  "bot": { "id": "bot_123", "name": "random-bot", "status": "online" }
}
```

### 7.3 获取 Bot 列表

```http
GET /api/bots
Authorization: Bearer <token>
```

响应：

```json
{
  "bots": [
    { "id": "bot_123", "name": "random-bot", "status": "online" }
  ]
}
```

### 7.4 发起挑战

```http
POST /api/challenges
Authorization: Bearer <token>
Content-Type: application/json
```

请求：

```json
{
  "opponent_bot_id": "bot_b",
  "variant": "xiangqi",
  "initial_fen": null,
  "time_control": {
    "base_ms": 300000,
    "increment_ms": 0
  }
}
```

响应 `201 Created`：

```json
{
  "challenge": {
    "id": "chal_123",
    "challenger_bot_id": "bot_a",
    "opponent_bot_id": "bot_b",
    "status": "pending"
  }
}
```

副作用：向被挑战方 SSE 推送 `challenge_received`。

### 7.5 接受挑战

```http
POST /api/challenges/{id}/accept
Authorization: Bearer <token>
```

响应：

```json
{
  "challenge": { "id": "chal_123", "status": "accepted" },
  "match": { "id": "match_123", "status": "playing" }
}
```

副作用：向双方 SSE 推送 `challenge_accepted` 与 `match_started`。

### 7.6 拒绝挑战

```http
POST /api/challenges/{id}/reject
Authorization: Bearer <token>
Content-Type: application/json
```

请求：

```json
{
  "reason": "busy"
}
```

响应：

```json
{
  "challenge": { "id": "chal_123", "status": "rejected" }
}
```

### 7.7 获取对局

```http
GET /api/matches/{id}
Authorization: Bearer <token>
```

响应：

```json
{
  "match": { "id": "match_123", "status": "playing", "legal_moves": ["h2e2"] }
}
```

### 7.8 提交走子

```http
POST /api/matches/{id}/move
Authorization: Bearer <token>
Content-Type: application/json
```

请求：

```json
{
  "move": "h2e2",
  "format": "ucci"
}
```

响应：

```json
{
  "match": { "id": "match_123", "status": "playing", "side_to_move": "black" },
  "move": {
    "ply": 1,
    "bot_id": "bot_a",
    "side": "red",
    "move": "h2e2",
    "format": "ucci"
  }
}
```

副作用：

- 向双方推送 `move_made`。
- 如果对局继续，向下一手 Bot 推送 `your_turn`。
- 如果对局结束，向双方推送 `match_finished`。

## 8. SSE 事件

SSE 格式：

```text
event: your_turn
data: {"type":"your_turn","match_id":"match_123","legal_moves":["h2e2"]}

```

每个事件的 `event:` 名称必须等于 JSON 中的 `type`。

### 8.1 connected

建立 SSE 后立即发送。

```json
{
  "type": "connected",
  "bot_id": "bot_123",
  "server_time": "2026-01-01T00:00:00Z"
}
```

### 8.2 challenge_received

```json
{
  "type": "challenge_received",
  "challenge": {
    "id": "chal_123",
    "challenger_bot_id": "bot_a",
    "opponent_bot_id": "bot_b",
    "variant": "xiangqi",
    "time_control": { "base_ms": 300000, "increment_ms": 0 }
  }
}
```

### 8.3 challenge_accepted

```json
{
  "type": "challenge_accepted",
  "challenge_id": "chal_123",
  "match_id": "match_123"
}
```

### 8.4 match_started

```json
{
  "type": "match_started",
  "match": {
    "id": "match_123",
    "red_bot_id": "bot_a",
    "black_bot_id": "bot_b",
    "side_to_move": "red",
    "fen": "..."
  }
}
```

### 8.5 your_turn

```json
{
  "type": "your_turn",
  "match_id": "match_123",
  "side": "red",
  "fen": "...",
  "legal_moves": ["h2e2", "b0c2"]
}
```

`legal_moves` 必须为 UCCI 字符串数组。Bot v0.1 应只从该数组选择走法。

### 8.6 move_made

```json
{
  "type": "move_made",
  "match_id": "match_123",
  "move": {
    "ply": 1,
    "bot_id": "bot_a",
    "side": "red",
    "move": "h2e2",
    "format": "ucci"
  },
  "fen": "...",
  "side_to_move": "black"
}
```

### 8.7 match_finished

```json
{
  "type": "match_finished",
  "match_id": "match_123",
  "result": {
    "winner_bot_id": "bot_a",
    "reason": "checkmate",
    "detail": "black is checkmated"
  },
  "final_fen": "..."
}
```

### 8.8 error

```json
{
  "type": "error",
  "code": "invalid_move",
  "message": "move is not legal",
  "details": {
    "match_id": "match_123",
    "move": "h2e2"
  }
}
```

## 9. 兼容性规则

- 客户端必须忽略未知字段。
- 服务端可以新增事件字段，但不应移除 v0.1 必填字段。
- 客户端遇到未知事件类型应记录日志并继续监听。
- UCCI 以外的走法格式应在未来版本通过 `capabilities.move_formats` 协商。
