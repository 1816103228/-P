FROM python:3.11-slim

WORKDIR /app

# 先复制依赖声明与源码，安装后再拷入其余文件（利用 Docker 层缓存）
COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install --no-cache-dir .

COPY . .

EXPOSE 8501

# 运行时通过环境变量注入 DEEPSEEK_API_KEY（参考 .env.example）
ENV DISABLE_SCHEDULER=0

CMD ["streamlit", "run", "app/ui/web.py", "--server.headless=true", "--server.address=0.0.0.0", "--server.port=8501"]
