@echo off
setlocal enabledelayedexpansion
cd /d %~dp0

echo ============================================
echo   面试官小P - Python/后端面试 Agent
echo ============================================
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 python，请先安装 Python 3.10+ 并勾选 "Add to PATH"
    pause
    exit /b 1
)

echo [1/6] 检查依赖...
set "PIP_OK="
for %%i in (1 2 3) do (
    if not defined PIP_OK (
        echo   第 %%i 次尝试安装依赖...
        python -m pip install -r requirements.txt --quiet --disable-pip-version-check
        if not errorlevel 1 set "PIP_OK=1"
    )
)
if not defined PIP_OK (
    echo   当前镜像源不可用，改用官方 PyPI 重试...
    python -m pip install -r requirements.txt --quiet --disable-pip-version-check --index-url https://pypi.org/simple
    if errorlevel 1 (
        echo [错误] 依赖安装失败，请检查网络后重试
        pause
        exit /b 1
    )
)
echo [OK] 依赖就绪

echo [2/6] 检测端口...

netstat -ano | findstr ":8501 " >nul 2>&1
set "PORT=8501"
if not errorlevel 1 (
    echo [提示] 8501 端口已被占用，改用 8502
    set "PORT=8502"
)

echo [3/6] 启动语音通话服务...
start "MianShiGuanXiaoP-Voice" python -m uvicorn voice_server:app --host 127.0.0.1 --port 8765

echo [4/6] 启动 Web 服务...
start "MianShiGuanXiaoP-Server" python -m streamlit run main.py --server.headless true --server.port !PORT!

echo [5/6] 等待服务启动...
timeout /t 5 /nobreak >nul

echo [6/6] 检测语音服务...
powershell -NoProfile -Command "try{$r=Invoke-WebRequest -Uri 'http://127.0.0.1:8765/health' -UseBasicParsing -TimeoutSec 3; if($r.StatusCode -eq 200){exit 0}else{exit 1}}catch{exit 1}"
if errorlevel 1 (
    echo [警告] 语音服务未就绪（8765 端口），网页仍可正常使用
    echo        请查看「MianShiGuanXiaoP-Voice」窗口中的报错
) else (
    echo [OK] 语音服务已就绪
)

echo 正在打开浏览器: http://localhost:!PORT!
start "" "http://localhost:!PORT!"

echo.
echo ============================================
echo   浏览器已打开
echo   地址: http://localhost:!PORT!
echo   若未弹出请手动复制地址访问
echo   停止: 关闭「MianShiGuanXiaoP-Server」与「MianShiGuanXiaoP-Voice」窗口
echo ============================================
pause