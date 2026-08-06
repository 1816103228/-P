#!/usr/bin/env bash
# 面试官小P 启动脚本（macOS / Linux）
set -e
# 切换到项目根目录（本脚本位于 scripts/ 下）
cd "$(dirname "$0")/.."

echo "============================================"
echo "  面试官小P - Python/后端面试 Agent"
echo "============================================"
echo

if ! command -v python3 >/dev/null 2>&1; then
    echo "[错误] 未找到 python3，请先安装 Python 3.10+"
    exit 1
fi

echo "[1/3] 检查依赖..."
python3 -m pip install -e . --quiet

echo "[2/3] 启动语音通话服务 (8765)..."
python3 -m uvicorn app.voice_server:app --host 127.0.0.1 --port 8765 &
VOICE_PID=$!
trap "kill $VOICE_PID 2>/dev/null" EXIT

echo "[3/3] 启动 Web 服务 (8501)..."
python3 -m streamlit run app/ui/web.py --server.headless true --server.port 8501
