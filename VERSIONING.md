# GitHub 版本发布管理手册

## 版本号规则（语义化版本 SemVer）

格式：**v 主版本.次版本.补丁版本**

| 级别 | 格式 | 什么时候用 | 举例 |
|------|------|-----------|------|
| **补丁（Patch）** | v2.1.1 → v2.1.2 | 修 bug、小调整，不加新功能 | 修复暂停后不走棋 |
| **次版本（Minor）** | v2.1 → v2.2 | 加新功能，向后兼容 | 新增悔棋功能 |
| **主版本（Major）** | v2.0 → v3.0 | 大改、破坏性变更 | 重写后端、换数据库 |

## 核心原则

1. **Release 只进不删** — 每次发布都新建 Release，绝不删除/覆盖老版本
2. **Tag 不可变** — 一旦打 tag 推到 GitHub，就不要 force 覆盖它
3. **每次代码改动都对应一个版本** — 不要攒一堆改完才发一个 Release

## 操作流程

### 1. 提交代码

```bash
cd /项目目录
git add -A
git commit -m "fix: 描述改了什么"
```

### 2. 打 Tag

```bash
# 补丁版本（小修）
git tag -a v2.1.1 -m "v2.1.1: 修复xxx"

# 次版本（新功能）
git tag -a v2.2 -m "v2.2: 新增xxx功能"

# 主版本（大改动）
git tag -a v3.0 -m "v3.0: 重写xxx"
```

### 3. 推送到 GitHub

```bash
# 推代码
git push origin main

# 推新 tag（只推新的，不要 --force 覆盖老 tag）
git push origin v2.1.1
```

### 4. 创建 Release

在 GitHub 网页上：
1. 进入仓库 → Releases → Draft a new release
2. 选择刚推的 tag（如 v2.1.1）
3. 填写标题：`v2.1.1 - 修复暂停恢复`
4. 填写说明：列出改了什么
5. 点 Publish release

或用 API：
```bash
curl -X POST "https://api.github.com/repos/用户名/仓库名/releases" \
  -H "Authorization: token YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "tag_name": "v2.1.1",
    "name": "v2.1.1 - 修复暂停恢复",
    "body": "Bug fix\n- 修复xxx问题",
    "draft": false
  }'
```

## 当前 Chess Arena 版本历史

| 版本 | 说明 |
|------|------|
| v0.2 | 平台基础版（FastAPI + 前端 + Bot 认证） |
| v2.0 | 胜率统计 + 自动匹配 |
| v2.1 | xqwlight 引擎 + 暂停/吃子显示 + 标准棋盘 |
| v2.1.1 | 修复：暂停后继续不走棋 |

## 常见错误（不要做）

- ❌ `git tag -f v2.1` 覆盖已有 tag
- ❌ 删除旧 Release 再重建
- ❌ 改了一堆东西只发一个版本
- ❌ 所有改动都叫 v2.1（没有递增版本号）
- ❌ `git push --tags -f` 强推所有 tag

## 什么时候用什么版本号

```
当前 v2.1，接下来：
  修了个 bug        → v2.1.1（补丁）
  又修了个 bug      → v2.1.2（补丁）
  加了新功能        → v2.2（次版本）
  又加了新功能      → v2.3（次版本）
  重写了整个后端    → v3.0（主版本）
```
