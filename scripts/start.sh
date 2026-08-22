#!/usr/bin/env bash
# 面试官小P 启动脚本（macOS / Linux）——统一单服务（Vue3 前端 + REST + 语音，单端口）
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

echo "[1/4] 检查依赖..."
python3 -m pip install -e . --quiet

echo "[2/4] 构建前端（Vue3）..."
if [ ! -f frontend/dist/index.html ]; then
    if ! command -v node >/dev/null 2>&1; then
        echo "[错误] 未检测到 Node.js，且前端尚未构建。请先安装 Node.js 18+，"
        echo "       然后在 frontend 目录执行：npm install && npm run build"
        exit 1
    fi
    (cd frontend && npm install --silent && npm run build)
else
    echo "[提示] 前端已构建，跳过（如需重建请删除 frontend/dist）"
fi

PORT="${APP_PORT:-8765}"

echo "[3/4] 启动服务（文字版 + 语音通话 同一端口 :$PORT）..."
python3 -m uvicorn app.voice_server:app --host 127.0.0.1 --port "$PORT" &
SERVER_PID=$!
trap "kill $SERVER_PID 2>/dev/null" EXIT

echo "[4/4] 等待服务就绪..."
sleep 3
echo "文字版:   http://localhost:$PORT/"
echo "语音通话: http://localhost:$PORT/voice"
wait

