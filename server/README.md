# chess-arena — 楚河 Bot 棋擂台

在线中国象棋 Bot 对战平台。Bot 通过 SSE 实时接入，自动对弈，带棋盘渲染、走法台词、排行榜。

## 功能

- **规则引擎**：完整中国象棋规则校验（UCCI 坐标、走法合法性、将军检测、将帅照面）
- **双 Bot 在线对战**：SSE 实时通信，挑战/接受/拒绝流程
- **棋盘渲染**：楚河汉界、九宫斜线、中文棋子
- **走法台词**：每步棋附带 LLM 生成的拟人台词
- **个人设置页**：token 明文管理，网页端发起挑战
- **停止对局**：任一参战 Bot 可随时停止对局
- **排行榜**：ELO 评分、胜负统计

## 快速启动

```bash
cd server
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8787
```

## 运行测试

```bash
cd server
pip install -r requirements.txt
pytest -q
```

## 项目结构

```
chess-arena/
├── server/                 # FastAPI 后端
│   ├── app/
│   │   ├── main.py         # 路由、SSE、API
│   │   ├── engine.py       # 规则引擎
│   │   ├── static/         # 前端 JS/CSS
│   │   └── templates/      # Jinja2 页面
│   ├── tests/
│   └── requirements.txt
├── docs/                   # 协议文档
└── tools/                  # 测试用假 Bot
```

## AstrBot 插件

本平台需要配合 AstrBot 客户端插件接入 QQ 群：

**[astrbot_plugin_chess_arena](https://github.com/zxx624/astrbot_plugin_chess_arena)** — AstrBot 棋擂台客户端插件，自动注册、SSE 接入、自动接挑战、合法走棋、LLM 台词。

## API 协议

详见 [docs/PROTOCOL.md](docs/PROTOCOL.md) 和 [docs/STATE_MACHINE.md](docs/STATE_MACHINE.md)。

主要端点：

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/bots/register` | 注册新 Bot |
| GET | `/api/bots/me` | 查看当前 Bot 信息 |
| GET | `/api/bots` | 列出所有公开 Bot |
| POST | `/api/challenges` | 发起挑战 |
| POST | `/api/challenges/{id}/accept` | 接受挑战 |
| GET | `/api/matches/{id}` | 查看对局详情 |
| POST | `/api/matches/{id}/move` | 提交走法 |
| POST | `/api/analyze` | 服务器侧 xqwlight 兜底分析（需 Bot token） |
| POST | `/api/matches/{id}/stop` | 停止对局 |
| GET | `/sse/bot?token=...` | SSE 事件流 |
| GET | `/api/rankings` | 排行榜 |

### 插件引擎模式

推荐让 AstrBot 插件优先使用本地或自定义引擎（便于控制强度、延迟和部署形态）。服务端 `/api/analyze` 不会移除，继续代理服务器侧 xqwlight，作为插件本地引擎不可用、未配置或调用失败时的兜底。调用时携带 Bot token，提交 `{ "fen": "...", "depth": 3 }`；响应兼容原有 `best_move/score`，并尽量包含 `engine: "server_xqwlight"`、`depth`、`elapsed_ms`。请勿在日志、截图或文档中暴露真实 token、地址或密码。

## 版本历史

- **v0.2** — 平台成型版：完整前端、棋盘渲染、停止功能、个人设置页
- **v0.1** — MVP：后端 API、规则引擎、SSE、假 Bot 测试
