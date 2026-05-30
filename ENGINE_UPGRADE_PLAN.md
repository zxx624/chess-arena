# 象棋引擎优化方案

## 现状问题

当前 AstrBot 插件 (`/opt/astrbot2/data/plugins/astrbot_plugin_chess_arena/main.py`) 的 `_choose_move()` 方法（第324行）只是从 `legal_moves` 中随机选择，导致 Bot 下棋完全无章法。

服务端 `engine.py` 已有完整的规则引擎：`legal_moves()`、`apply_ucci()`、棋子走法规则等，但缺少**搜索和评估**能力。

## 目标

让 Bot 能下出有章法的棋，而不是随机乱走。

## 技术方案

### 第一步：在 engine.py 中添加评估+搜索

在 `/opt/chess-arena/server/app/engine.py` 末尾追加：

**1. 子力价值表**
```
K=10000, A=120, B=120, N=270, R=600, C=285, P=30(未过河)/70(过河)
```

**2. 位置分表（piece-square tables）**
为每种棋子定义 10×9 的位置分矩阵，鼓励：
- 车占开放列、骑河
- 马跳中腹、有蹩脚意识
- 炮有炮架
- 兵过河后价值提升
- 将/帅不出九宫

**3. 评估函数 `evaluate(board) -> int`**
- 正分=红方优势，负分=黑方优势
- 子力价值 + 位置分
- 被将死返回 ±99999

**4. Alpha-Beta 搜索 `best_move(fen, depth=3) -> str`**
- 带 alpha-beta 剪枝的 minimax
- 走法排序：吃子走法优先（MVV-LVA）
- 搜索深度默认3层（可配置）
- 返回 UCCI 格式走法字符串

### 第二步：添加 API 端点

在 `main.py` 添加：

```
POST /api/analyze
Body: { "fen": "...", "depth": 3 }
Auth: X-Bot-Token header
Response: { "best_move": "h0e2", "score": 120, "depth": 3, "nodes": 5000 }
```

### 第三步：修改 AstrBot 插件

修改 `_choose_move()` 方法：
- 有 `legal_moves` 时，调用 `POST /api/analyze` 获取引擎推荐走法
- 如果 API 调用失败或超时（2秒），回退到原来的随机选择
- 保留 `_make_comment()` 的 LLM 评棋功能

### 第四步：可配置难度

在 bot 注册时的 `engine_mode` 字段支持：
- `"random"`：原来的随机走法（保留兼容）
- `"easy"`：depth=1，偶尔随机
- `"medium"`：depth=2
- `"hard"`：depth=3（默认）
- `"expert"`：depth=4

在插件配置 `astrbot_plugin_chess_arena_config.json` 中添加 `engine_depth` 参数。

## 文件改动清单

1. `/opt/chess-arena/server/app/engine.py` — 追加 evaluate() + best_move()
2. `/opt/chess-arena/server/app/main.py` — 追加 POST /api/analyze 端点
3. `/opt/astrbot2/data/plugins/astrbot_plugin_chess_arena/main.py` — 修改 _choose_move()
4. `/opt/astrbot2/data/plugins/astrbot_plugin_chess_arena/config.json` — 添加 engine_depth 配置

## 验证

1. 单元测试：engine.py 的 evaluate() 和 best_move() 对已知局面返回合理走法
2. API 测试：curl POST /api/analyze 返回合法走法
3. 集成测试：Bot 对局中走法明显不再是随机的
4. 重启服务：`sudo systemctl restart chess-arena && sudo systemctl restart astrbot2`
