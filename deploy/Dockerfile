# ---- 前端构建（Vue3 + Vite）----
FROM node:20-alpine AS frontend
WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci --silent || npm install --silent
COPY frontend/ ./
RUN npm run build

# ---- 后端运行时（统一 FastAPI 服务：Vue3 前端 + REST + 语音）----
FROM python:3.11-slim

WORKDIR /app

# 先复制依赖声明与源码，安装后再拷入其余文件（利用 Docker 层缓存）
COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install --no-cache-dir .

COPY . .

# 前端构建产物（已含静态资源）
COPY --from=frontend /frontend/dist ./frontend/dist

EXPOSE 8765

# 运行时通过环境变量注入 DEEPSEEK_API_KEY（参考 .env.example）
ENV DISABLE_SCHEDULER=0

CMD ["uvicorn", "app.voice_server:app", "--host", "0.0.0.0", "--port", "8765"]
