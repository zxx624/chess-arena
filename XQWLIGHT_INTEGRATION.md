# xqwlight 引擎集成方案

## 引擎文件
已下载到 /opt/chess-arena/engine/：
- position.js - 棋盘表示、走法生成、评估
- search.js - alpha-beta 搜索
- book.js - 开局库
- cchess.js - ICCS 坐标转换

## 坐标转换（关键）
xqwlight 内部用 16x16 棋盘，左上角=0x33。
- UCCI file a-i (0-8) → xqwlight file = 3 + file
- UCCI rank 0-9 → xqwlight rank = 12 - rank
- square = rank * 16 + file

UCCI "h0e2" → src: file=7,rank=0 → sq=(12-0)*16+(3+7)=0x7C; dst: file=4,rank=2 → sq=(12-2)*16+(3+4)=0xA7

ICCS 格式: "A0-A2" (file大写A-I, rank数字0-9, 红方视角rank0在下)
move2Iccs 在 cchess.js 中。需要 ICCS↔UCCI 互转函数。

## sdPlayer 注意
xqwlight 的 sdPlayer: 0=红方走, 1=黑方走
我们的 FEN: "r"=红方走, "b"=黑方走
fromFen 里: sdPlayer==0 时 fen 应该是 "w"(红), sdPlayer==1 时是 "b"(黑)
所以: xqwlight 的 0=红方 和我们的 "r"=红方 一致

## 搜索接口
```js
var pos = new Position();
pos.fromFen(fen);
var search = new Search(pos, 16); // 16=hash大小MB
var mv = search.searchMain(depth, 60000); // depth, 时间限制ms
// mv 是 xqwlight 内部 move 值，需要转 UCCI
```

## 实现步骤

### 1. 创建 Node.js 引擎服务 engine/server.js
- 加载 position.js, search.js, book.js, cchess.js
- 启动 HTTP 服务 监听 127.0.0.1:8789
- POST /analyze: {fen:"...", depth:3} → {best_move:"h0e2", score:123, depth:3}
- 内部做 UCCI↔xqwlight 坐标转换

### 2. systemd 服务 chess-engine
- ExecStart=/usr/bin/node /opt/chess-arena/engine/server.js
- Restart=always

### 3. main.py 添加 /api/analyze 端点
- 接收 {fen, depth}
- 调用 http://127.0.0.1:8789/analyze
- 返回引擎结果

### 4. 插件 _choose_move() 改造
- engine_mode="xqwlight" 时调 /api/analyze
- 失败回退随机

### 5. 棋盘 UI 改进
- 标准棋盘样式（楚河汉界、九宫格斜线、兵炮位标记）
- 被吃棋子显示在棋盘两侧
- 对局暂停/继续按钮
- 暗色模式适配

## 验证
1. node engine/server.js 能响应
2. curl POST /api/analyze 返回合法走法
3. Bot 走棋明显有章法
4. 棋盘样式标准
