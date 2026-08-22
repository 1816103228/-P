@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d %~dp0..

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

echo [1/6] 安装依赖...
set "PIP_OK="
for %%i in (1 2 3) do (
    if not defined PIP_OK (
        echo   第 %%i 次尝试安装依赖...
        python -m pip install -e . --quiet --disable-pip-version-check
        if not errorlevel 1 set "PIP_OK=1"
    )
)
if not defined PIP_OK (
    echo   当前镜像源不可用，改用官方 PyPI 安装...
    python -m pip install -e . --quiet --disable-pip-version-check --index-url https://pypi.org/simple
    if errorlevel 1 (
        echo [错误] 依赖安装失败，请检查网络连接
        pause
        exit /b 1
    )
)
echo [OK] 依赖安装完成

echo [2/6] 构建前端（Vue3）...
if not exist "frontend\dist\index.html" (
    where node >nul 2>&1
    if errorlevel 1 (
        echo [错误] 未检测到 Node.js，且前端尚未构建。
        echo        请先安装 Node.js 18+，然后在 frontend 目录执行：
        echo        npm install ^&^& npm run build
        pause
        exit /b 1
    )
    pushd frontend
    call npm install --silent
    if errorlevel 1 (
        echo [警告] npm install 失败，改用国内镜像重试...
        call npm install --silent --registry=https://registry.npmmirror.com
    )
    call npm run build
    if errorlevel 1 (
        echo [错误] 前端构建失败，请查看 frontend 目录报错
        popd
        pause
        exit /b 1
    )
    popd
) else (
    echo [提示] 前端已构建，跳过（如需重建请删除 frontend\dist）
)

echo [3/6] 检测端口...
netstat -ano | findstr ":8765 " >nul 2>&1
set "PORT=8765"
if not errorlevel 1 (
    echo [提示] 8765 端口已被占用，改用 8766
    set "PORT=8766"
)

echo [4/6] 启动统一服务（文字版 + 语音通话 同一端口）...
start "MianShiGuanXiaoP-Server" cmd /c "python -m uvicorn app.voice_server:app --host 127.0.0.1 --port !PORT!"

echo [5/6] 等待服务就绪...
timeout /t 5 /nobreak >nul

echo [6/6] 检查服务...
set "APP_PORT=!PORT!"
powershell -NoProfile -Command "try{$r=Invoke-WebRequest -Uri ('http://127.0.0.1:'+$env:APP_PORT+'/health') -UseBasicParsing -TimeoutSec 3; if($r.StatusCode -eq 200){exit 0}else{exit 1}}catch{exit 1}"
if errorlevel 1 (
    echo [警告] 服务可能未启动成功，请查看「MianShiGuanXiaoP-Server」窗口中的报错
) else (
    echo [OK] 服务已就绪
)

echo 正在打开浏览器: http://localhost:!PORT!
start "" "http://localhost:!PORT!"

echo.
echo ============================================
echo   服务已全部启动
echo   文字版:   http://localhost:!PORT!/
echo   语音通话: http://localhost:!PORT!/voice
echo   停止: 关闭「MianShiGuanXiaoP-Server」窗口
echo ============================================
pause
