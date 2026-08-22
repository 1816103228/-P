# 面试官小P - 开发约定

基于 DeepSeek 的 Python/后端面试模拟与辅导 Agent：Vue3 前端（多用户账号）+
FastAPI 统一后端（REST + SSE + 语音 WebSocket）+ 定时爬取题库（SQLite）。

## 常用命令

```bash
# 安装（含开发依赖：pytest / httpx / ruff）
pip install -e ".[dev]"

# 测试
python -m pytest

# 静态检查与格式化
ruff check .
ruff format .

# 启动统一服务（Vue3 前端 + REST + 语音，单端口 8765）
python -m uvicorn app.voice_server:app --host 127.0.0.1 --port 8765

# 前端（Vue3 + Vite）——启动前需构建，产物供后端托管
cd frontend
npm install
npm run dev        # 开发模式（5173，代理 /api /ws 到 8765）
npm run build      # 生产构建 → frontend/dist
cd ..

# 手动抓取题库
python -m app.crawler.run
```

## 目录约定

- `app/`：应用包。
  - `voice_server.py`：**统一 FastAPI 服务**——挂载 REST 路由、语音 WebSocket（按
    用户认证）、托管 `frontend/dist`（SPA history 回退）。
  - `routers/`：REST 路由（`auth` 认证 / `session` 会话与 SSE 聊天 / `questions`
    题库与收藏 / `custom` 定制面试）。
  - `auth.py`（pbkdf2 + 令牌）、`session_store.py`（会话持久化）、
    `voice_store.py`（按用户定制面试）为多用户数据层。
  - `agent/`：会话状态机（`coach.py` 支持 to_dict/from_dict 序列化）与 LLM 封装；
    `crawler/`：数据源适配器；`ui/`：仅保留静态资源 `assets/`（头像）。
- `frontend/`：Vue3 前端工程（Vite + Element Plus）。`src/composables/voice/`
  是语音通话引擎（从旧 `voice_page.html` 移植）。构建产物 `frontend/dist` 由后端托管，
  **修改前端后需 `npm run build` 并重启/刷新**。
- `scripts/`：启动脚本（`start.bat` Windows / `start.sh` macOS·Linux），自动构建前端。
- `deploy/`：容器部署（多阶段 `Dockerfile` + `docker-compose.yml`）。
- `tests/`：根级测试目录（pytest 默认 `testpaths`），通过
  `[tool.pytest.ini_options] pythonpath = ["."]` 导入 `app.*` 与根级模块。
- `data/`：运行时数据（SQLite、日志、锁），已 gitignore，不要提交。

## 约定与注意事项

- 代码注释、文档使用中文；源码 UTF-8 无 BOM、LF 换行（`.editorconfig` 已声明）。
- `scripts/start.bat` 为 **UTF-8 + CRLF** 编码（`chcp 65001`），修改时保持该编码与换行。
- 依赖只在 `pyproject.toml` 中声明（`requirements.txt` 已移除），新增依赖同步
  更新 `[project.dependencies]` 与 `[project.optional-dependencies].dev`。
- 前端依赖声明在 `frontend/package.json`；国内网络慢时 `frontend/.npmrc` 已指向
  npmmirror 镜像。
- 测试必须可离线运行：LLM、网络、edge-tts 全部 mock；不要依赖真实 API Key。
- 运行时通过环境变量注入密钥（`.env` 不入库），参见 `.env.example`。
- 数据库迁移通过 `PRAGMA user_version`（当前版本 7，含多用户表）；改 schema 需在
  `app/db.py` 的 `_migrate` 追加迁移步骤并升级版本号。
- 登录令牌经 `Authorization: Bearer <token>`（REST）与 `?token=`（WebSocket）传递；
  新增受保护接口用 `auth.CurrentUser` 依赖解析当前用户。

