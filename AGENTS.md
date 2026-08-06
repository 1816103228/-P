# 面试官小P - 开发约定

基于 DeepSeek 的 Python/后端面试模拟与辅导 Agent：Streamlit 文字版 +
FastAPI/WebSocket 语音通话页 + 定时爬取题库（SQLite）。

## 常用命令

```bash
# 安装（含开发依赖：pytest / httpx / ruff）
pip install -e ".[dev]"

# 测试
python -m pytest

# 静态检查与格式化
ruff check .
ruff format .

# 启动 Web 文字版
streamlit run app/ui/web.py

# 启动语音通话服务（8765）
python -m uvicorn app.voice_server:app --host 127.0.0.1 --port 8765

# 手动抓取题库
python -m app.crawler.run
```

## 目录约定

- `app/`：应用包。`voice_server.py` 是 FastAPI 语音服务；`agent/` 会话状态机与
  LLM 封装；`crawler/` 数据源适配器；`ui/` 共享组件、`voice_page.html` 与
  Streamlit Web 入口（`ui/web.py`，语音页入口按钮在右下角）。
- `scripts/`：启动脚本（`start.bat` Windows / `start.sh` macOS·Linux）。
- `deploy/`：容器部署（`Dockerfile` + `docker-compose.yml`）。
- `tests/`：根级测试目录（pytest 默认 `testpaths`），通过
  `[tool.pytest.ini_options] pythonpath = ["."]` 导入 `app.*` 与根级模块。
- `data/`：运行时数据（SQLite、日志、锁），已 gitignore，不要提交。

## 约定与注意事项

- 代码注释、文档使用中文；源码 UTF-8 无 BOM、LF 换行（`.editorconfig` 已声明）。
- `scripts/start.bat` 为 GBK/CRLF 编码，修改时用对应编码读写，勿整体转 UTF-8。
- 依赖只在 `pyproject.toml` 中声明（`requirements.txt` 已移除），新增依赖同步
  更新 `[project.dependencies]` 与 `[project.optional-dependencies].dev`。
- 测试必须可离线运行：LLM、网络、edge-tts 全部 mock；不要依赖真实 API Key。
- 运行时通过环境变量注入密钥（`.env` 不入库），参见 `.env.example`。
- 语音页 HTML 由 `app/voice_server.py` 实时读取渲染，改动无需重启服务。
